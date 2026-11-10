"""验证生成后的依据校验：出处白名单、数字回查、改写/删句/拒答。

不调用真实 LLM：改写用 monkeypatch 的 `invoke_text` 返回固定文本或 None。
不写数据库；纯合成 state。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_grounding.py
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents import grounding
from app.config import settings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_passed = 0
_failed = 0

CONTEXT = "【1】《成都主要景点详解 > 大熊猫繁育研究基地》\n门票约 55 元。"
CITATIONS = [
    {
        "index": 1,
        "title": "成都主要景点详解",
        "source": "https://example.com/cd",
        "section": ["大熊猫繁育研究基地"],
    }
]


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  OK  {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")
        if detail:
            print(f"       {detail}")


def _state(
    context: str = "",
    citations=None,
    weather_days=None,
    poi_items=None,
    query: str = "",
    messages=None,
) -> dict:
    findings = {
        "rag": {
            "context": context,
            "citations": citations or [],
            "has_material": bool(context),
        }
    }
    if weather_days is not None:
        findings["weather"] = {"days": weather_days}
    if poi_items is not None:
        findings["attraction"] = {
            "poi": {"enabled": True, "items": poi_items}
        }
    return {"user_query": query, "messages": messages or [], "findings": findings}


def _guard(answer: str, state: dict, rewrite: str | None = None):
    """跑 guard_answer，改写函数固定返回 rewrite（None 表示改写不可用）。"""
    original_invoke = grounding.invoke_text
    try:
        grounding.invoke_text = lambda *a, **k: (rewrite, 7 if rewrite else 0)
        return grounding.guard_answer(answer, state)
    finally:
        grounding.invoke_text = original_invoke


def main() -> int:
    original_enabled = settings.GROUNDING_GUARD_ENABLED
    original_retry = settings.GROUNDING_GUARD_RETRY
    settings.GROUNDING_GUARD_ENABLED = True
    settings.GROUNDING_GUARD_RETRY = True

    try:
        # ---- 1. 合法出处 + 资料内数字 ----
        state = _state(CONTEXT, CITATIONS)
        answer = "根据《成都主要景点详解》，大熊猫基地门票约 55 元。"
        violations = grounding.check_answer(answer, state)
        check("合法出处+资料数字不违规", violations == [], str(violations))
        safe, report = _guard(answer, state, rewrite=None)
        check("无违规时原样返回", safe == answer)
        check("无违规 action=none", report.action == "none", report.action)

        # ---- 2. 编造出处 + 数字，改写后通过 ----
        state = _state()
        answer = "根据《成都美食圣经》，火锅人均 80 元。"
        violations = grounding.check_answer(answer, state)
        check(
            "编造出处被识别",
            any(v.kind == "fabricated_citation" for v in violations),
            str(violations),
        )
        check(
            "无依据数字被识别",
            any(v.kind == "ungrounded_number" for v in violations),
            str(violations),
        )
        fixed = "根据资料，火锅人均约 80 元。"
        safe, report = _guard(answer, state, rewrite=fixed)
        check("改写后输出修正文本", safe == fixed, safe)
        check("改写后 action=rewritten", report.action == "rewritten", report.action)
        check("改写 token 计入报告", report.tokens == 7, str(report.tokens))
        check("改写前违规计数", report.violations_before >= 2, str(report.violations_before))

        # ---- 3. 无依据精确价格，改写不可用时拒答 ----
        state = _state()
        answer = "门票 123 元。"
        safe, report = _guard(answer, state, rewrite=None)
        check("无依据价格拒答", safe == grounding._REFUSAL, safe)
        check("拒答 action=refused", report.action == "refused", report.action)

        # 改写太短（模型敷衍“好的”）不能接受
        safe, report = _guard(answer, state, rewrite="好的。")
        check("过短改写被拒绝", safe == grounding._REFUSAL, safe)
        check("过短改写 action=refused", report.action == "refused", report.action)

        # ---- 4. 区间 + 估算标记放行 ----
        state = _state()
        answer = "门票约 50-80 元。"
        check(
            "区间估算放行",
            grounding.check_answer(answer, state) == [],
            str(grounding.check_answer(answer, state)),
        )
        safe, report = _guard(answer, state, rewrite=None)
        check("区间估算 action=none", report.action == "none", report.action)

        # ---- 5. “约 55 元”仍要回查 ----
        state = _state()
        answer = "门票约 55 元。"
        violations = grounding.check_answer(answer, state)
        check(
            "精确估算仍被回查",
            len(violations) == 1 and violations[0].kind == "ungrounded_number",
            str(violations),
        )
        safe, report = _guard(answer, state, rewrite=None)
        check("精确估算拒答", report.action == "refused", report.action)

        # ---- 6. 天气真实数字 ----
        state = _state(
            weather_days=[
                {
                    "date": "2026-11-05",
                    "precip_prob": 80,
                    "temp_max": 23.5,
                }
            ]
        )
        answer = "11 月 5 日降水概率 80%，气温 23.5℃。"
        check(
            "天气数字放行",
            grounding.check_answer(answer, state) == [],
            str(grounding.check_answer(answer, state)),
        )
        answer_c = "气温 23°C。"
        check(
            "°C 单位也能识别",
            grounding.check_answer(answer_c, state) == [],
            str(grounding.check_answer(answer_c, state)),
        )
        answer_bad = "气温 30°C。"
        check(
            "编造温度被识别",
            len(grounding.check_answer(answer_bad, state)) == 1,
            str(grounding.check_answer(answer_bad, state)),
        )
        answer_diff = "昼夜温差接近 10℃。"
        check(
            "接近的温度差放行",
            grounding.check_answer(answer_diff, state) == [],
            str(grounding.check_answer(answer_diff, state)),
        )
        answer_diff_bad = "昼夜温差 10℃。"
        check(
            "没有依据的温度差被识别",
            len(grounding.check_answer(answer_diff_bad, state)) == 1,
            str(grounding.check_answer(answer_diff_bad, state)),
        )

        # ---- 7. 用户原话里的数字 ----
        state = _state(query="预算 3000 元，玩 3 天")
        answer = "预算 3000 元可以玩 3 天。"
        check(
            "用户数字放行",
            grounding.check_answer(answer, state) == [],
            str(grounding.check_answer(answer, state)),
        )

        # ---- 8. POI 地址 ----
        state = _state(
            poi_items=[
                {
                    "name": "宽窄巷子",
                    "address": "人民路 123 号",
                    "cityname": "成都市",
                    "adname": "青羊区",
                }
            ]
        )
        answer = "地址是人民路 123 号。"
        check(
            "POI 地址放行",
            grounding.check_answer(answer, state) == [],
            str(grounding.check_answer(answer, state)),
        )

        # ---- 9. 行程结构 / 计划时长 ----
        state = _state()
        answer = "第 2 天上午去宽窄巷子，建议游玩 3 小时。"
        check(
            "天数与计划时长放行",
            grounding.check_answer(answer, state) == [],
            str(grounding.check_answer(answer, state)),
        )

        # ---- 10. 无来源的地铁线路 ----
        state = _state()
        answer = "坐地铁 2 号线到犀浦站。"
        violations = grounding.check_answer(answer, state)
        check(
            "无来源地铁线路被识别",
            len(violations) == 1 and violations[0].kind == "ungrounded_number",
            str(violations),
        )

        # ---- 11. 编造引用编号 ----
        state = _state(CONTEXT, CITATIONS)
        answer = "根据【9】，门票 55 元。"
        violations = grounding.check_answer(answer, state)
        check(
            "编造编号被识别",
            any(v.kind == "invalid_citation_index" for v in violations),
            str(violations),
        )

        # ---- 12. 校验器自身异常：fail-open ----
        state = _state()
        original_check = grounding.check_answer

        def boom(*args, **kwargs):
            raise RuntimeError("check failed")

        grounding_logger = logging.getLogger("app.agents.grounding")
        old_disabled = grounding_logger.disabled
        grounding_logger.disabled = True
        try:
            grounding.check_answer = boom
            safe, report = grounding.guard_answer("门票 123 元。", state)
        finally:
            grounding.check_answer = original_check
            grounding_logger.disabled = old_disabled
        check("校验异常原样返回", safe == "门票 123 元。", safe)
        check("校验异常 action=failed", report.action == "failed", report.action)

        # ---- 13. 开关关闭 ----
        settings.GROUNDING_GUARD_ENABLED = False
        try:
            safe, report = grounding.guard_answer("门票 123 元。", _state())
        finally:
            settings.GROUNDING_GUARD_ENABLED = True
        check("关闭时跳过校验", safe == "门票 123 元。")
        check("关闭时 action=skipped", report.action == "skipped", report.action)

        # ---- 14. 专家产出清洗 ----
        no_material = _state()
        finding = {"attractions": [{"price_range": "55 元"}]}
        grounding.sanitize_expert("attraction", finding, no_material)
        check("无资料时清空精确价格", finding["attractions"][0]["price_range"] == "")

        finding = {"attractions": [{"price_range": "约 50-80 元"}]}
        grounding.sanitize_expert("attraction", finding, no_material)
        check(
            "带估算标记的价格保留",
            finding["attractions"][0]["price_range"] == "约 50-80 元",
        )

        finding = {"attractions": [{"price_range": "免费"}]}
        grounding.sanitize_expert("attraction", finding, no_material)
        check("无数字的价格结论保留", finding["attractions"][0]["price_range"] == "免费")

        finding = {
            "attractions": [{"price_range": "55 元"}],
            "poi": {"items": [{"name": "某景点"}]},
        }
        grounding.sanitize_expert("attraction", finding, no_material)
        check("有 POI 事实时不清价格", finding["attractions"][0]["price_range"] == "55 元")

        material = _state(CONTEXT, CITATIONS)
        finding = {"attractions": [{"price_range": "55 元"}]}
        grounding.sanitize_expert("attraction", finding, material)
        check("有资料时不清价格", finding["attractions"][0]["price_range"] == "55 元")

        finding = {"options": [{"price_range": "500 元"}]}
        grounding.sanitize_expert("transport", finding, no_material)
        check("交通精确票价也清空", finding["options"][0]["price_range"] == "")

    finally:
        settings.GROUNDING_GUARD_ENABLED = original_enabled
        settings.GROUNDING_GUARD_RETRY = original_retry

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
