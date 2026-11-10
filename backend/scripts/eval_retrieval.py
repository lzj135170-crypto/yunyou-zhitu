"""检索效果验证：把各阶段的结果并排打出来，看清每一路到底召回了什么。

用法（在 backend 目录下执行）：

    # 看一次完整检索的四个阶段
    .venv/Scripts/python.exe scripts/eval_retrieval.py "成都有什么好吃的"

    # 并排对比：只用向量 / 只用稀疏 / RRF 融合 / 精排后
    .venv/Scripts/python.exe scripts/eval_retrieval.py --compare "带小孩不要太累的地方"

    # 带元数据过滤
    .venv/Scripts/python.exe scripts/eval_retrieval.py --city 成都 "住哪儿方便"

    # 关掉 Query 改写，看看不改写差多少
    .venv/Scripts/python.exe scripts/eval_retrieval.py --no-rewrite "那住宿呢"

为什么需要这个脚本（而不是"发一条消息看回复对不对"）：

    最终回复是很多环节叠加的产物——检索没召回、精排排错了、模型表达跑偏，
    在回复里长得一模一样。要定位到底哪一环出问题，必须把中间结果摊开看。

    尤其是 [v?,k?] 这一列：它直接说明某一条是"向量路第几名、关键词路第几名"。
    如果所有结果都没有 k，说明关键词路一条都没召回；
    如果融合后的排序和单路完全一样，说明另一路没起作用——
    这两种情况在最终回复里完全看不出来。

这个脚本【只读】，不写任何库。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag import pipeline  # noqa: E402

# 评估时取回的候选数。比正常检索大得多，因为要看的是"召回得全不全"，
# 而不是"最终给用户看哪 5 条"。
EVAL_K = 15
# 候选池大小。pipeline 内部默认只保留 30 条融合结果，
# 评估时放宽一点才看得到两路各自的完整榜单。
POOL = 30


def main() -> int:
    args = _parse_args()
    filters = {"city": args.city} if args.city else None

    outcome = pipeline.search(
        args.query,
        filters=filters,
        top_k=EVAL_K,
        history=_history(args.history),
        rewrite_query=not args.no_rewrite,
    )

    if outcome is None:
        print("❌ 向量库这条路不可用（容器没起 / 表不存在 / SQL 报错）")
        print("   先确认：docker compose -f deploy/pgvector/docker-compose.yml up -d")
        return 1

    _print_header(args.query, outcome)

    if not outcome.candidates:
        print()
        print("（没有召回任何内容。可能是语料里确实没有相关材料，")
        print("  也可能是过滤条件把范围筛空了——试试去掉 --city。）")
        return 0

    if args.compare:
        _print_compare(outcome)
    else:
        _print_stages(outcome)

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="检索效果验证")
    p.add_argument("query", help="查询语句")
    p.add_argument("--city", help="按城市过滤（受控词表：成都/西安/云南/三亚/北京/川西）")
    p.add_argument("--compare", action="store_true", help="四个阶段并排对比")
    p.add_argument("--no-rewrite", action="store_true", help="关掉 Query 改写")
    p.add_argument(
        "--history",
        nargs="*",
        default=[],
        help="模拟多轮对话的历史，形如 --history 'user:帮我规划成都三日游' 'ai:好的…'",
    )
    return p.parse_args()


def _history(raw: list[str]) -> list[tuple[str, str]]:
    """把 --history 的参数解析成 (角色, 内容) 列表。

    格式是 "user:xxx" / "ai:xxx"。没写前缀的一律当成 user——
    用法上最自然的是补用户说过的话。
    """
    out: list[tuple[str, str]] = []
    for item in raw:
        role, sep, text = item.partition(":")
        if sep and role.strip() in ("user", "ai", "human"):
            out.append(("human" if role.strip() in ("user", "human") else "ai", text.strip()))
        else:
            out.append(("human", item))
    return out


def _print_header(query: str, outcome) -> None:
    print(f"查询：{query}")
    if outcome.rewritten != query:
        print(f"改写：{outcome.rewritten}")
    print(f"过滤：{outcome.filters or '（无）'}")
    print(
        f"路线：{outcome.backend}"
        f"    耗时(ms)："
        + "  ".join(f"{k}={v:.0f}" for k, v in outcome.timings_ms.items())
    )
    print()


def _rank(rank: int | None) -> str:
    """把一个名次格式化成 `#3` / `—`。

    ⚠️ 这里必须写 `is None` 而不是 `if rank`：名次是**从 1 开始**的，
    所以真值判断今天碰巧是对的——但哪天有人把名次改成 0 起，
    `if rank` 会把**第 1 名**显示成"—"（没召回），而不报错。
    `eval_quality --self-check` 的检查 4 就是盯这件事的。
    """
    return f"#{rank}" if rank is not None else "—"


def _print_stages(outcome) -> None:
    """按最终顺序打印，每条带上它在各路里的名次。"""
    print(f"{'#':>3} {'最终分':>7}  {'向量':>5} {'稀疏':>5} {'稀疏分':>8}  出处 / 内容")
    print("-" * 100)
    for i, c in enumerate(outcome.candidates, 1):
        ev = c.evidence
        v = _rank(ev.get("vec"))
        k = _rank(ev.get("sparse"))
        b = f"{c.sparse_score:.3f}" if c.sparse_score is not None else "—"
        src = _where(c)
        print(f"{i:>3} {c.score:>7.4f}  {v:>5} {k:>5} {b:>8}  {src}")
        print(f"    {_first_line(c.content)}")


def _print_compare(outcome) -> None:
    """一行一篇文档，把各阶段的名次/分数摊在一行里看。

    ⚠️ 为什么按文档排而不是按阶段排成四列：
    按阶段排的话，同一篇文档会出现在好几个列里，读者要自己去做"这两行的
    标题是不是同一个"的眼力活，而四列横向滚动之后根本对不上。
    一行一篇文档，每一列是它在某个阶段的位置，一眼就能看出
    "这篇是靠向量路上来的"还是"靠稀疏路上来的"。

    看这一屏要回答的三个问题：
      - 有没有文档的「向量」列是 —？那说明语义路没召回它
      - 有没有文档的「稀疏」列是 —？那说明稀疏路没召回它
      - 「精排」列的顺序和「RRF 分」列差多少？完全一样说明重排没起作用
    """
    cands = outcome.candidates
    # 精排后的名次：candidates 本身就是精排结果的顺序
    final_rank = {id(c): i + 1 for i, c in enumerate(cands)}

    print(f"{'精排':>4} {'RRF分':>8} {'向量':>5} {'稀疏':>5} {'稀疏分':>8}  出处（文档 > 章节）")
    print("-" * 108)
    for c in cands:
        ev = c.evidence
        v = _rank(ev.get("vec"))
        k = _rank(ev.get("sparse"))
        b = f"{c.sparse_score:.3f}" if c.sparse_score is not None else "—"
        # ⚠️ 用 evidence 的键判断"哪一路召回了它"，而不是逐个读 vec_rank / sparse_rank。
        # 加一条召回分支时，这里一个字都不用改——新分支的名字会自动出现在
        # evidence 里，而 _BRANCH_RANK_FIELD 是唯一登记"分支名 ↔ 字段"的地方。
        flag = ""
        if "vec" not in ev:
            flag = "  ← 只稀疏命中"
        elif "sparse" not in ev:
            flag = "  ← 只向量命中"
        print(f"{final_rank[id(c)]:>4} {c.rrf:>8.5f} {v:>5} {k:>5} {b:>8}  {_where(c)}{flag}")

    reranked = sum(1 for c in cands if c.rerank_score is not None)
    print()
    if reranked == 0:
        print("⚠️ 全部没有精排分——重排模型没加载，这一列走的是 RRF 分数")
    both = sum(1 for c in cands if {"vec", "sparse"} <= c.evidence.keys())
    print(
        f"共 {len(cands)} 条候选：两路都命中 {both} 条，"
        f"只向量 {sum(1 for c in cands if 'vec' in c.evidence and 'sparse' not in c.evidence)} 条，"
        f"只稀疏 {sum(1 for c in cands if 'sparse' in c.evidence and 'vec' not in c.evidence)} 条"
    )


def _where(c) -> str:
    """出处：文档标题 + 章节路径。"""
    parts = [c.title, *[s for s in c.section if s and s != c.title]]
    return "《" + " > ".join(parts) + "》"


def _first_line(content: str) -> str:
    """取正文第一行有内容的（跳过面包屑那行）。

    面包屑行已经在 _where() 里展示过了，这里不重复。
    """
    for line in content.split("\n"):
        line = line.strip()
        if line and not line.startswith("【"):
            return line[:110]
    return ""


if __name__ == "__main__":
    sys.exit(main())
