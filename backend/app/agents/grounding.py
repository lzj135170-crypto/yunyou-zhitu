"""生成后的确定性依据校验。

最终回复写入 messages 前，用代码检查三类问题：

1. 出处白名单：《标题》必须来自 `rag.citations`；
2. 数字回查：可核对数字必须出现在知识库、天气、POI 事实或用户原话里；
3. 估算标记：价格只有带估算词且是区间或 10 的倍数，才允许不回查。

处理顺序：按违规清单改写一次 → 删句 → 拒答。
校验器自身异常时原样返回（fail-open），报告里记 `failed`。
它不承诺 0% 编造，定性描述、中文数字和无数值结论不在覆盖范围。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.llm import invoke_text
from app.agents.state import TravelState
from app.common.tracing import trace_span
from app.config import settings

logger = logging.getLogger(__name__)

# 带单位的数字。单位顺序有讲究：
# - `分钟` 必须在 `分` 前面，否则“5 分钟”会被拆成“5 分”；
# - `千米` 必须在 `米` 前面；
# - `号线` 必须在 `号` 前面。
# 不抓 `人/晚/次/天`：这些通常是同行人数或行程安排，抓了误伤远大于收益。
_NUMBER_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*"
    r"(元|块|小时|分钟|分|公里|千米|米|号线|班|%|℃|°C|°c|度|岁|号)"
)

# 单独的数字 token，用于从事实源里抽数字做数值比较。
_NUMBER_TOKEN_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])")

_CITATION_RE = re.compile(r"《([^》]{1,80})》")
_INDEX_RE = re.compile(r"【(\d{1,3})】")
_DAY_RE = re.compile(r"第\s*\d+\s*天")
_TIME_RE = re.compile(r"\d+\s*点(?:半)?")

# 价格“估算”的信号。刻意不放“多”和“起”：
# “一起”“很多”这类词会把整句误判成估算，反而放过真正的精确数字。
_ESTIMATE_RE = re.compile(
    r"约|大约|左右|估算|参考|以官方为准|区间|不等|浮动|"
    r"预计|大概|接近|差不多|上下|以内|以上|至少|至多|不超过|不少于"
)
_PRICE_UNITS = frozenset({"元", "块"})
_PLANNING_MARKERS = (
    "建议",
    "安排",
    "游玩",
    "游览",
    "预留",
    "逛",
    "车程",
    "耗时",
    "步行",
    "住",
    "玩",
)

# 上下文块的格式由 app/rag/assemble.py 决定：
#     【1】《成都主要景点详解 > 大熊猫繁育研究基地》
#     正文……
_CONTEXT_BLOCK_RE = re.compile(
    r"【(\d+)】《([^》]+)》\n(.*?)(?=\n\n【\d+】《|\Z)", re.S
)

_FULLWIDTH_TABLE = str.maketrans("０１２３４５６７８９．", "0123456789.")

_CLAUSE_PUNCT = "，,、；;：:（）()"
_MIN_KEEP_RATIO = 0.4

_STRIP_NOTE = "\n\n（已移除资料中无法核实的数字，具体以官方渠道为准。）"
_REFUSAL = (
    "知识库里没有足够的资料来回答这个问题，我不会给出没有出处的具体价格、"
    "时刻或地址。建议以官方渠道为准。"
)

_REWRITE_PROMPT = """你是一个依据校验后的改写器。

