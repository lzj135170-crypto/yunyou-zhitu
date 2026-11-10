"""验证行程 POI 补充：词表、章节对账、端到端 context。

三件事分开检查，因为它们的失效方式不同：

1. **意图识别**：行程 query 被漏判 → 什么都不会补；非行程 query 被误判
   → 多补几段无关章节。用正反例断言。
2. **词表对账**：词表里的 canonical 必须真的存在于向量库的 `section_key`。
   漏了不会报错，只会"这个景点永远补不进来"。
3. **端到端**：跑一次 `pipeline.search`，断言大熊猫基地 / 宽窄巷子的块
   真的进了 context，而且没有超过 3000 字预算。

用法（在 backend 目录下执行）：

    .venv/Scripts/python.exe scripts/check_poi_expand.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sqlalchemy import text  # noqa: E402

from app.rag import poi as poi_mod  # noqa: E402
from app.rag import vector_store  # noqa: E402

QUERY = "成都三天怎么安排"


def main() -> int:
    passed = failed = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  ✅ {label}")
        else:
            failed += 1
            print(f"  ❌ {label}")
            if detail:
                print(f"     {detail}")

    print("[1] 行程意图识别")
    for q in ("成都三天怎么安排", "成都三日游", "成都4天3晚", "成都怎么玩", "成都周边一日游"):
        check(f"正例：{q}", poi_mod.is_itinerary_query(q))
    for q in ("成都住哪个区域比较方便", "从成都怎么去都江堰", "成都有什么好吃的", "成都火锅人均大概多少钱"):
        check(f"反例：{q}", not poi_mod.is_itinerary_query(q))

    engine = vector_store.get_vector_engine()
    if engine is None:
        print("\n❌ 向量库连不上")
        return 1

    print("\n[2] 词表 canonical 和 section_key 对账")
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT section_key FROM document_chunks")
        ).fetchall()
    have = {r[0] for r in rows}
    missing = [canonical for canonical, _ in poi_mod.POI_ALIASES if canonical not in have]
    check(f"{len(poi_mod.POI_ALIASES)} 个 canonical 全部存在", not missing, f"缺：{missing}")

    print("\n[3] 从行程骨架抽取 POI")
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT content FROM document_chunks "
                "WHERE section_key = '三日行程骨架' LIMIT 1"
            )
        ).fetchone()
    if row is None:
        check("找得到「三日行程骨架」", False)
    else:
        names = poi_mod.extract_pois(row[0])
        print(f"     抽到：{names}")
        for expected in ("大熊猫繁育研究基地", "宽窄巷子", "武侯祠", "杜甫草堂", "都江堰", "青城山"):
            check(f"抽到 {expected}", expected in names)

    print("\n[4] 端到端：行程 query 的主 context")
    from app.rag import pipeline

    outcome = pipeline.search(QUERY, top_k=8, rewrite_query=False)
    if outcome is None:
        check("pipeline.search 返回结果", False)
    else:
        print(f"     poi_expanded：{outcome.poi_expanded}")
        print(f"     context_len：{len(outcome.assembled.context)}")
        check(
            "补充了大熊猫繁育研究基地",
            "大熊猫繁育研究基地" in outcome.poi_expanded,
            f"实际 {outcome.poi_expanded}",
        )
        check(
            "补充了宽窄巷子",
            "宽窄巷子" in outcome.poi_expanded,
            f"实际 {outcome.poi_expanded}",
        )
        check(
            "context 里有熊猫基地门票 55 元",
            "55 元" in outcome.assembled.context,
            "补充块没进预算，或取错了章节",
        )
        check(
            "context 里有宽窄巷子「全天开放」",
            "全天开放" in outcome.assembled.context,
            "补充块没进预算，或取错了章节",
        )
        from app.rag.policy import CONTEXT_BUDGET_CHARS

        check(
            f"context 不超过预算 {CONTEXT_BUDGET_CHARS} 字",
            len(outcome.assembled.context) <= CONTEXT_BUDGET_CHARS,
            f"实际 {len(outcome.assembled.context)} 字",
        )

    print(f"\n通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
