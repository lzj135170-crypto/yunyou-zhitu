"""专家节点的公共骨架。

六个专家和收尾的 itinerary 流程一致：读历史 → 调模型 → 结构化输出 → 标 done → 回 planner。
任何成功或失败路径都必须把专家名写进 done，否则 planner 会反复调度同一节点，
直到 recursion_limit 才报错；失败原因写进 findings，让下游知道这块没有产出。
"""

import logging
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel

from app.agents.grounding import sanitize_expert
from app.agents.llm import invoke_structured
from app.agents.nodes._poi import PoiRequest, fetch_poi
from app.agents.state import TravelState

# invoke_structured 放在 app/agents/llm.py：rag 包不依赖 LangGraph，
# Query 改写也要用这个函数，不能把它留在会加载图状态的模块里。

logger = logging.getLogger(__name__)

# 历史窗口只有一份定义：专家、Query 改写、检索接口必须使用同一个值。
from app.rag.policy import HISTORY_WINDOW  # noqa: E402


def recent_history(state: TravelState, max_messages: int = HISTORY_WINDOW) -> list[tuple[str, str]]:
    """把最近的历史消息转成提示词可用的 (角色, 内容) 列表。"""
    recent = (state.get("messages") or [])[-max_messages:]
    return [
        ("human" if isinstance(m, HumanMessage) else "ai", str(m.content))
        for m in recent
    ]


def finish_expert(name: str, finding: dict[str, Any], tokens: int = 0) -> Command:
    """把产出写进 findings、标记 done，并回到 planner。"""
    update: dict[str, Any] = {"done": [name], "findings": {name: finding}}
    if tokens:
        update["tokens"] = tokens
    return Command(goto="planner", update=update)


_RETRIEVAL_NOTE = """【知识库资料】
下面是知识库里检索到的原始资料。你给出的所有**具体事实**（价格、时间、门票、
班次、地址、距离、代码）都必须来自这里。

⚠️ 不要用你自己知道的数字去补。两个数字长得像不代表一样，而用户会照着你的
数字做决定。资料里查不到的就直说查不到，宁可答得不完整也不要编。
"""

_NO_RETRIEVAL_NOTE = """【知识库资料】
这次**没有检索到任何相关资料**（这个问题可能不在知识库覆盖范围内）。

因此：**不要给任何具体数字**——价格、时长、班次、门票、地址这类可核对的事实
一律不要凭记忆写。可以给方向性的建议，但凡是涉及具体数字的地方，
都要说明"这需要以官方渠道为准"。
"""

# 必须和上一条分开：知识库没覆盖是产品问题，检索失败是服务故障。
# 混成一句"没有资料"会把故障伪装成语料缺口，误导后续决策。
_RETRIEVAL_FAILED_NOTE = """【知识库资料】
这次**知识库检索没有成功**（服务暂时不可用），所以你没有资料可依据。

因此：**不要给任何具体数字**——价格、时长、班次、门票、地址这类可核对的事实
一律不要凭记忆写。可以给方向性的建议，但凡是涉及具体数字的地方，
都要说明"这需要以官方渠道为准"。
"""

# 只在本次拿到 POI 事实时追加；没有 POI 时不出现，避免模型误以为还有一份事实。
_POI_NOTE = """
【高德 POI 事实】里的名称、地址、类型、评分、参考人均可以直接使用；
那里没有的票价、营业时间仍按估算处理，不要写成确定事实。
"""


def _grounding(state: TravelState, *, has_poi: bool = False) -> str:
    """说明本次有哪些资料可用，并据此限制具体数字。

    有资料时附上原文；没有资料或检索失败时明确禁止给具体数字。
    不能留空：模型不知道有没有依据时，会照常凭记忆给价格和时刻。
    `has_poi=True` 时追加高德 POI 事实，和知识库资料是补充关系。
    """
    finding = (state.get("findings") or {}).get("rag")
    context = ""
    if finding is None:
        # 正常情况 planner._retrieval_first 会保证 rag 先跑。
        # 默默当成"没资料"会把"检索被绕过"伪装成"知识库没覆盖"，这里留一条 warning。
        logger.warning(
            "专家拿不到检索结果：findings 里没有 rag，检索可能又被绕过了（见 planner._retrieval_first）"
        )
        note = _NO_RETRIEVAL_NOTE
    else:
        context = finding.get("context") or ""
        if context:
            note = _RETRIEVAL_NOTE
        elif finding.get("error"):
            note = _RETRIEVAL_FAILED_NOTE
        else:
            note = _NO_RETRIEVAL_NOTE

    if has_poi:
        note += _POI_NOTE
    if context:
        return f"{note}\n{context}"
    return note


