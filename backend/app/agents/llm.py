"""大模型客户端的唯一构造点。

全项目只在这里 import LangChain 的模型客户端；换模型、参数或超时只改本文件。
NODE_PROVIDERS 是唯一分工表：DeepSeek 负责结构化判断和最终回复，
MiMo 负责长文生成。MiMo 未配置或熔断时自动回退 DeepSeek，只 warning 一次。
"""

import logging
import os
import threading

from app.common.resilience import (
    BreakerOutcome,
    CircuitOpenError,
    get_breaker,
)
from app.common.tracing import trace_span
from app.config import settings

logger = logging.getLogger(__name__)

DEEPSEEK_MODEL = "deepseek-v4-flash"

# 兼容旧引用：这个模块原来只有一个模型。外部如果 import 过 MODEL_NAME，
# 它仍然指 DeepSeek 的模型名。
MODEL_NAME = DEEPSEEK_MODEL

# 节点 → provider，唯一分工表；换节点只改这里。
NODE_PROVIDERS: dict[str, str] = {
    "planner": "deepseek",
    "rewrite": "deepseek",
    "weather_place": "deepseek",
    "response": "deepseek",
    "attraction": "mimo",
    "food": "deepseek",
    "hotel": "mimo",
    "transport": "mimo",
    "weather_advice": "mimo",
    "itinerary": "mimo",
}

PROVIDERS = ("deepseek", "mimo")

# MiMo 必须显式使用 json_schema；function_calling 实测会漏解析或拖延。
_STRUCTURED_METHOD: dict[str, str] = {"mimo": "json_schema"}

# 每个 provider 一个客户端；用已初始化集合区分"未初始化"和"初始化失败"。
_clients: dict[str, object | None] = {}
_initialized: set[str] = set()
_lock = threading.Lock()

# 回退告警只打一次，避免每个节点每次都刷屏。
_fallback_warned: set[str] = set()
_fallback_lock = threading.Lock()


def provider_for(node: str) -> str:
    """节点名 → provider。未知节点一律 DeepSeek（保守默认）。"""
    return NODE_PROVIDERS.get(node, "deepseek")


def get_llm(provider: str = "deepseek"):
    """返回 provider 的聊天客户端；没配 key 或包未安装时返回 None。

    None 不是异常，调用方走各自的降级分支。
    """
    if provider not in PROVIDERS:
        raise ValueError(f"未知 provider {provider!r}；只能是 {PROVIDERS}")

    if provider in _initialized:
        return _clients.get(provider)

    with _lock:
        if provider not in _initialized:
            _clients[provider] = _build_client(provider)
            _initialized.add(provider)
    return _clients.get(provider)


def get_llm_for(node: str):
    """按节点取客户端；provider 不可用时回退 DeepSeek，DeepSeek 也不可用才返回 None。"""
    provider = provider_for(node)
    llm = get_llm(provider)
    if llm is None and provider != "deepseek":
        _warn_fallback(provider)
        llm = get_llm("deepseek")
    return llm


def _build_client(provider: str):
    """真正创建客户端。失败一律返回 None 并记一条 warning，不往上抛。"""
    if provider == "deepseek":
        return _build_deepseek()
    if provider == "mimo":
        return _build_mimo()
    return None