下面这段回复里有资料无法支持的具体数字或出处。请改写这段回复：
- 删除或弱化无法支持的内容，不要新增任何事实；
- 保留其余内容和语气；
- 不要解释你在改写，直接输出改写后的回复。
"""


@dataclass
class Violation:
    kind: str          # fabricated_citation | invalid_citation_index | ungrounded_number
    text: str          # 触发片段，最多 80 字
    detail: str = ""


@dataclass
class GroundingReport:
    checked: bool
    action: str        # none | rewritten | stripped | refused | skipped | failed
    violations_before: int = 0
    violations_after: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)
    retried: bool = False
    tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "action": self.action,
            "violations_before": self.violations_before,
            "violations_after": self.violations_after,
            "kinds": dict(self.kinds),
            "samples": list(self.samples),
            "retried": self.retried,
            "tokens": self.tokens,
        }


@dataclass(frozen=True)
class _Source:
    """一次校验要用到的事实源和出处索引。"""

    text: str
    numbers: tuple[float, ...]
    temperature_numbers: tuple[float, ...]
    index_numbers: dict[int, tuple[float, ...]]
    title_numbers: dict[str, tuple[float, ...]]
    source_numbers: dict[str, tuple[float, ...]]
    valid_titles: frozenset[str]
    valid_paths: frozenset[str]
    valid_sources: frozenset[str]


def guard_answer(
    answer: str, state: TravelState
) -> tuple[str, GroundingReport]:
    """校验并修正最终回复，返回 (可展示文本, 报告)。

    永远返回字符串：校验器异常时原样返回并记 `failed`，调用方不需要 try/except。
    """
    if not answer or not answer.strip() or not settings.GROUNDING_GUARD_ENABLED:
        return answer, GroundingReport(checked=False, action="skipped")

    try:
        with trace_span("grounding.verify", "grounding") as span:
            original = check_answer(answer, state)
            if not original:
                report = _make_report("none", [], [])
                _apply_span(span, report)
                return answer, report

            current = answer
            remaining = original
            retried = False
            tokens = 0

            if settings.GROUNDING_GUARD_RETRY:
                retried = True
                fixed, tokens = _rewrite(answer, original)
                # 过短的“改写”通常是模型敷衍了一句“好的”，不能拿它替掉
                # 整段回答；宁可走后面的删句/拒答。
                if fixed and len(fixed) >= len(answer) * _MIN_KEEP_RATIO:
                    current = fixed
                    remaining = check_answer(fixed, state)
                    if not remaining:
                        report = _make_report(
                            "rewritten", original, [], retried=True, tokens=tokens
                        )
                        _apply_span(span, report)
                        return fixed, report

            stripped = _strip_answer(current, remaining)
            if stripped and len(stripped) >= len(current) * _MIN_KEEP_RATIO:
                after = check_answer(stripped, state)
                if not after:
                    report = _make_report(
                        "stripped", original, [], retried=retried, tokens=tokens
                    )
                    _apply_span(span, report)
                    return stripped + _STRIP_NOTE, report
                remaining = after

            report = _make_report(
                "refused", original, remaining, retried=retried, tokens=tokens
            )
            _apply_span(span, report)
            return _REFUSAL, report

    except Exception:
        logger.warning("依据校验失败，保留原回复", exc_info=True)
        return answer, GroundingReport(checked=False, action="failed")


def sanitize_expert(
    name: str, finding: dict[str, Any], state: TravelState
) -> dict[str, Any]:
    """专家产出进入 findings 前的确定性清洗。

    只在没有知识库资料、也没有 POI 事实时，清空不带估算标记的精确
    `price_range`。有资料时由最终校验判断，有 POI 时 `avg_cost` 只是参考人均。
    """
    field_name = {
        "attraction": "attractions",
        "food": "foods",
        "hotel": "hotels",
        "transport": "options",
    }.get(name)
    if not field_name:
        return finding

    rag = (state.get("findings") or {}).get("rag") or {}
    if rag.get("has_material"):
        return finding

    poi = finding.get("poi")
    if isinstance(poi, dict) and poi.get("items"):
        return finding

    items = finding.get(field_name)
    if not isinstance(items, list):
        return finding

    changed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        price = item.get("price_range")
        if not isinstance(price, str) or not price.strip():
            continue
        # “免费”“详询”这类没有数字的结论不动；只清精确数字。
        if not re.search(r"\d", price):
            continue
        if _ESTIMATE_RE.search(price):
            continue
        item["price_range"] = ""
        changed += 1

    if changed:
        logger.warning("%s 没有资料也没有 POI，清空 %s 个精确价格", name, changed)
    return finding


def check_answer(answer: str, state: TravelState) -> list[Violation]:
    """跑一遍校验，返回违规列表；不修改 answer。"""
    source = _build_source(state)
    violations = _check_citations(answer, source)

    # 先去掉“第 2 天”“9 点”这类行程结构，避免把它们当事实数字。
    cleaned = _DAY_RE.sub(" ", answer)
    cleaned = _TIME_RE.sub(" ", cleaned)

    for sentence in _sentences(cleaned):
        cited = _cited_numbers(sentence, source)
        for match in _NUMBER_RE.finditer(sentence):
            number = match.group(1)
            unit = match.group(2)
            clause = _clause_of(sentence, match.start())

            if unit in {"小时", "分钟"} and any(
                marker in clause for marker in _PLANNING_MARKERS
            ):
                continue

            if _ESTIMATE_RE.search(clause):
                if unit in _PRICE_UNITS:
                    if _is_range(clause, number) or _is_multiple_of_ten(number):
                        continue
                    # 精确价格（比如“约 55 元”）仍要回查。
                else:
                    continue

            if _grounded(number, unit, cited, source):
                continue

            violations.append(
                Violation(
                    "ungrounded_number",
                    _clip(match.group(0).strip() or number),
                    _clip(clause, 60),
                )
            )

    return violations


def _build_source(state: TravelState) -> _Source:
    """只收真实数据，不收模型生成的 `summary` / `advice` / `price_range`。

    把模型自由文本当事实源，等于让模型自己给自己作证。
    """
    findings = state.get("findings") or {}
    rag = findings.get("rag") or {}
    context = str(rag.get("context") or "")
    citations = rag.get("citations") or []

    index_numbers: dict[int, tuple[float, ...]] = {}
    title_acc: dict[str, list[float]] = {}

    for match in _CONTEXT_BLOCK_RE.finditer(context):
        index = int(match.group(1))
        header = match.group(2)
        body = _normalize_text(match.group(3))
        numbers = _extract_numbers(body)
        index_numbers[index] = numbers

        header_key = _canon(header)
        title_acc.setdefault(header_key, []).extend(numbers)
        doc_title = _canon(header.split(" > ")[0])
        if doc_title != header_key:
            title_acc.setdefault(doc_title, []).extend(numbers)

    valid_titles: set[str] = set()
    valid_paths: set[str] = set()
    valid_sources: set[str] = set()
    source_acc: dict[str, list[float]] = {}

    if isinstance(citations, list):
        for cite in citations:
            if not isinstance(cite, dict):
                continue

            title = _canon(str(cite.get("title") or ""))
            if title:
                valid_titles.add(title)
                section = cite.get("section") or []
                if isinstance(section, list) and section:
                    path = " > ".join(
                        part for part in (title, *(str(s) for s in section if s))
                    )
                    valid_paths.add(_canon(path))

            source = _canon(str(cite.get("source") or ""))
            if source:
                valid_sources.add(source)
                index = cite.get("index")
                if isinstance(index, int):
                    source_acc.setdefault(source, []).extend(
                        index_numbers.get(index, ())
                    )

    parts = [context]

    weather = findings.get("weather") or {}
    days = weather.get("days")
    weather_source = _weather_source(days)
    if weather_source:
        parts.append(json.dumps(weather_source, ensure_ascii=False, default=str))

    # 各专家的 POI 事实。只收 poi.items，不收同级的 reason/price_range。
    for finding in findings.values():
        if not isinstance(finding, dict):
            continue
        poi = finding.get("poi")
        if not isinstance(poi, dict):
            continue
        items = poi.get("items")
        if isinstance(items, list) and items:
            parts.append(
                json.dumps(_poi_source(items), ensure_ascii=False, default=str)
            )

    parts.append(str(state.get("user_query") or ""))
    for message in state.get("messages") or []:
        # 只收人类说的话。AI 上一轮也可能编过数字，不能拿它当依据。
        if isinstance(message, HumanMessage):
            parts.append(str(message.content))

    text = _normalize_text("\n".join(parts))
    return _Source(
        text=text,
        numbers=_extract_numbers(text),
        # 温度单独抽：±1 的取整容差只能和真实气温比，
        # 不能和日期里的“11 月”或降水概率的“10%”碰巧匹配。
        temperature_numbers=_weather_temperatures(days),
        index_numbers=index_numbers,
        title_numbers={key: tuple(values) for key, values in title_acc.items()},
        source_numbers={key: tuple(values) for key, values in source_acc.items()},
        valid_titles=frozenset(valid_titles),
        valid_paths=frozenset(valid_paths),
        valid_sources=frozenset(valid_sources),
    )


def _check_citations(answer: str, source: _Source) -> list[Violation]:
    violations: list[Violation] = []

    for match in _CITATION_RE.finditer(answer):
        key = _canon(match.group(1))
        if (
            key not in source.valid_titles
            and key not in source.valid_paths
            and key not in source.valid_sources
        ):
            violations.append(
                Violation(
                    "fabricated_citation",
                    match.group(0),
                    "标题不在 citations 中",
                )
            )

    for match in _INDEX_RE.finditer(answer):
        index = int(match.group(1))
        if index not in source.index_numbers:
            violations.append(
                Violation(
                    "invalid_citation_index",
                    match.group(0),
                    "编号不在 citations 中",
                )
            )

    return violations


def _cited_numbers(
    sentence: str, source: _Source
) -> tuple[bool, tuple[float, ...]]:
    """返回这一句引用的出处及其数字。

    有引用但查不到对应正文时返回 `(True, ())`，按无依据处理，不退回全库搜索。
    """
    has_citation = False
    numbers: list[float] = []

    for match in _CITATION_RE.finditer(sentence):
        has_citation = True
        key = _canon(match.group(1))
        numbers.extend(source.title_numbers.get(key, ()))
        numbers.extend(source.source_numbers.get(key, ()))

    for match in _INDEX_RE.finditer(sentence):
        has_citation = True
        index = int(match.group(1))
        numbers.extend(source.index_numbers.get(index, ()))

    return has_citation, tuple(numbers)


def _grounded(
    number: str,
    unit: str,
    cited: tuple[bool, tuple[float, ...]],
    source: _Source,
) -> bool:
    try:
        target = float(number)
    except ValueError:
        return False

    has_citation, cited_numbers = cited

    if unit in {"℃", "°C", "°c", "度"}:
        if has_citation:
            # 引用块里的温度按精确值查，不做 ±1：块里还有别的数字，
            # 放宽容差会让“23℃”碰巧匹配到块里的日期/价格。
            return _contains(cited_numbers, target, 0.0)
        if _contains(source.temperature_numbers, target, 1.0):
            return True
        # 不是天气数据里的温度，再按全库精确值查（比如用户原话里的温度）。
        return _contains(source.numbers, target, 0.0)

    candidates = cited_numbers if has_citation else source.numbers
    return _contains(candidates, target, 0.0)


def _rewrite(
    answer: str, violations: list[Violation]
) -> tuple[str | None, int]:
    details = "\n".join(
        f"- {v.text}" + (f"（{v.detail}）" if v.detail else "")
        for v in violations[:8]
    )
    instruction = f"无法支持的内容：\n{details}\n\n原回复：\n{answer}"

    try:
        text, tokens = invoke_text(_REWRITE_PROMPT, [], instruction, node="response")
    except Exception:
        logger.warning("依据校验改写失败", exc_info=True)
        return None, 0

    if not text or not text.strip():
        return None, 0
    return text.strip(), tokens


def _strip_answer(answer: str, violations: list[Violation]) -> str:
    """确定性地删掉没依据的分句/句子，替换编造出处。"""
    bad_numbers = [v.text for v in violations if v.kind == "ungrounded_number"]
    bad_citations = [v.text for v in violations if v.kind == "fabricated_citation"]
    bad_indices = [v.text for v in violations if v.kind == "invalid_citation_index"]

    kept: list[str] = []
    for sentence in _sentences(answer):
        if any(text and text in sentence for text in bad_numbers):
            continue

        for text in bad_citations:
            if text:
                sentence = sentence.replace(text, "相关资料")
        for text in bad_indices:
            if text:
                sentence = sentence.replace(text, "")

        kept.append(sentence)

    return "".join(kept).strip()


def _make_report(
    action: str,
    before: list[Violation],
    after: list[Violation],
    *,
    retried: bool = False,
    tokens: int = 0,
) -> GroundingReport:
    kinds: dict[str, int] = {}
    for violation in before:
        kinds[violation.kind] = kinds.get(violation.kind, 0) + 1

    return GroundingReport(
        checked=True,
        action=action,
        violations_before=len(before),
        violations_after=len(after),
        kinds=kinds,
        samples=[_clip(v.text, 80) for v in before[:5]],
        retried=retried,
        tokens=tokens,
    )


def _apply_span(span, report: GroundingReport) -> None:
    if span is None:
        return
    span.attributes["violations_before"] = report.violations_before
    span.attributes["violations_after"] = report.violations_after
    span.attributes["action"] = report.action
    span.attributes["retried"] = report.retried


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。！？!?\n])", text or "") if part]


def _clause_of(sentence: str, start: int) -> str:
    left = -1
    for char in _CLAUSE_PUNCT:
        left = max(left, sentence.rfind(char, 0, start))

    right = len(sentence)
    for char in _CLAUSE_PUNCT:
        found = sentence.find(char, start)
        if found != -1:
            right = min(right, found)

    return sentence[left + 1 : right]


def _is_range(clause: str, number: str) -> bool:
    escaped = re.escape(number)
    sep = r"[-~—–至到～]"
    left = re.search(r"(?<![\d.])" + escaped + r"\s*" + sep + r"\s*\d", clause)
    right = re.search(r"\d\s*" + sep + r"\s*" + escaped + r"(?![\d.])", clause)
    return bool(left or right)


def _is_multiple_of_ten(number: str) -> bool:
    try:
        return float(number) % 10 == 0
    except ValueError:
        return False


def _contains(
    numbers: tuple[float, ...] | list[float], target: float, tolerance: float
) -> bool:
    return any(abs(value - target) <= tolerance + 1e-9 for value in numbers)


def _weather_source(days: Any) -> list[dict[str, Any]]:
    """天气事实源只留用户能读到、且可能被引用的字段。

    不收 `weather_code`（如 61）和坐标：它们和价格/时长没有关系，
    留在源里只会让“61 元”这种数字碰巧匹配上。
    """
    if not isinstance(days, list):
        return []
    fields = ("date", "weather", "temp_max", "temp_min", "precip_prob")
    return [
        {key: day.get(key) for key in fields}
        for day in days
        if isinstance(day, dict)
    ]


def _poi_source(items: Any) -> list[dict[str, Any]]:
    """POI 事实源只留名称/地址/类型/评分/参考人均。

    坐标和电话不进源：它们不是回答里该出现的数字，也会扩大误匹配面。
    """
    if not isinstance(items, list):
        return []
    fields = (
        "name",
        "address",
        "cityname",
        "adname",
        "type",
        "business_area",
        "rating",
        "avg_cost",
    )
    return [
        {key: item.get(key) for key in fields}
        for item in items
        if isinstance(item, dict)
    ]


def _weather_temperatures(days: Any) -> tuple[float, ...]:
    """从天气 finding 里抽真实气温，供温度的 ±1 取整比较。"""
    if not isinstance(days, list):
        return ()

    values: list[float] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        for key in ("temp_max", "temp_min"):
            value = day.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return tuple(values)


def _extract_numbers(text: str) -> tuple[float, ...]:
    values: list[float] = []
    for match in _NUMBER_TOKEN_RE.finditer(text or ""):
        try:
            values.append(float(match.group(1)))
        except ValueError:
            continue
    return tuple(values)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").translate(_FULLWIDTH_TABLE))


def _canon(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _clip(text: str, limit: int = 80) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"