def _with_poi(
    finding: dict[str, Any], poi_meta: dict[str, Any]
) -> dict[str, Any]:
    """把 POI 排查信息挂到专家产出上；`enabled=False` 也保留，便于区分未开启和没查到。"""
    if not poi_meta:
        return finding
    return {**finding, "poi": poi_meta}


def run_expert(
    state: TravelState,
    name: str,
    prompt: str,
    schema: type[BaseModel],
    instruction: str,
    *,
    ground: bool = True,
    poi: PoiRequest | None = None,
) -> Command:
    """专家节点的统一骨架：调模型 → 结构化输出 → 标 done → 回 planner。

    异常一律吞掉并记 warning：单个专家超时或限流不应该让整轮对话 500，
    缺的那块会在 findings 里留下 error，由 itinerary / response 说明。

    调模型前注入检索结果：rag 不是可选的"专家意见"，而是所有专家共用的资料面。
    实测曾出现 25% 的住宿/交通/景点请求绕过 rag，模型凭记忆编价格。
    `ground=False` 只给 itinerary：它已经通过 `_render_findings()` 带上全部资料，
    再注入一次会重复两千字并多付 token。

    `poi` 为可选的高德事实查询；POI 失败只记 meta，不影响专家产出。
    """
    poi_block, poi_meta = ("", {})
    if poi is not None:
        try:
            poi_block, poi_meta = fetch_poi(state, name, poi)
        except Exception as e:
            # fetch_poi 已兜住 MCP 调用；这里防止城市解析等意外把专家带崩。
            logger.warning("%s 专家 POI 注入失败：%s", name, type(e).__name__)
            poi_block, poi_meta = "", {"enabled": True, "error": "unexpected"}

    parts = [instruction]
    if poi_block:
        parts.append(poi_block)
    if ground:
        parts.append(_grounding(state, has_poi=bool(poi_block)))
    instruction = "\n\n".join(parts)
    try:
        parsed, tokens = invoke_structured(
            prompt,
            recent_history(state),
            instruction,
            schema,
            node=name,
        )
    except Exception as e:
        logger.warning("%s 专家调用失败：%s", name, e)
        return finish_expert(
            name, _with_poi({"error": f"调用失败：{e}"}, poi_meta)
        )

    if parsed is None:
        # 结构化输出偶发失败（模型返回普通文本而非 tool call），重试一次成本很低。
        logger.warning("%s 专家结构化输出为空，重试一次", name)
        try:
            parsed, retry_tokens = invoke_structured(
                prompt,
                recent_history(state),
                instruction,
                schema,
                node=name,
            )
            tokens += retry_tokens
        except Exception as e:
            logger.warning("%s 专家重试失败：%s", name, e)

    if parsed is None:
        # 未配置 key 和"答了但格式不对"都会走到这里；宁可空着，也不能编。
        return finish_expert(
            name,
            _with_poi(
                {"error": "模型未返回可用结果（未配置 key、服务暂时不可用或输出格式不符）"},
                poi_meta,
            ),
        )

    # Pydantic 对象不能直接进检查点和 agent_runs.output 的 JSONB 列。
    # 先挂 poi meta 再清洗，清洗需要判断本次有没有 POI 事实。
    finding = _with_poi(parsed.model_dump(), poi_meta)
    try:
        finding = sanitize_expert(name, finding, state)
    except Exception as e:
        # 清洗是增强，失败不能让专家产出整个消失。
        logger.warning("%s 专家产出清洗失败：%s", name, type(e).__name__)
    return finish_expert(name, finding, tokens)