def _build_deepseek():
    key = (settings.DEEPSEEK_API_KEY or "").strip()
    if not key:
        logger.warning("未配置 DEEPSEEK_API_KEY，智能体专家节点将不产出内容")
        return None

    # langchain-deepseek 是自己去读【进程环境变量】DEEPSEEK_API_KEY 的，
    # 它不认识我们的 settings 对象。所以这里把配置回填进 os.environ。
    #
    # 用 setdefault 而不是直接赋值：如果外部已经用环境变量指定了 key（比如 CI 或容器），
    # 那应该以外部为准，不要让 .env 里那份悄悄覆盖掉它。
    os.environ.setdefault("DEEPSEEK_API_KEY", key)

    # 延迟到函数内部 import：万一 langchain-deepseek 没装，
    # 也不该让整个后端启动失败——只是智能体降级而已。
    try:
        from langchain_deepseek import ChatDeepSeek
    except ImportError as e:
        logger.warning("langchain-deepseek 未安装，智能体专家节点将不产出内容：%s", e)
        return None

    try:
        return ChatDeepSeek(
            model=DEEPSEEK_MODEL,
            # ⚠️ 超时和重试必须显式给。不传的话 SDK 用的是自己的默认值
            # （可能长到几分钟，甚至没有上限），一次 API 卡住就会把
            # 整轮图拖死——2026-10 的答案质量评估实测卡在第 1 条 13 分钟，
            # 本地模型早就加载完了，进程只是在等一个没有超时的 HTTP 响应。
            # 90 秒和 MiMo 那边保持一致：flash 档足够，超时后节点走 error 分支。
            timeout=90,
            max_retries=1,
            # 关掉思考模式。本项目要的是快速的多节点调度，
            # 每个节点都带一段推理过程会让整张图的耗时成倍增长，
            # 而且各专家的产出本来就是结构化的，不需要"想很久"。
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        logger.warning("DeepSeek 客户端创建失败，智能体专家节点将不产出内容：%s", e)
        return None


def _build_mimo():
    """MiMo 是 OpenAI 兼容端点，所以走 ChatOpenAI，不需要单独的 SDK。

    ⚠️ 认证用标准 Bearer。官方文档同时接受 `api-key` 头；
    如果某天换到只认 `api-key` 的网关，在这里加
    `default_headers={"api-key": key}` 即可。
    """
    key = (settings.MIMO_API_KEY or "").strip()
    if not key:
        logger.warning("未配置 MIMO_API_KEY，MiMo 节点将回退到 DeepSeek")
        return None

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        logger.warning("langchain-openai 未安装，MiMo 节点将回退到 DeepSeek：%s", e)
        return None

    try:
        return ChatOpenAI(
            model=settings.MIMO_MODEL,
            api_key=key,
            base_url=settings.MIMO_BASE_URL,
            # 专家节点是同步调用，没有超时的话一次卡住会把整轮对话拖死。
            # 90 秒足够 flash 档生成一段专家建议；超时后节点走自己的 error 分支。
            timeout=90,
            max_retries=1,
            # MiMo 默认开思考。关掉的理由和 DeepSeek 那边一样：
            # 这些节点要的是快速产出，不需要长推理。
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as e:
        logger.warning("MiMo 客户端创建失败，MiMo 节点将回退到 DeepSeek：%s", e)
        return None


def _warn_fallback(provider: str) -> None:
    """回退告警只打一次。

    每个节点每次调用都 warning 的话，一轮对话能刷出十几条同样的日志，
    真正需要看的告警会被淹掉。
    """
    with _fallback_lock:
        if provider in _fallback_warned:
            return
        _fallback_warned.add(provider)
    logger.warning(
        "provider %r 当前不可用（未配置或熔断），相关节点本次回退到 DeepSeek",
        provider,
    )


def token_usage(message) -> int:
    """从一个 AIMessage 里读出这次调用消耗的 token 总数。

    LangChain 把用量统一放在 usage_metadata 里（input_tokens / output_tokens），
    不同厂商的原始字段名不一样，用这个就不用管底层是谁。

    取不到就返回 0：用量统计不值得为它中断一次对话，
    而且有些模型/网关就是不返回 usage。
    """
    meta = getattr(message, "usage_metadata", None) or {}
    return int(meta.get("input_tokens", 0)) + int(meta.get("output_tokens", 0))


def _build_structured(llm, provider: str, schema):
    """按 provider 选结构化输出方式。"""
    method = _STRUCTURED_METHOD.get(provider)
    return (
        llm.with_structured_output(schema, include_raw=True, method=method)
        if method
        else llm.with_structured_output(schema, include_raw=True)
    )


def _classify_llm_exception(exc: BaseException) -> BreakerOutcome:
    """LLM 调用抛异常一律算依赖失败。

    `parsed=None` 不走这里——那是"模型答了但格式没对上"，传输是成功的，
    不能把这种输出质量问题算成上游不可用。
    """
    if isinstance(exc, CircuitOpenError):
        # 防御性：内层调用如果已经带了熔断，不要把拒绝再记一次失败。
        return BreakerOutcome.IGNORE
    return BreakerOutcome.FAILURE


def _model_name(llm) -> str:
    """取模型名给 span 的 attributes 用；取不到就留空。"""
    name = getattr(llm, "model_name", None) or getattr(llm, "model", None) or ""
    return str(name)[:100]


def _invoke_structured_once(
    llm, provider: str, schema, messages, *, node: str = ""
):
    """一次真正的结构化调用，外层包 ``llm:<provider>`` 熔断和 LLM span。"""
    structured = _build_structured(llm, provider, schema)
    with trace_span(
        f"llm.{provider}",
        "llm",
        node_name=node or None,
        attributes={"provider": provider, "model": _model_name(llm)},
    ) as span:
        result = get_breaker(f"llm:{provider}").call(
            structured.invoke,
            messages,
            classify_exception=_classify_llm_exception,
        )
        if span is not None and isinstance(result, dict):
            raw = result.get("raw")
            span.tokens = token_usage(raw) if raw is not None else 0
            span.attributes["parsed_ok"] = result.get("parsed") is not None
        return result


def _invoke_text_once(
    client, provider_name: str, messages, *, node: str = ""
):
    """一次纯文本调用，外层包 ``llm:<provider>`` 熔断和 LLM span。"""
    with trace_span(
        f"llm.{provider_name}",
        "llm",
        node_name=node or None,
        attributes={
            "provider": provider_name,
            "model": _model_name(client),
        },
    ) as span:
        message = get_breaker(f"llm:{provider_name}").call(
            client.invoke,
            messages,
            classify_exception=_classify_llm_exception,
        )
        if span is not None:
            span.tokens = token_usage(message)
            content = (
                message.content
                if isinstance(message.content, str)
                else str(message.content)
            )
            span.attributes["output_chars"] = len(content)
        return message


def invoke_structured(
    prompt: str, history: list, instruction: str, schema, *, node: str = ""
):
    """调用 LLM 获取结构化输出，返回 (解析结果, token 用量)。

    node 决定 provider，默认 DeepSeek。本函数不依赖 LangGraph，
    因为图和 Query 改写都要复用它。使用 include_raw=True 以保留 token 用量。
    解析失败返回 (None, tokens)，表示模型答了但格式不符；
    LLM 不可用返回 (None, 0)。MiMo 熔断时回退 DeepSeek。
    """
    provider = provider_for(node) if node else "deepseek"
    llm = get_llm(provider)
    if llm is None and provider != "deepseek":
        _warn_fallback(provider)
        provider = "deepseek"
        llm = get_llm(provider)
    if llm is None:
        return None, 0

    messages: list = [("system", prompt), *history]
    if instruction:
        messages.append(("human", instruction))

    try:
        result = _invoke_structured_once(
            llm, provider, schema, messages, node=node
        )
    except CircuitOpenError:
        if provider != "mimo":
            logger.warning("DeepSeek 熔断中，结构化调用降级")
            return None, 0
        # MiMo 熔断时和"没配 MiMo key"走同一条回退：换 DeepSeek 再试一次。
        _warn_fallback(provider)
        provider = "deepseek"
        llm = get_llm(provider)
        if llm is None:
            return None, 0
        try:
            result = _invoke_structured_once(
                llm, provider, schema, messages, node=node
            )
        except CircuitOpenError:
            logger.warning("DeepSeek 熔断中，结构化调用降级")
            return None, 0

    raw = result.get("raw")
    tokens = token_usage(raw) if raw is not None else 0

    parsed = result.get("parsed")
    if parsed is None:
        # parsing_error 里是 pydantic 的校验细节，记进日志方便定位是哪次调用格式跑偏了
        logger.warning("结构化输出解析失败（provider=%s）：%s", provider, result.get("parsing_error"))
    return parsed, tokens


def invoke_text(
    prompt: str, history: list, instruction: str, *, node: str = ""
) -> tuple[str | None, int]:
    """调用 LLM 获取纯文本，返回 (文本, token 用量)。

    (None, 0) 表示没有可用模型；传输异常仍向上抛，由调用方处理。
    """
    provider = provider_for(node) if node else "deepseek"
    llm = get_llm(provider)
    if llm is None and provider != "deepseek":
        _warn_fallback(provider)
        provider = "deepseek"
        llm = get_llm(provider)
    if llm is None:
        return None, 0

    messages: list = [("system", prompt), *history]
    if instruction:
        messages.append(("human", instruction))

    try:
        message = _invoke_text_once(llm, provider, messages, node=node)
    except CircuitOpenError:
        if provider != "mimo":
            logger.warning("DeepSeek 熔断中，文本调用降级")
            return None, 0
        _warn_fallback(provider)
        provider = "deepseek"
        llm = get_llm(provider)
        if llm is None:
            return None, 0
        try:
            message = _invoke_text_once(llm, provider, messages, node=node)
        except CircuitOpenError:
            logger.warning("DeepSeek 熔断中，文本调用降级")
            return None, 0

    text = message.content if isinstance(message.content, str) else str(message.content)
    return text, token_usage(message)


def reset() -> None:
    """丢掉缓存的客户端，下次调用重新创建。

    给测试用：验证"有 key / 没 key"两种降级路径时，
    需要在不重启进程的前提下切换状态。
    生产代码不该调它。
    """
    global _clients, _initialized
    with _lock:
        _clients = {}
        _initialized = set()
    with _fallback_lock:
        _fallback_warned.clear()
