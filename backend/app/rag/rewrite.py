"""Query 改写：一次 LLM 调用完成指代消解和元数据过滤抽取。

多轮问题常省略主语，例如"那住宿呢"；改写会补全成完整查询，
并抽出 city/doc_type。LLM 不可用或解析失败时退回原查询和空过滤，
改写是增强，不能阻塞检索。

改写是非确定性组件，评估时用 `--no-rewrite` 拿可复现基线。
"""

import logging
import time

from pydantic import BaseModel, Field

from app.agents.llm import invoke_structured
from app.rag.policy import CITIES, DOC_TYPES
from app.rag.policy import validate_filters

logger = logging.getLogger(__name__)

# 历史截断由调用方负责，这里只使用传入的 history。

# 改写提示词放本地，不进 app/agents/prompts.py：它不是智能体角色，
# 也保持 app/rag 自足。
_REWRITE_PROMPT = f"""你负责把用户的问题改写成更适合知识库检索的形式。

知识库是旅游资料，元数据有两个受控字段：

- city：只能从这里选——{'、'.join(CITIES)}
  （这是省份/大区域级别。用户说的是"大理""丽江"时填「云南」，
   说的是"都江堰""熊猫基地"时填「成都」。判断不出就留空。）
- doc_type：只能从这里选——{'、'.join(DOC_TYPES)}
  用户问什么就填什么。什么都问、判断不出就留空。

你要产出两样东西：

1. rewritten：把问题改写成一句【不依赖上下文也能读懂】的完整查询。
   ⚠️ 用户经常会省略主语（"那住宿呢""那边天气怎么样"），
   必须从对话历史里找出他真正在问什么，把省略的部分补全。
   改写的目的是让检索能命中，不是让句子变漂亮——
   不要加形容词、不要加"请帮我"这类客套、不要改变原意。

2. city / doc_type：从问题里判断出的过滤条件，判断不出就留空。
   ⚠️ 只在用户【明确提到】该目的地或类型时才填。
   从"帮我规划一下行程"里推断不出城市，这时候留空是对的——
   填错会让检索在一个错误的范围内搜索，而那比不搜更糟。
"""


class _ExtractedFilters(BaseModel):
    """结构化过滤条件；拆成明确字段，避免开放 dict 让模型随意填键。"""

    city: str = Field(default="", description="目的地，从给定的城市列表里选；判断不出留空")
    doc_type: str = Field(default="", description="文档类型，从给定类型里选；判断不出留空")


class QueryPlan(BaseModel):
    """改写的产出。"""

    rewritten: str = Field(description="补全上下文后的完整查询")
    filters: _ExtractedFilters = Field(default_factory=_ExtractedFilters)


def plan(
    query: str,
    history: list[tuple[str, str]] | None = None,
) -> tuple[QueryPlan, float]:
    """改写查询，返回 (QueryPlan, 耗时毫秒)。

    永远返回可用的 QueryPlan；LLM 不可用或解析失败时退化为原查询。
    """
    started = time.monotonic()

    fallback = QueryPlan(rewritten=query, filters=_ExtractedFilters())

    if not query or not query.strip():
        return fallback, 0.0

    try:
        parsed, _ = invoke_structured(
            _REWRITE_PROMPT,
            list(history or []),
            instruction=f"请改写这个查询：{query}",
            schema=QueryPlan,
            node="rewrite",
        )
    except Exception as e:
        # 限流、超时、未配置 key 都是预期内失败，只记 warning，避免刷屏。
        logger.warning("Query 改写失败，用原查询检索：%s", e)
        return fallback, _elapsed_ms(started)

    if parsed is None:
        logger.warning("Query 改写没有返回可用结果，用原查询检索")
        return fallback, _elapsed_ms(started)

    # 模型可能只填 filters 不填 rewritten；空查询会伪装成"没搜到"。
    rewritten = (parsed.rewritten or "").strip() or query

    return (
        QueryPlan(rewritten=rewritten, filters=parsed.filters),
        _elapsed_ms(started),
    )


def merge_filters(
    explicit: dict[str, str] | None,
    extracted: _ExtractedFilters,
) -> dict[str, str] | None:
    """合并调用方显式过滤和模型抽取的过滤。

    显式条件优先，因为代码传的值可验证，模型是猜的。两者都必须过受控词表：
    词表外的值会让 meta 匹配永远为空，用户只会看到"没有资料"。
    只取 city，不取 doc_type：文档体裁和用户问的内容粒度不一致，
    实测按 doc_type 硬过滤会掉 0.15 召回。doc_type 仍会抽取，仅用于排查。
    """
    merged: dict[str, str] = {}

    if extracted.city and extracted.city in CITIES:
        merged["city"] = extracted.city
    elif extracted.city:
        logger.warning("Query 改写给出的 city 不在受控词表里，已丢弃：%r", extracted.city)

    if extracted.doc_type:
        logger.debug("抽取到 doc_type=%r，按设计不用于过滤", extracted.doc_type)

    # 显式条件也要过词表：代码传的意图可信，但取值不一定合法。
    # 这里只丢弃并记 warning，接口边界（schemas.RagSearchRequest）才做 422。
    if explicit:
        accepted, dropped = validate_filters(explicit)
        merged.update(accepted)
        for reason in dropped:
            logger.warning("调用方传来的过滤条件被丢弃：%s", reason)

    return merged or None


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)
