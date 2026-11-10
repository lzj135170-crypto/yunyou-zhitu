"""检索质量评估：跑标注集，出指标，跨配置对比。

和 `eval_retrieval.py` 的分工：

    eval_retrieval.py   人肉看中间结果（哪条来自向量路、哪条来自稀疏路）
    eval_quality.py     自动打分（recall / MRR / nDCG），有断言，退出码 0/1

用法（在 backend 目录下执行）：

    # 跑默认配置
    .venv/Scripts/python.exe scripts/eval_quality.py

    # 列出所有没命中的用例，看错在哪
    .venv/Scripts/python.exe scripts/eval_quality.py --show-failures

    # 遍历 top_k 对比
    .venv/Scripts/python.exe scripts/eval_quality.py --sweep

    # 验证【指标本身】没算错（必做，见下）
    .venv/Scripts/python.exe scripts/eval_quality.py --self-check

═══════════════════════════════════════════════════════════════════════
 ⚠️ 这个脚本有一个绕不开的性质：默认配置下它【不是确定性的】
═══════════════════════════════════════════════════════════════════════

Query 改写要调一次 LLM，而 LLM 的输出每次都不一样。所以：

    --no-rewrite    纯检索，完全确定性。**自检和回归对比要用这个模式**
    默认（带改写）   更接近线上，但两次跑的指标会有小幅波动

要把"检索本身变差了"和"这次改写恰好差一点"分开，就得两个模式都看：
**如果 --no-rewrite 的指标没掉而带改写的掉了，问题多半在改写的提示词上，
不在检索层。**

═══════════════════════════════════════════════════════════════════════
 指标怎么算
═══════════════════════════════════════════════════════════════════════

标注的粒度是【文档 + 章节】，所以"命中"的判定是：检索回来的某个块，
它属于的文档和小节都对上了。

三个指标回答三个不同的问题：

    hit@k      有没有找到？            —— 召回能力
    MRR        第一个对的排第几？       —— 多久能找到
    nDCG@k     整体的排序质量如何？     —— 精排的意义就在这里

⚠️ NDCG 用【二元相关性 + 贪心去重】：一条检索结果只算一次命中，
不会因为"同一节被召回了好几个块"而重复加分。
不加这个去重的话，一个把 5 条全塞成同一节的检索器会拿到虚高的分数，
而那恰恰是这个项目已知的缺陷之一。
"""

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag import pipeline  # noqa: E402
from app.rag import policy as policy_mod  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"

# 默认用例集。⚠️ 它有第二个用途：**重构期间的闸门**。
# 里面的 86 条是"改造前就测过"的那一批，数字全部有基线可对，
# 所以它必须保持不动——想加题就加在 qa_hard.jsonl（--qa 指定）。
#
# 加题这件事本身踩过坑：往这里追加 18 条之后 `cases` 从 86 变 104，
# 而当时的对比逻辑不看用例数，拿 104 条的结果去比 86 条的基线，
# **静默错位**。现在有指纹了（见 _fingerprint），但"闸门不变动"这条仍然成立。
QA_PATH = EVAL_DIR / "qa_set.jsonl"

# 评分时每条文档最多贡献几个块。
# 用来量化"单篇占满 top-k"这个已知缺陷——和主指标对比就能看出它值不值得修。
MAX_PER_DOC = 2


@dataclass
class Config:
    """一次评估用的配置。"""

    top_k: int = 5
    rewrite: bool = True
    second_branch: str = policy_mod.SECOND_DEFAULT
    """第二路跑哪一条。⚠️ 默认值取自 `policy.SECOND_DEFAULT`，
    不在这里写死——两处默认值漂移的话，`--second-branch` 不传时评估跑的
    和生产的就不是一回事，而报告上看不出来。"""

    second_query: str = policy_mod.SECOND_QUERY_DEFAULT
    """第二路用原始输入还是改写后的查询。见 `policy.SECOND_QUERIES`。"""

    rerank_query: str = policy_mod.RERANK_QUERY_DEFAULT
    """精排用哪段文本和候选块拼对。见 `policy.RERANK_QUERIES`。"""

    def label(self) -> str:
        # ⚠️ 第二路写进 label，因为它是**这份报告是在哪个配置下产生的**的一部分。
        # 不写的话，"这次 nDCG 怎么和上次差了 0.01"要翻半天命令行历史。
        return (
            f"top_k={self.top_k} rewrite={'on' if self.rewrite else 'off'} "
            f"second={self.second_branch} second_query={self.second_query} "
            f"rerank_query={self.rerank_query}"
        )


@dataclass
class CaseResult:
    case: dict
    index: int
    retrieved: list[tuple[str, tuple[str, ...], float]]  # (file, section, score)
    rewritten: str = ""
    filters: dict = field(default_factory=dict)
    backend: str = ""
    branches: dict[str, list[tuple[str, tuple[str, ...]]]] = field(default_factory=dict)
    """每一路召回的【完整】结果，已解析成 (文件, 章节)。键是 "vec" / "sparse" / "trgm"。

    ⚠️ 和 `retrieved` 的区别是这一个：`retrieved` 是融合+精排**之后**留下的
    那 top_k 条；`branches` 是每一路**自己**看到的东西。前者回答"用户拿到什么"，
    后者回答"这一路值不值得留"。**两个问题的答案不一样，实测过。**
    """

    context_chars: int = 0
    """这次组装出来的上下文有多少字。

    单独记它是因为**排序指标看不见内容多少**：把 top_k 从 5 提到 8，
    recall 一定上升（多两个名额），但那多出来的资料值不值多花的 token，
    光看 recall 判断不了。有了这个数才能算"每多一个字买到多少召回"。
    """

    @property
    def kind(self) -> str:
        return self.case.get("kind", "?")

    @property
    def expected(self) -> list[tuple[str, tuple[str, ...]]]:
        return [
            (e["file"], tuple(e["section"])) for e in self.case.get("expect", [])
        ]


def main() -> int:
    args = _parse_args()

    if args.self_check:
        return _self_check()

    qa_path = Path(args.qa) if args.qa else QA_PATH
    if not qa_path.exists():
        print(f"❌ 用例集不存在：{qa_path}")
        return 1
    cases = _load_cases(qa_path)
    if not cases:
        print(f"❌ 没读到用例：{qa_path}")
        return 1

    if args.sweep:
        return _sweep(cases, args)

    cfg = Config(
        top_k=args.top_k,
        rewrite=not args.no_rewrite,
        second_branch=args.second_branch,
        second_query=args.second_query,
        rerank_query=args.rerank_query,
    )

    rewrite_cache = None
    if args.rewrite_cache:
        cache_path = Path(args.rewrite_cache)
        rewrite_cache = (
            json.loads(cache_path.read_text(encoding="utf-8"))
            if cache_path.exists()
            else {}
        )

    results = _run(cases, cfg, rewrite_cache=rewrite_cache)

    if args.rewrite_cache and rewrite_cache is not None:
        Path(args.rewrite_cache).write_text(
            json.dumps(rewrite_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    _report(results, cfg, args, qa_name=qa_path.name)
    if args.branches:
        _branches_report(results, cfg)

    # 退出码：只看"有没有崩"，不因为指标不达标就返回 1。
    # 理由：现在的基线是多少还没人知道，第一版就把阈值写死成"必须 ≥ 0.8"
    # 只会让人为了让它变绿而调阈值，而不是调检索。
    # 真正的回归保护是【把这次的数字记下来，下次跑出来比】——
    # 那件事由 --baseline 做。
    if args.save_baseline:
        _save_baseline(results, args, args.save_baseline)

    if args.baseline:
        _compare_baseline(
            results,
            args.baseline,
            deterministic=args.no_rewrite,
            k=args.top_k,
            second_branch=args.second_branch,
            second_query=args.second_query,
            rerank_query=args.rerank_query,
        )
    return 0


def _save_baseline(results: list[CaseResult], args: argparse.Namespace, path: str) -> None:
    """存一份基线，供以后 --baseline 比。

    ⚠️ 带改写模式存的基线【没有意义】，这里直接拒绝。

    Query 改写要调 LLM，同样的查询每次抽的过滤条件可能不同，
    指标会在几个点之间抖动。拿一个抖动的数当基线，
    下次跑出来的"退步"很可能只是抖动——而那种假警报会让人
    逐渐忽略这个机制，等真退步的时候就没人看了。

    要回归保护就跑 --no-rewrite，那个模式是完全确定性的
    （自检里的"连跑两次完全一致"验的就是它）。
    """
    if args.no_rewrite is False:
        print()
        print("⚠️ 拒绝保存基线：带 Query 改写的模式是非确定性的，")
        print("   它的指标不能当基准。加 --no-rewrite 再存一次。")
        return

    k = args.top_k
    data = {
        # 配置指纹（用例数 / top_k / 模式 / 语料块数 / 检索策略参数）。
        # 比对时逐项核对，对不上就拒绝对比——见 _fingerprint 的注释。
        **_fingerprint(
            results,
            k,
            rewrite=False,
            second_branch=args.second_branch,
            second_query=args.second_query,
            rerank_query=args.rerank_query,
        ),
        "recall": recall_at_k(results, k),
        "hit": hit_at_k(results, k),
        "mrr": mrr(results),
        "ndcg": ndcg_at_k(results, k),
        "recall_doc_cap": recall_with_doc_cap(results, k, MAX_PER_DOC),
    }
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print()
    print(f"基线已存到 {path}（--no-rewrite 模式，可复现）")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="检索质量评估")
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--no-rewrite", action="store_true", help="关掉 Query 改写（确定性模式）")
    p.add_argument("--show-failures", action="store_true", help="列出没命中的用例")
    p.add_argument(
        "--branches",
        action="store_true",
        help="按【召回来源】分层报告：每一路自己的命中率 + 候选生成层的上限。"
        "回答「这条召回路径值不值得留」和「该修召回还是该修排序」",
    )
    p.add_argument(
        "--sweep",
        action="store_true",
        help="遍历 RetrievalPolicy 的各种策略开关，输出对比表（见 SWEEPS）",
    )
    p.add_argument(
        "--qa",
        help=f"用例集路径（默认 {QA_PATH.name}）。"
        "eval/qa_hard.jsonl 是 18 条【同文档内兄弟章节】的难题："
        "期望的章节和它的邻居在结构上高度相似，用来压排序质量",
    )
    p.add_argument(
        "--second-branch",
        choices=list(policy_mod.SECOND_CHOICES),
        default=policy_mod.SECOND_DEFAULT,
        help="第二路跑哪一条（默认取 policy.SECOND_DEFAULT）。"
        "`none`（只跑向量路）是【已知答案测试】："
        "它的指标在 eval/RESULTS.md 里有记录，跑不出来说明别的地方被改坏了。"
        "见 eval/PHASE3_PREREG.md",
    )
    p.add_argument(
        "--second-query",
        choices=list(policy_mod.SECOND_QUERIES),
        default=policy_mod.SECOND_QUERY_DEFAULT,
        help="第二路用哪种查询文本：minimal（最小清洗，生产默认）/ raw（字节级原样）"
        "/ rewritten（改写后）。前两者在干净输入上相同，脏输入上 minimal 更稳。",
    )
    p.add_argument(
        "--rerank-query",
        choices=list(policy_mod.RERANK_QUERIES),
        default=policy_mod.RERANK_QUERY_DEFAULT,
        help="精排用哪段文本和候选块拼对（rewritten / raw / union）。",
    )
    p.add_argument(
        "--rewrite-cache",
        help="复用 Query 改写结果的 JSON 缓存。比较不同第二路配置时用它，"
        "让两边看到同一份 rewritten；第一次跑会填充，之后复用。",
    )
    p.add_argument("--self-check", action="store_true", help="验证指标函数本身")
    p.add_argument("--baseline", help="上次的指标 JSON 路径，对比是否回归")
    p.add_argument(
        "--save-baseline",
        help="把这次的指标存成 JSON，供以后的 --baseline 对比。"
        "⚠️ 只在【确定性的】--no-rewrite 模式下有意义，见下面 main 里的检查",
    )
    return p.parse_args()


def _load_cases(path: Path) -> list[dict]:
    """读一个 jsonl 用例集。

    ⚠️ 空行会跳过，坏行不会。一行 JSON 解析失败说明这个文件被改坏了，
    而"静默跳过坏行"的结果是**用例数少了几条、指标照常打印**——
    看起来像一次正常的评估，实际上少了几个样本。
    """
    cases = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"❌ {path.name} 第 {lineno} 行不是合法 JSON：{e}")
                sys.exit(1)
            bad = _case_error(case)
            if bad:
                print(f"❌ {path.name} 第 {lineno} 行：{bad}")
                sys.exit(1)
            cases.append(case)
    return cases


def _case_error(case: dict) -> str | None:
    """一条用例的格式检查。返回 None 表示没问题。

    ⚠️ 这里的检查是**故意的严格**：漏检的代价不是"报错"，而是"静默算错"。

    - 少了 `expect` 的用例会被判成"这条没命中"，看起来像检索退步了——
      而实际上是用例写坏了。所以除了 `unanswerable`（它的期望就是"什么都别召回"），
      其他 kind 必须有 `expect`。
    - `expect` 里的 `file` / `section` 要能对上真实的块键，否则永远是未命中。
      那个检查在 `_chunk_keys()` 上做更合适（它才知道有哪些键），
      这里只看形状。
    """
    if not case.get("query"):
        return "缺 query"
    kind = case.get("kind")
    if not kind:
        return "缺 kind（报告会按 kind 分组，也要用它判断该不该有 expect）"
    if kind == "unanswerable":
        return None
    expect = case.get("expect")
    if not expect:
        return f"kind={kind} 但没有 expect（只有 unanswerable 才允许没有）"
    for i, e in enumerate(expect):
        if "file" not in e or "section" not in e:
            return f"expect[{i}] 缺 file 或 section"
        if not isinstance(e["section"], list):
            # 字符串会在下面被 tuple() 切成一个个字符，得到一个永远不命中的键。
            # 这是 JSON 用例里最容易犯、又最难看出来的错。
            return f"expect[{i}].section 应该是数组，不是 {type(e['section']).__name__}"
    return None


def _run(
    cases: list[dict],
    cfg: Config,
    policy: "pipeline.RetrievalPolicy | None" = None,
    rewrite_cache: dict | None = None,
) -> list[CaseResult]:
    """跑一遍全部用例。

    ⚠️ 文档 id → 文件名的映射只查一次。
    每个用例都查一次的话，86 条 × 一次查询，慢而且没必要——
    这份映射在一次评估里不会变。

    policy 为 None 时按 `cfg` 构造策略——**注意是"按 cfg 构造"而不是"用默认值"**：
    `--second-branch` 就是这么生效的。默认的 cfg 构造出来的策略和
    `RetrievalPolicy()` 逐字段相同，所以不传这个参数时行为和以前完全一样。
    """
    if policy is None:
        policy = pipeline.RetrievalPolicy(
            second_branch=cfg.second_branch,
            second_query=cfg.second_query,
            rerank_query=cfg.rerank_query,
        )
    id2file = _document_files()
    chunk_keys = _chunk_keys()
    out: list[CaseResult] = []

    from app.rag import rewrite as rewrite_mod

    for i, case in enumerate(cases, 1):
        history = [tuple(h) for h in case.get("history", [])]

        # 改写缓存：让 sparse(rewritten) 和 bm25(raw) 两次评估看到**同一份**
        # rewritten。不带缓存的话，改写每次抽的结果不同，两次跑出来的差异
        # 说不清是"换了第二路"还是"改写抖了"。
        plan = None
        if cfg.rewrite and rewrite_cache is not None:
            key = case["query"] + "|" + json.dumps(
                case.get("history", []), ensure_ascii=False, sort_keys=True
            )
            cached = rewrite_cache.get(key)
            if cached is None:
                plan, _ = rewrite_mod.plan(case["query"], history or None)
                rewrite_cache[key] = {
                    "rewritten": plan.rewritten,
                    "filters": plan.filters.model_dump(),
                }
            else:
                plan = rewrite_mod.QueryPlan(
                    rewritten=cached["rewritten"],
                    filters=rewrite_mod._ExtractedFilters(**cached["filters"]),
                )

        outcome = pipeline.search(
            case["query"],
            top_k=cfg.top_k,
            history=history or None,
            rewrite_query=cfg.rewrite,
            policy=policy,
            plan=plan,
        )

        if outcome is None:
            # 向量库整条路不可用——这不是"检索效果差"，是环境问题，直接退出。
            # 继续跑下去会得到一整套全 0 的指标，看起来像"检索器坏了"。
            print("❌ 向量库不可用，评估无法进行。")
            print("   先确认容器：docker start travel-pgvector")
            sys.exit(1)

        retrieved = [
            (
                id2file.get(c.document_id, "?"),
                tuple(c.section),
                c.score,
            )
            for c in outcome.candidates
        ]
        out.append(
            CaseResult(
                case=case,
                index=i,
                retrieved=retrieved,
                rewritten=outcome.rewritten,
                filters=outcome.filters,
                backend=outcome.backend,
                context_chars=len(outcome.assembled.context),
                branches={
                    name: [chunk_keys[cid] for cid in ids if cid in chunk_keys]
                    for name, ids in (outcome.branches or {}).items()
                },
            )
        )
    return out


def _document_files() -> dict[int, str]:
    """document_id → 语料文件名。

    走业务库的 documents 表（meta.file 是入库时写进去的稳定标识），
    而不是去猜标题——标题会改，文件名是语料的真实身份。
    """
    from app.database import SessionLocal
    from app.models.document import Document

    db = SessionLocal()
    try:
        return {
            d.id: (d.meta or {}).get("file", "?")
            for d in db.query(Document).all()
        }
    finally:
        db.close()


def _chunk_keys() -> dict[int, tuple[str, tuple[str, ...]]]:
    """chunk_id → (语料文件名, 章节元组)。

    ⚠️ 只查一次，和 `_document_files()` 一样。130 块是一次顺手的事，
    但有了它 `branch_recall` 才算得出来——**而在此之前，"这一路到底召回了什么"
    只存在于一个一次性诊断脚本里**（`diagnose_branches.py`），
    要回答"第二路有没有用"得专门去跑那个脚本再人工比对。

    这正是"可解释性靠考古"的具体样子：数据一直在库里，但没有一个常规的
    出口把它变成指标。
    """
    from sqlalchemy import text

    from app.rag import vector_store

    id2file = _document_files()
    engine = vector_store.get_vector_engine()
    if engine is None:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, document_id, meta FROM document_chunks")
        ).fetchall()

    out: dict[int, tuple[str, tuple[str, ...]]] = {}
    for cid, doc_id, meta in rows:
        if isinstance(meta, str):
            meta = json.loads(meta or "{}")
        meta = meta or {}
        out[int(cid)] = (
            id2file.get(int(doc_id), "?"),
            tuple(meta.get("section") or ()),
        )
    return out


# ---------- 指标 ----------


def _match(entry: tuple[str, tuple[str, ...]], expected: tuple[str, tuple[str, ...]]) -> bool:
    """一个检索结果算不算命中了某条期望。"""
    return entry[0] == expected[0] and entry[1] == expected[1]


def branch_recall(results: list[CaseResult], branch: str, k: int) -> float | None:
    """**单条召回路径自己**的 top-k 命中率。

    ⚠️ 和 `recall_at_k` 的关键区别：后者算的是【融合+精排之后】的结果，
    它回答不了"这条路径值不值得留"——两路融合会把单路的贡献掩盖掉。
    实测踩过：`weight_second=0` 和等权在 `top_k=5` 上四项指标【逐位相同】，
    因为两路的候选池各占全语料 23%，融合后留下的东西几乎一样。

    这个函数直接问："这一路的 top-k 里，命中了多少正确答案"。
    """
    total = hit = 0
    for r in results:
        exp = r.expected
        if not exp:
            continue
        ids = r.branches.get(branch)
        if ids is None:
            # 这一路这次没跑（比如嵌入模型不可用时只有 trgm）。
            # 跳过而不是记 0——记 0 会把"没跑"和"跑了但没命中"混成同一件事。
            continue
        total += len(exp)
        hit += sum(1 for e in exp if any(_match(c, e) for c in ids[:k]))
    return hit / total if total else None


def oracle_recall(results: list[CaseResult]) -> float | None:
    """两路并集【不管排第几】能命中多少期望项 —— 召回层的上限。

    ⚠️ 这是判断"该修哪一层"的分水岭：

        并集里就没有      → 问题在【召回】。再好的精排也找不回来，
                            该动的是切块、嵌入或加召回路径
        并集里有、但没进 top-k → 问题在【排序】。该动的是精排或融合

    没有这个数的话，两种失败在最终指标上长得一模一样。
    """
    total = hit = 0
    for r in results:
        exp = r.expected
        if not exp:
            continue
        union = [c for ids in r.branches.values() for c in ids]
        if not union:
            continue
        total += len(exp)
        hit += sum(1 for e in exp if any(_match(c, e) for c in union))
    return hit / total if total else None


def recall_at_k(results: list[CaseResult], k: int) -> float | None:
    """期望条目里有多少被 top-k 覆盖到。只统计有期望的用例。"""
    total = hit = 0
    for r in results:
        exp = r.expected
        if not exp:
            continue
        total += len(exp)
        hit += sum(1 for e in exp if any(_match(c, e) for c in r.retrieved[:k]))
    return hit / total if total else None


def hit_at_k(results: list[CaseResult], k: int) -> float | None:
    """有多少用例【至少】命中一条期望。"""
    cases = [r for r in results if r.expected]
    if not cases:
        return None
    return sum(
        1 for r in cases if any(_match(c, e) for c in r.retrieved[:k] for e in r.expected)
    ) / len(cases)


def mrr(results: list[CaseResult]) -> float | None:
    """第一个命中的倒数排名。一条都没命中的用例记 0。"""
    cases = [r for r in results if r.expected]
    if not cases:
        return None
    total = 0.0
    for r in cases:
        for rank, c in enumerate(r.retrieved, 1):
            if any(_match(c, e) for e in r.expected):
                total += 1.0 / rank
                break
    return total / len(cases)


def ndcg_at_k(results: list[CaseResult], k: int) -> float | None:
    """二元相关性 + 贪心去重的 NDCG@k。

    ⚠️ 为什么必须去重：一个检索器如果把 5 条全填成同一节的块，
    不去重的话那 5 条都算相关，NDCG 会很高——而这恰恰是
    "单篇占满 top-k"那个已知缺陷。指标必须能惩罚它，而不是奖励它。

    做法是贪心分配：一条期望最多被一条检索结果"消耗"掉，
    消耗过之后同样的文档+章节再出现就不算相关了。
    """
    cases = [r for r in results if r.expected]
    if not cases:
        return None

    total = 0.0
    for r in cases:
        exp = list(r.expected)
        dcg = 0.0
        for rank, c in enumerate(r.retrieved[:k], 1):
            matched = next((e for e in exp if _match(c, e)), None)
            if matched is not None:
                exp.remove(matched)
                dcg += 1.0 / math.log2(rank + 1)

        # 理想排序：相关结果全排在最前面
        ideal = sum(
            1.0 / math.log2(i + 1) for i in range(1, min(len(r.expected), k) + 1)
        )
        total += dcg / ideal if ideal else 0.0
    return total / len(cases)


def recall_with_doc_cap(results: list[CaseResult], k: int, cap: int) -> float | None:
    """每篇文档最多只取 cap 块之后，再算一次覆盖率。

    和 recall@k 的差值就是这个已知缺陷的代价：
    差值大 = 单篇占满确实在挤掉别的文档，去重值得做；
    差值小 = 那个缺陷在这个语料上不严重，别为它加复杂度。
    """
    total = hit = 0
    for r in results:
        exp = r.expected
        if not exp:
            continue
        capped: list[tuple[str, tuple[str, ...], float]] = []
        seen: dict[str, int] = {}
        for entry in r.retrieved:
            n = seen.get(entry[0], 0)
            if n >= cap:
                continue
            seen[entry[0]] = n + 1
            capped.append(entry)
            if len(capped) >= k:
                break
        total += len(exp)
        hit += sum(1 for e in exp if any(_match(c, e) for c in capped))
    return hit / total if total else None


def score_separation(results: list[CaseResult]) -> dict[str, object] | None:
    """可答查询和不可答查询的分数分布差多少，以及各种阈值的得失。

    这是"能不能拒答"的前提。检索器没有"我不知道"这个状态——它永远返回
    top-k。所以对"上海有什么好玩的"这种问题，正确的做法不是让它返回空
    （做不到），而是**给一个分数阈值**：低于它就告诉用户没查到。

    ⚠️ 光看"最大值有没有重叠"不够。实测这两类的中位数差得很远
    （0.987 vs 0.050），但两边各有几条跑到对方的区间里去了——
    只报一句"有重叠"，读的人没法判断代价到底是 1 条还是 20 条。

    所以这里直接列出几档阈值各自的得失：**拒掉了多少不可答的（收益）
    和误伤了多少可答的（代价）**。收益和代价放在一起才做得成决定。
    """
    ans = [r.retrieved[0][2] for r in results if r.expected and r.retrieved]
    unans = [r.retrieved[0][2] for r in results if not r.expected and r.retrieved]
    if not ans or not unans:
        return None

    band = sorted(set(ans + unans))
    rows: list[tuple[float, int, int]] = []
    # 只在"两类样本都可能落在这里"的区间上试阈值，
    # 全 0 分或全满分的那些点是白试。
    for t in band:
        if not (min(unans) <= t <= max(ans)):
            continue
        rejected = sum(1 for s in unans if s < t)   # 正确拒掉的不可答（收益）
        hurt = sum(1 for s in ans if s < t)         # 被误伤的可答（代价）
        rows.append((round(t, 3), rejected, hurt))

    # ⚠️ 按【收益】去重，不按代价。
    #
    # 收益（拒掉几条不可答的）是台阶式的，只有 10 档左右；
    # 代价（误伤几条可答的）几乎每个阈值点都在变，按它去重会出几十行，
    # 一屏刷满反而看不出该选哪儿。
    # 收益是那个"整档变一次"的粗粒度信号，用它当行标更可读。
    picked: list[tuple[float, int, int]] = []
    last: int | None = None
    for row in rows:
        if row[1] != last:
            picked.append(row)
            last = row[1]
    # 最后一档补上：不然"阈值取到最高时误伤多少"这个上界看不见
    if rows and rows[-1] not in picked:
        picked.append(rows[-1])

    return {
        "answerable_median": _median(ans),
        "unanswerable_median": _median(unans),
        "unanswerable_max": max(unans),
        "answerable_min": min(ans),
        "n_answerable": len(ans),
        "n_unanswerable": len(unans),
        "thresholds": picked,
    }


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _exclusive_report(results: list[CaseResult], names: list[str]) -> None:
    """每一路【独家】召回了什么——它才是"这一路值不值得留"的直接答案。

    ## 为什么 branch_recall 还不够

    `branch_recall@30` 回答的是"这一路自己找得到多少答案"。两路的这个数
    可以都很高，而**其中一路完全不产生新信息**——因为它们的候选池大面积重合
    （每路 30 条占全语料 23%）。

    真正决定"这一路该不该留"的是：**去掉它，候选池会少什么。**
    所以这里数的是"只在某一路里出现、另一路没有"的候选。

    ## ⚠️ 它曾经把一个巧合包装成了收益

    实测（Phase 3 的头对头）：BM25 在 `hit@8` 上比"只跑向量路"高出一条用例，
    而用这个报告一查——**BM25 一条包含答案的独家候选都没有**（0/86）。

    那条用例的真相是：正确答案本来就在向量路的候选池里、精排给它 0.768212 分，
    排第 9。BM25 的独家候选**挤掉了池子里两个分数更高的干扰项**
    （0.7842 / 0.7803），于是它被动地从第 9 升到第 7 进了 top-8。

    也就是说那一分不是"BM25 找到了什么"，是"BM25 把对手赶走了"。
    两种情况在 `hit@8` 上长得一模一样。

    ⚠️ 所以这三列要**一起**读：独家候选很多、但"含答案的独家"是 0，
    说明这一路的贡献全在**扰动排序**上，不在提供信息上。扰动有时候恰好帮上忙，
    但它是运气，不是能力——换一份语料就不一定了。
    """
    print()
    print("  独家贡献：去掉这一路，候选池会少什么")
    print()
    header = f"  {'分支':<8}{'独家候选/用例':>14}{'一条独家都没有的用例':>22}{'独家候选含答案的用例':>22}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for name in names:
        others = [n for n in names if n != name]
        total, none_at_all, with_gold, answerable = 0, 0, 0, 0
        for r in results:
            if name not in r.branches:
                continue
            mine = set(r.branches[name])
            rest: set = set()
            for other in others:
                rest |= set(r.branches.get(other, []))
            only = mine - rest
            total += len(only)
            if not only:
                none_at_all += 1
            if r.expected:
                answerable += 1
                if set(r.expected) & only:
                    with_gold += 1
        n = sum(1 for r in results if name in r.branches) or 1
        print(
            f"  {name:<8}{total / n:>14.1f}{f'{none_at_all} / {n}':>22}"
            f"{f'{with_gold} / {answerable}':>22}"
        )

    print()
    print("  读法：中间那一列越低越好（说明这一路总归带了点新东西）。")
    print("        最后一列是唯一能证明「这一路不可替代」的数——")
    print("        它是 0 的话，最高的 branch_recall 也不说明它提供了新信息。")


# ---------- 报告 ----------


def _branches_report(results: list[CaseResult], cfg: Config) -> None:
    """按【召回来源】分层报告。

    ⚠️ 这一节的存在本身就是在修一个结构性问题：在这之前，"BM25 到底有没有用"
    要靠一个一次性脚本（`scripts/diagnose_branches.py`）去跑、再人工比对候选集。
    **可解释性不该靠考古**——数据一直在 `Hit` 和 `SearchOutcome.branches`
    里躺着，只是没有人把它变成指标。

    现在它是常规出口：改任何召回相关的东西之后，跑一次就能看到每一路各自的成绩。
    """
    have = [r for r in results if r.branches]
    if not have:
        print()
        print("归因：这次没有双路召回数据（降级路是单路标题匹配，没有分支概念）")
        return

    names = sorted({n for r in have for n in r.branches})
    n_max = max((len(ids) for r in have for ids in r.branches.values()), default=0)
    ks = [k for k in (5, 10, 30) if k <= n_max] or [n_max]

    oracle = oracle_recall(have)
    final = recall_at_k(results, cfg.top_k)

    print()
    print("归因：每一路【自己】的成绩（和最终指标是两回事）")
    print()
    print(f"  {'候选生成层的上限（两路并集，不看排第几）':<40} {_fmt(oracle):>8}")
    print(f"  {f'融合+精排之后的最终 recall@{cfg.top_k}':<40} {_fmt(final):>8}")
    if oracle is not None and final is not None:
        missed = 1.0 - oracle
        ranked_away = oracle - final
        print(f"  {'├ 并集里就没有 → 问题在【召回】':<40} {missed:>8.4f}")
        print(f"  {f'└ 并集里有但没进 top-{cfg.top_k} → 问题在【排序】':<40} {ranked_away:>8.4f}")
    print()
    header = f"  {'分支':<8}" + "".join(f"{'top-' + str(k):>10}" for k in ks)
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name in names:
        row = f"  {name:<8}"
        for k in ks:
            row += f"{_fmt(branch_recall(results, name, k)):>10}"
        print(row)
    print()
    print("  读法：某一路的 top-N 越接近上限，说明它越能【独立】找到答案；")
    print("        两路的 top-N 都明显低于上限，说明它们在互补。")

    _exclusive_report(have, names)

    # ⚠️ 这一行是整个报告里最该先看的数。候选池占全语料的比例越大，
    # 两路看到的东西越像，"哪个分支更好"就越测不出来——实测踩过：
    # 每路 30 条占全语料 23% 时，`weight_second=0` 和等权在 top_k=5 上逐位相同。
    from app.rag import vector_store

    total_chunks = vector_store.corpus_size()
    if total_chunks:
        print()
        print(
            f"  ⚠️ 每路召回 {n_max} 条 ÷ 全语料 {total_chunks} 块 = "
            f"{n_max / total_chunks:.1%}"
        )
        if n_max / total_chunks > 0.15:
            print("     比例偏高：两路看到的东西太像，上面的分支对比分辨率有限。")
            print("     判断标准应该是比例不是条数，见 app/rag/policy.py 的 CAND_FRACTION。")


def _report(
    results: list[CaseResult],
    cfg: Config,
    args: argparse.Namespace,
    qa_name: str = QA_PATH.name,
) -> None:
    k = cfg.top_k
    # ⚠️ 用例集名字必须打出来。两套用例的指标水平差得很远（难题集就是设计成
    # 掉分的），只看数字分不清"检索退步了"和"这次跑的是另一套题"。
    print(f"用例集：{qa_name}    配置：{cfg.label()}    用例 {len(results)} 条")
    print()

    r = recall_at_k(results, k)
    h = hit_at_k(results, k)
    m = mrr(results)
    n = ndcg_at_k(results, k)
    rd = recall_with_doc_cap(results, k, MAX_PER_DOC)

    print(f"{'指标':<34} {'值':>8}")
    print("-" * 44)
    print(f"{'recall@' + str(k):<34} {_fmt(r):>8}")
    print(f"{'hit@' + str(k):<34} {_fmt(h):>8}")
    print(f"{'MRR':<34} {_fmt(m):>8}")
    print(f"{'nDCG@' + str(k):<34} {_fmt(n):>8}")
    print(f"{'recall@' + str(k) + f'（每篇最多 {MAX_PER_DOC} 块）':<34} {_fmt(rd):>8}")
    if r is not None and rd is not None:
        print(f"{'  ↑ 两者之差 = 单篇占满的代价':<34} {r - rd:>8.4f}")

    sep = score_separation(results)
    if sep:
        print()
        print("分数分离度（决定「拒答阈值」能不能设）：")
        print(
            f"  可答查询 {sep['n_answerable']} 条，top-1 中位分 {sep['answerable_median']:.4f}"
        )
        print(
            f"  不可答查询 {sep['n_unanswerable']} 条，top-1 中位分 {sep['unanswerable_median']:.4f}"
        )
        print()
        print(f"  {'阈值':>8} {'拒掉不可答(收益)':>16} {'误伤可答(代价)':>16}")
        print("  " + "-" * 44)
        for t, rejected, hurt in sep["thresholds"]:
            print(
                f"  {t:>8.3f} {rejected:>8} / {sep['n_unanswerable']:<7} "
                f"{hurt:>8} / {sep['n_answerable']:<7}"
            )
        if sep["unanswerable_max"] < sep["answerable_min"]:
            print()
            print("  ✅ 两类完全不重叠，随便挑一个中间值都行")
        else:
            print()
            print("  ↑ 收益和代价在同一张表上，自己权衡。")
            print("    注意：代价那一边更值钱——把能答的问题判成'查不到'，")
            print("    用户会觉得这个功能是坏的；把答不了的硬答一句，用户至少")
            print("    能从内容看出不对。所以阈值应该往【低】取。")

    print()
    print("按类型分：")
    kinds: dict[str, list[CaseResult]] = {}
    for r_ in results:
        kinds.setdefault(r_.kind, []).append(r_)
    for kind, rs in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        hh = hit_at_k(rs, k)
        nn = ndcg_at_k(rs, k)
        print(f"  {kind:<14} {len(rs):>2} 条   hit={_fmt(hh)}  nDCG={_fmt(nn)}")

    if args.show_failures:
        _show_failures(results, k)


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.4f}"


def _show_failures(results: list[CaseResult], k: int) -> None:
    fails = [
        r
        for r in results
        if r.expected and not any(_match(c, e) for c in r.retrieved[:k] for e in r.expected)
    ]
    print()
    print(f"=== 没命中的用例（{len(fails)} 条）===")
    for r in fails:
        exp = r.expected
        # 提前拼好，不写在 f-string 里嵌套——Python 3.11 的 f-string
        # 表达式部分不允许出现反斜杠，`{' > '.join(s)}` 这种嵌在
        # 另一个 f-string 里再加转义引号就编不过。
        want = ", ".join(f"{f} > {' > '.join(s)}" for f, s in exp)
        print()
        print(f"[{r.index}] {r.case['query']}   ({r.kind})")
        if r.rewritten != r.case["query"]:
            print(f"     改写: {r.rewritten}")
        print(f"     期望: {want}")
        print("     实际:")
        for f, s, sc in r.retrieved[:3]:
            got = f"{f} > {' > '.join(s)}"
            print(f"       {sc:.3f}  {got}")


# ---------- sweep 与自检 ----------


# sweep 要回答的问题，以及对应的配置。
#
# ⚠️ 这不是"列出所有能调的参数"，而是**几个可能推翻现有设计的验证**：
#
#     关掉精排        精排花掉的算力（每轮 3 秒 + 1.2GB 显存）值不值
#     weight_second=0 第二路有没有贡献？没有就该砍掉一整套设施
#                     （Phase 3 之前这一项叫 `w_bm25`，RESULTS.md 里的老表用的
#                      是老名字，说的是同一项）
#     w_vec=0         反过来的那一半
#     rrf_k 变一变     这个参数敏不敏感（预期：不敏感，因为它只是候选生成器）
#
# 每一项的结论都要写进 eval/RESULTS.md，包括"试过但没用"的那些。
SWEEPS: list[tuple[str, pipeline.RetrievalPolicy]] = [
    ("__baseline__", pipeline.RetrievalPolicy()),
    ("关掉精排", pipeline.RetrievalPolicy(rerank=False)),
    ("只用向量路 (weight_second=0)", pipeline.RetrievalPolicy(weight_second=0)),
    ("只用第二路 (w_vec=0)", pipeline.RetrievalPolicy(weight_vec=0)),
    ("rrf_k=10", pipeline.RetrievalPolicy(rrf_k=10)),
    ("rrf_k=100", pipeline.RetrievalPolicy(rrf_k=100)),
    # ↓ 这一组不是在"调优"，是在**改尺子的分辨率**。
    #
    # 实测踩到过：`weight_second=0` 和等权在 top_k=5 上四项指标逐位相同，
    # 在 top_k=8 上却差一整条用例——同一份语料给出相反结论。
    # 怀疑根因是每路召回 30/130 = 23%，两个方法看到的东西太像。
    #
    # 这一组就是去验证：**把池子缩小之后，"有没有关键词路"是不是才开始有区别。**
    # 最后一行是决定性的——它要和上面"每路 8 条"那行比，而不是和基线比。
    ("每路召回 15 条", pipeline.RetrievalPolicy(cand_per_branch=15, cand_total=15)),
    ("每路召回 8 条", pipeline.RetrievalPolicy(cand_per_branch=8, cand_total=8)),
    (
        "每路 8 条 + 只用向量路",
        pipeline.RetrievalPolicy(cand_per_branch=8, cand_total=8, weight_second=0),
    ),
    # ↓ Phase 3：换第二路的头对头。**判据在 eval/PHASE3_PREREG.md 里，先写的。**
    #
    # 第一行是**已知答案测试**：它等价于"weight_second=0"，而那一行在
    # RESULTS.md 第 5 节里有实测记录（0.9756 / 0.9868 / 0.8926 @ top_k=8）。
    # 跑不出那组数就说明前面某个 Phase 偷偷改了行为，回去查，不要解释。
    #
    # 之所以两种写法（关掉权重 / 不跑这一路）结果应当相同，是因为第二路权重为 0 时
    # 它独有的候选 RRF 是 0，而向量路的候选分数都 > 0，候选池又正好被填满
    # ——那些 0 分候选全落在截断之外。**这个论断只在向量路返回满 30 条时成立**，
    # 所以它是论断不是恒等式。见 PHASE3_PREREG.md 第一节。
    ("第二路 = none（已知答案测试）", pipeline.RetrievalPolicy(second_branch="none")),
]

# 单独一张表，因为它的指标口径和上面不同（上面固定 @5，这里要用各自的 k）。
#
# ⚠️ 这张表**曾经把默认值从 5 改成了 8**（见 eval/RESULTS.md 第 6 节）。
# 决定性的一行是 hit@8 = 1.0000——hit@5 = 0.9868 意味着 76 条可答用例里
# 恰好有 1 条完全检索不到任何相关内容，用户会拿到一个没有资料支撑的回答。
#
# 它同时也证明过另一条路走不通：同样约 1900 字的预算，取 8 条全面好过
# 取 5 条再把每块撑成整个章节（"父章节扩展"）。那个开关已经删掉，
# 结论留在 RESULTS.md；现在多取块加进来的是**另一条**相关内容。
#
# 所以这张表的用途不只是"看 k 该取几"——它同时是"上下文预算该怎么花"的判据。
TOP_K_SWEEP = (3, 5, 8, 12)


def _sweep(cases: list[dict], args: argparse.Namespace) -> int:
    """遍历检索策略，输出对比表。

    ⚠️ 策略那几行跑在 --no-rewrite 下，基线那两行都跑。

    理由：策略对比要的是"这个开关本身有没有用"，而 Query 改写是
    非确定性的（每次抽的过滤条件可能不同），混进来就分不清差异是
    开关造成的还是改写抖动造成的。86 条 × 10 个配置 = 860 次改写调用
    也太慢了。

    但**基线必须两种模式都给**：只看 --no-rewrite 的基线会低估绝对值
    （丢掉改写带来的那部分收益），于是策略看起来都比实际好。
    所以第一行（带改写，真实默认）和第二行（不带改写，策略行的对照基准）
    都在。
    """
    # ⚠️ 表头和指标都跟着 args.top_k 走，不写死 5。
    #
    # 写死的话这份表就【只在一个 k 上成立】，而 k 是会变的（它本身就是一个被测
    # 出来的旋钮）。生产值从 5 改成 8 之后，"等权融合 = 纯向量"这类结论必须在
    # 新 k 上重新量——top-8 和 top-5 是不同的集合，从旧结果推新结论不成立。
    #
    # 更糟的是写死会造成一种【看不出来的错位】：检索取 8 条、指标却只算前 5 条，
    # 输出的数字看起来完全正常，但它回答的是另一个问题。
    k = args.top_k
    header = f"{'配置':<26} {'recall@' + str(k):>9} {'hit@' + str(k):>8} {'MRR':>8} {'nDCG@' + str(k):>8}"

    print("═" * len(header))
    print("基线（这两种模式的差别本身就说明改写值多少）")
    print("═" * len(header))
    print(header)
    print("-" * len(header))
    for label, rw in (("当前默认（带改写）", True), ("不带改写（策略行基准）", False)):
        rs = _run(cases, Config(top_k=k, rewrite=rw))
        print(
            f"{label:<26} {_fmt(recall_at_k(rs, k)):>9} {_fmt(hit_at_k(rs, k)):>8} "
            f"{_fmt(mrr(rs)):>8} {_fmt(ndcg_at_k(rs, k)):>8}"
        )

    print()
    print("═" * len(header))
    print(f"策略对比（都在 --no-rewrite 下，top_k={k}，和不带改写那行比）")
    print("═" * len(header))
    print(header)
    print("-" * len(header))
    for label, policy in SWEEPS:
        if label == "__baseline__":
            continue
        rs = _run(cases, Config(top_k=k, rewrite=False), policy)
        print(
            f"{label:<26} {_fmt(recall_at_k(rs, k)):>9} {_fmt(hit_at_k(rs, k)):>8} "
            f"{_fmt(mrr(rs)):>8} {_fmt(ndcg_at_k(rs, k)):>8}"
        )

    print()
    print("═" * len(header))
    print("top_k 对比（--no-rewrite，指标用各自的 k）")
    print("═" * len(header))
    print(
        f"{'top_k':<26} {'recall@k':>9} {'hit@k':>8} {'MRR':>8} {'nDCG@k':>8} "
        f"{'平均上下文字数':>12}"
    )
    print("-" * (len(header) + 14))
    for k in TOP_K_SWEEP:
        rs = _run(cases, Config(top_k=k, rewrite=False))
        avg_chars = sum(r.context_chars for r in rs) / len(rs)
        print(
            f"{f'取前 {k} 条':<26} {_fmt(recall_at_k(rs, k)):>9} "
            f"{_fmt(hit_at_k(rs, k)):>8} {_fmt(mrr(rs)):>8} {_fmt(ndcg_at_k(rs, k)):>8} "
            f"{avg_chars:>12.0f}"
        )

    print()
    print("怎么读这两张表：")
    print("  · 某一行的 nDCG 和「不带改写」那行差不多 → 这个组件【没在起作用】，")
    print("    该查它是不是接错了，或者干脆砍掉（省一次推理、省一整套依赖）")
    print("  · 明显更低 → 它有用")
    print("  · 明显更高 → 该把它设成默认")
    print()
    print("  ⚠️ top_k 那一张表的 recall 是【单调上升】的，那是名额变多的必然结果，")
    print("     不是质量变好。要看的是【每多一条买到多少召回】，以及最后一列")
    print("     的涨幅——那一列直接就是 token 成本。")
    print("     如果从 5 到 8 只多买到 0.01 的召回却多花 40% 的上下文，")
    print("     那这个余量就不该用掉。")
    return 0


def _self_check() -> int:
    """验证指标函数本身没算错。**这个检查比任何指标数字都重要。**

    一个算错的指标会给出**看起来客观的数字**，而所有人都会信它。
    最典型的一种错误是"对排序不敏感"：比如 nDCG 里忘了除以 log2(rank+1)，
    那样不管结果怎么排分数都差不多，于是"精排有没有用"这个问题
    永远得不到真答案——而精排恰恰是这个项目花最大代价做的部分。

    ⚠️ 检查必须用【噪音的位置】来构造，不能用"把正确结果倒过来排"。

    这一条是踩出来的：二元相关性下，如果返回的几条**全都是相关的**，
    把它们倒过来排，DCG 一模一样（每条 rel 都是 1，谁在前谁在后
    只是把相同的一组权重换个顺序加到相同的一组项上）。
    所以"完美 vs 倒序"这个对比**验证不了任何东西**——
    它永远通过，包括在指标真的写错的时候。

    真正有区分度的是：相关结果排在噪音前面 vs 排在噪音后面。
    """
    print("=== 指标自检 ===")
    print()

    # 3 条期望；检索返回 5 条，其中 2 条是噪音（用 9 表示）
    exp = [("f.md", ("A",)), ("f.md", ("B",)), ("f.md", ("C",))]

    def make(order: list[int]) -> list[CaseResult]:
        retrieved = []
        for i, which in enumerate(order):
            entry = exp[which] if which < 3 else ("f.md", ("噪音",))
            retrieved.append((entry[0], entry[1], 1.0 - i * 0.1))
        return [
            CaseResult(
                case={
                    "query": "q",
                    "expect": [{"file": f, "section": list(s)} for f, s in exp],
                    "kind": "self",
                },
                index=1,
                retrieved=retrieved,
            )
        ] * 3

    # 相关结果在前（第 1/2/3 名） vs 在噪音之后（第 3/4/5 名）
    good = make([0, 1, 2, 9, 9])
    bad = make([9, 9, 0, 1, 2])

    # 同一节被反复召回：不该因为"条数多"而拿到高分
    crammed = make([0, 0, 0, 0, 0])
    diverse = make([0, 1, 2, 9, 9])

    ok = True

    g, b = ndcg_at_k(good, 5), ndcg_at_k(bad, 5)
    gm, bm = mrr(good), mrr(bad)
    print("  检查 1：相关结果排前面 vs 排在噪音后面")
    print(f"    nDCG: {g:.4f} vs {b:.4f}   差 {g - b:+.4f}")
    print(f"    MRR : {gm:.4f} vs {bm:.4f}   差 {gm - bm:+.4f}")
    if g - b < 0.2:
        print("    ❌ nDCG 对排序不敏感 —— 指标算错了")
        ok = False
    if gm - bm < 0.2:
        print("    ❌ MRR 对排序不敏感 —— 指标算错了")
        ok = False

    c, d = ndcg_at_k(crammed, 5), ndcg_at_k(diverse, 5)
    print()
    print("  检查 2：同一节反复召回 vs 三条不同小节（贪心去重有没有生效）")
    print(f"    nDCG: 全同一节 {c:.4f} vs 三条不同 {d:.4f}")
    if c > d + 0.05:
        print("    ❌ 重复召回拿到了更高分 —— 去重没生效，指标会奖励'单篇占满'")
        ok = False

    if not _check_backend_labels():
        ok = False

    if not _check_rank_base():
        ok = False

    if not _check_finding_shape():
        ok = False

    if not _check_branch_mapping():
        ok = False

    if not _check_refusal_rule():
        ok = False

    print()
    print("✅ 指标函数正常" if ok else "❌ 指标函数有问题，先修它再看任何数字")
    return 0 if ok else 1


def _check_finding_shape() -> bool:
    """检查 Hit 上的字段有没有"没人想过它去哪"的。

    ⚠️ 这个检查防的是**"四种形状"那类债务重新长出来**。

    检索层改造前，同一个命中对象有四种形状、四处手写映射：
    加一个字段要决定三遍，漏一遍**不报错**——只是那个信息在某个出口上
    不存在了。等有人依赖了"接口里有、图里没有"这个不一致时才发现。

    现在只有两个出口（`to_finding_chunk` / `to_api_hit`），
    加字段时如果两个函数都不带它，它就会**静默地只在一半的地方存在**。
    所以断言：`Hit` 的每一个字段，要么出现在某个投影里，
    要么在 `types._INTERNAL_FIELDS` 里写明为什么不给。

    两条路都要求你想过一次——这就是全部目的，它不检查你想得对不对。
    """
    from app.rag import types

    print()
    print("  检查 5：Hit 的每个字段都有明确的去向（被投影、或登记为内部字段）")
    leftover = types.unprojected_fields()
    if leftover:
        print(f"    ❌ 这些字段没有去向：{leftover}")
        print("       二选一：加进 types.to_finding_chunk / to_api_hit，")
        print("       或者加进 types._INTERNAL_FIELDS 并写清为什么不给")
        return False

    n = len(__import__("dataclasses").fields(types.Hit))
    print(f"    {n} 个字段全部有去向（投影 {len(types._sample_hit_projection())} 个，"
          f"登记为内部 {len(types._INTERNAL_FIELDS)} 个）")
    return True


def _check_backend_labels() -> bool:
    """检查「机器串 → 人话」的映射表覆盖了每一个【合法的】backend 取值。

    ⚠️ 这个检查看着琐碎，防的是一种很难发现的失效。

    `retriever._describe()` 把 backend 的机器串翻成人话，
    翻不到时【原样返回机器串】——这个 fallback 是刻意写的
    （编一句人话比给个陌生标识符更危险，见那个函数的注释）。

    但后果是：**新增一条降级路径时，note 会静默变成 "检索（pgvector+sparse+xxx）"
    这种东西**。它不报错、不影响检索、只是那句给用户看的说明变得没法读，
    而没有任何测试会红。

    ═══════════════════════════════════════════════════════════════════
     ⚠️ 取值集合从 `policy.BACKENDS` 来，**不再手写**。
    ═══════════════════════════════════════════════════════════════════

    这个检查原来是这样写的：

        produced = {"keyword-fallback", "empty-query"}   # 手写的两个字面量

    那是同一个坑的另一种形式：**新增一个 backend 取值时，这个检查看不到它**，
    于是"检查全绿"和"说明书缺一页"可以同时成立。
    自己手写一份集合来检查"另一处的集合有没有漏"，等于没检查——
    真正需要的是那个集合只存在一份，然后两边都去读它。

    搬过去之后还多出一件事可查：`_backend()` 的两个布尔组合出来的四个值
    必须**真的落在** `BACKENDS` 里。原来没这个约束，所以
    `_backend()` 返回一个词表外的写法是可以编译、可以跑、可以过检查的。
    """
    from app.agents import retriever
    from app.rag import policy

    # 1. policy 自己的两份字面量不能分叉（BACKENDS 里那份和 retriever 的常量）
    ok = True
    if retriever.BACKEND_FALLBACK != "keyword-fallback":
        print()
        print("    ❌ policy.BACKENDS 和 retriever.BACKEND_FALLBACK 对不上了：")
        print(f"       retriever.BACKEND_FALLBACK = {retriever.BACKEND_FALLBACK!r}")
        print("       两边必须一致，否则词表里那个 'keyword-fallback' 是死的")
        ok = False

    # 2. _backend() 的产出必须落在词表里
    #
    # ⚠️ 这里是**穷举所有组合**而不是只跑两个布尔：Phase 3 之后第二路是三选一的
    # （sparse / 不跑 / trgm 顶替），组合比加分支之前多得多。
    # 只跑布尔的话 `pgvector+sparse` 这一整类取值**从来不会被构造出来**，
    # 于是"词表里写着、_describe 里没写"这件事就检查不到。
    from_combos = {
        pipeline._backend(list(combo), reranked=r)
        for combo in policy.BRANCH_COMBOS
        for r in (False, True)
    }
    stray = sorted(from_combos - policy.BACKENDS)
    print()
    print(f"  检查 3：backend 取值（{len(policy.BACKENDS)} 个）是否都有人话说明")
    if stray:
        print(f"    ❌ _backend() 产出了词表外的值：{stray}")
        print("       加进 app/rag/policy.py 的 BACKENDS")
        ok = False

    # 3. 每一个合法取值都要有人话
    missing = [
        b for b in sorted(policy.BACKENDS) if retriever._describe(b).startswith("检索（")
    ]
    if missing:
        print(f"    ❌ 这几个没有对应的人话：{missing}")
        print("       在 app/agents/retriever.py 的 _describe() 表里补上")
        ok = False

    if ok:
        print(f"    全部 {len(policy.BACKENDS)} 个都有：{sorted(policy.BACKENDS)}")
    return ok


def _check_rank_base() -> bool:
    """检查分支名次确实【从 1 开始】——而这件事只写在注释里是不够的。

    ⚠️ 为什么要有这条断言：同一个概念在检索层里有**两套基**。
    `Hit.vec_rank` 是 1 起（`_fuse` 里 `i + 1`），而
    `SearchOutcome.branches` 是一个 list，位置天然 0 起。

    今天没出 bug 是因为没有人把两者相减。但 `if c.vec_rank` 这种写法
    只在 1 起时成立——哪天有人"顺手"把 `_fuse` 改成 `i`（0 起），
    全项目所有真值判断都会把**第 1 名**当成"没召回"，
    而且不报错，只是名次统计凭空少一条。

    这条断言**实际调用** `_fuse` 去看它返回什么，而不是检查常量。
    这也正是 `types.py` 里刻意不放一个 `RANK_BASE = 1` 常量的原因：
    那种常量没有任何代码会用它，改它也不会改变行为——
    它给人"改了它就能改行为"的错觉，却不产生任何约束。
    """
    from app.rag import policy

    print()
    print("  检查 4：分支名次是不是从 1 开始（1 起还是 0 起，全项目只认一种）")
    # 造两路假数据：chunk 101 在向量路第 1 名，chunk 102 在第二路第 1 名。
    fused = pipeline._fuse(
        [
            pipeline.BranchRecall(policy.BRANCH_VEC, [(101, 0.9)]),
            pipeline.BranchRecall(policy.BRANCH_SPARSE, [(102, 5.0)]),
        ],
        k=60,
        total=10,
    )
    ranks = {f.chunk_id: f.ranks for f in fused}

    problems = []
    if ranks.get(101, {}).get(policy.BRANCH_VEC) != 1:
        problems.append(f"向量路第 1 名的 chunk 101 拿到 {ranks.get(101)}")
    if ranks.get(102, {}).get(policy.BRANCH_SPARSE) != 1:
        problems.append(f"第二路第 1 名的 chunk 102 拿到 {ranks.get(102)}")

    if problems:
        print("    ❌ 名次不是从 1 开始：")
        for p in problems:
            print(f"       {p}")
        print("       改 `_fuse` 很容易，改全项目所有 `if rank` 的真值判断不容易——")
        print("       要么把 _fuse 改回去，要么把所有真值判断改成 `is not None`。")
        return False

    print(f"    两路各自第 1 名的名次都是 1：{ranks}")
    return True


def _check_refusal_rule() -> bool:
    """检查拒答判定那三条规则——`pipeline._has_material`。

    ⚠️ 这个函数现在**参与线上行为**：它为 False 时 `context` / `chunks`
    会被清空，模型就看不到资料了。而它坏掉的方式全都很安静：

        · 永远返回 True  → 拒答功能不存在，但一切看起来正常
        · 永远返回 False → 整个知识库看起来是空的
        · 无精排时也套阈值 → 随机误伤（RRF 分数不含相关性信息）

    所以这里把四条规则各喂一组数据，逐条断言。
    """
    from app.rag import pipeline, policy, types

    print()
    print("  检查 7：拒答判定（三条规则，每条都影响线上行为）")

    def hit(score: float) -> "types.Hit":
        return types.Hit(
            chunk_id=1, document_id=1, chunk_index=0, title="t", source=None,
            section=(), meta={}, content_hash="", content="x",
            score=score, rrf=0.0, vec_rank=1, sparse_rank=None,
            sparse_score=None, bm25_rank=None, bm25_score=None,
            trgm_rank=None, trgm_score=None, rerank_score=score,
        )

    problems = []
    t = policy.REFUSAL_THRESHOLD

    if pipeline._has_material([], reranked=True):
        problems.append("一条候选都没有时返回了 True")
    # ⚠️ fail-open：无精排时 score 是归一化 RRF（只由名次决定），
    # 拿阈值卡它等于随机误伤。所以哪怕分数低到 0.001 也必须放行。
    if not pipeline._has_material([hit(0.001)], reranked=False):
        problems.append("无精排时也套了阈值——RRF 分数不含相关性信息，这会随机误伤")
    if pipeline._has_material([hit(t - 0.001)], reranked=True):
        problems.append(f"有精排且 top-1 低于阈值 {t} 时仍返回 True")
    # 边界：正好等于阈值算「够格」，否则阈值本身那个点会漏掉
    if not pipeline._has_material([hit(t)], reranked=True):
        problems.append(f"top-1 正好等于阈值 {t} 时被判成没资料（边界应该算够格）")
    # 只看第一名：第二名再低也不影响"有一条能用的资料"这个事实
    if not pipeline._has_material([hit(0.9), hit(0.001)], reranked=True):
        problems.append("top-1 够高、后面很低时被判成没资料（应该只看第一名）")

    if problems:
        print("    ❌ 拒答判定有错：")
        for p in problems:
            print(f"       · {p}")
        return False

    print(f"    四条规则都对（阈值 {t}；无精排 fail-open；只看 top-1；空结果判否）")
    return True


def _check_branch_mapping() -> bool:
    """检查每个分支名都有对应的 `Hit` 字段。

    ⚠️ 防的是**"加一路召回，但忘记改字段表"**。

    `types._BRANCH_RANK_FIELD` 是"分支名 → Hit 字段名"的唯一映射
    （Phase 2 之前这个关系散在六处，每加一路要改六遍）。收敛成一个 dict
    之后，改动量从六处变成一处——但**"忘记改"这个动作本身还在**：

        `_fuse` 拿到 `"sparse"` 的候选 → `_branch_fields` 查表 → 查不到
        → `.get()` 返回 None → **跳过**

    结果是：检索照常工作、指标照常算、`SearchOutcome.branches["sparse"]`
    照常有值，只有 `Hit.sparse_rank` 和 `Hit.evidence` 是空的——
    而那正是"这条是被哪一路召回的"的唯一出口。**没有任何测试会红。**

    所以这里断言：`policy` 里声明的每个分支名都在表里有条目。
    和 `_check_backend_labels` 同一个形状：一个集合、一个映射，核对覆盖率。
    """
    from app.rag import policy, types

    print()
    print("  检查 6：每个召回分支都有对应的 Hit 字段")
    # ⚠️ 分支名集合从 `policy.BRANCH_LABEL` 来，**不要手写**。
    #
    # 这一行第一版写的是 `{policy.BRANCH_VEC, *policy.SECOND_BRANCHES}`，
    # 然后加 `BRANCH_TRGM` 时它**没红**——因为 trgm 不在 SECOND_BRANCHES 里
    # （它是条件分支，只在模型挂的时候跑）。检查自己漏了被检查的东西，
    # 和 `_check_backend_labels` 里被记住的那个教训一模一样。
    # `BRANCH_LABEL` 是"有哪些分支"的真相来源（`_backend` 拼串也用它）。
    declared = set(policy.BRANCH_LABEL)
    missing = sorted(declared - set(types._BRANCH_RANK_FIELD))
    if missing:
        print(f"    ❌ 这些分支在 types._BRANCH_RANK_FIELD 里没有字段：{missing}")
        print("       给 Hit 加一个字段，再往那个 dict 里加一行。")
        print("       不改的话：这条分支召回了什么，从 Hit 上一个字都看不到，")
        print("       而 SearchOutcome.branches 照常有值——不会报错。")
        return False

    # 反向也要查：表里写着一个早就不存在的分支名，是删代码时漏掉的一半。
    stale = sorted(set(types._BRANCH_RANK_FIELD) - declared)
    if stale:
        print(f"    ❌ types._BRANCH_RANK_FIELD 里有已经不存在分支：{stale}")
        print("       删掉它们（以及对应的 Hit 字段和投影）。")
        return False

    print(
        f"    {sorted(declared)} 全部有字段："
        f"{ {n: types._BRANCH_RANK_FIELD[n] for n in sorted(declared)} }"
    )
    return True


def _cases_digest(results: list[CaseResult]) -> str:
    """用例内容的短摘要，用来判断"两次跑的是不是同一套题"。

    `sort_keys=True` 让字段顺序不影响结果（JSON 里换个键序不该算改动用例）。
    ⚠️ 但**用例之间的顺序**是算进去的——重排用例不影响指标，
    所以排序本可以宽容一点；这里选择不宽容，是因为"用例被重排过"
    这件事本身不该发生，发生了值得看一眼。
    """
    h = hashlib.sha256()
    for r in results:
        h.update(
            json.dumps(r.case, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        h.update(b"\n")
    return h.hexdigest()[:12]


def _fingerprint(
    results: list[CaseResult],
    k: int,
    rewrite: bool,
    second_branch: str,
    second_query: str = policy_mod.SECOND_QUERY_DEFAULT,
    rerank_query: str = policy_mod.RERANK_QUERY_DEFAULT,
) -> dict:
    """这次跑的【配置指纹】。

    ⚠️ 这个东西的存在，是因为项目已经踩过两次同一类坑：

    1. **同一份数据在不同 `top_k` 上给出相反结论**（`weight_second=0` 在 k=5 上
       逐位相同、在 k=8 上差一条）。这条教训之前**完全靠人记住**。
    2. 往 `qa_set.jsonl` 里追加用例之后，`cases` 从 86 变成 104，
       而 `_compare_baseline` 不检查用例数——**拿 104 条的结果去比 86 条的基线，
       静默错位。**
    3. Phase 3 之后**第二路本身成了一个开关**（`sparse` / `none`），
       而默认值还变过一次（`bm25` → `sparse`）。不把这一项写进指纹的话，
       拿 `sparse` 的基线去比 `--second-branch none` 的一次跑，
       会得到一个看起来像"退步"的 −0.0026——那是换配置，不是退步。

    配上指纹之后，这些不一致会**拒绝对比**，而不是给出一个看起来正常的数字。

    ⚠️ 语料块数也在指纹里：重灌知识库之后指标会变，但那不是"检索退步了"。
    """
    from app.rag import policy as P
    from app.rag import vector_store

    n = vector_store.corpus_size()
    return {
        "cases": len(results),
        # ⚠️ 用例【内容】的指纹，不是文件名。
        #
        # 文件名挡不住这一类错：把某条用例的 gold 标签改一下（或者把
        # `section` 从数组写成字符串），用例数不变、文件名不变，
        # 指标却变了——然后对比机制会把这个差归到"检索退步"上。
        # 内容指纹会直接拒绝对比。
        "cases_digest": _cases_digest(results),
        "top_k": k,
        "rewrite": rewrite,
        # ⚠️ 第二路是哪一条。它是**检索策略的一部分**，和 top_k 同级：
        # 换第二路会改变候选池，改了就不是同一套配置。
        "second_branch": second_branch,
        # ⚠️ 第二路用哪个查询也是配置的一部分：bm25(raw) 和 bm25(rewritten)
        # 是两个不同的检索器，拿一个的基线去比另一个是换配置，不是退步。
        "second_query": second_query,
        # ⚠️ 精排用哪段文本也是配置的一部分：交叉编码器的输入变了，
        # 分数和排序就会变，拿旧基线比是换配置，不是退步。
        "rerank_query": rerank_query,
        "corpus_chunks": n,
        # 检索策略的关键参数。它们会随代码变——改了 `CAND_FRACTION`
        # 而没意识到基线作废，是这套机制最容易漏掉的一种。
        "cand_per_branch": P.cand_per_branch(n),
        "cand_total": P.cand_total(n),
        "rrf_k": P.RRF_K,
    }


# 指纹项对不上时的解释。⚠️ 必须给，因为像 cases_digest 这种值
# 打印出来是 "a3f9c1e07b42 → 5d2b8e441f9a"，不说人话的话看到的人
# 只知道"对不上"、不知道"为什么对不上"，下一步就只能去猜或去改基线。
_FP_HINT = {
    "cases": "用例条数变了",
    "cases_digest": "用例【内容】被改过（条数可能没变，比如改了 gold 标签）",
    "top_k": "取几条变了",
    "rewrite": "Query 改写开关变了",
    "second_branch": "第二路换了（sparse / bm25 / none）——这是换了配置，不是退步",
    "second_query": "第二路的查询文本换了（rewritten / raw）——这是换了配置，不是退步",
    "rerank_query": "精排的查询文本换了（rewritten / raw / union）——这是换了配置，不是退步",
    "corpus_chunks": "知识库重灌过——指标变了不是检索退步，是语料变了",
    "cand_per_branch": "候选池的每路条数变了（改过 CAND_FRACTION 或语料规模）",
    "cand_total": "候选池总量变了",
    "rrf_k": "RRF 的 k 变了",
}


def _compare_baseline(
    results: list[CaseResult],
    path: str,
    deterministic: bool,
    k: int,
    second_branch: str,
    second_query: str = policy_mod.SECOND_QUERY_DEFAULT,
    rerank_query: str = policy_mod.RERANK_QUERY_DEFAULT,
) -> None:
    """和上次的指标对比，掉得明显就报出来。

    ⚠️ 对比只在【同一套配置】下有意义。带改写的跑和 --no-rewrite 的跑
    本来就不可比——两者的绝对水平差 0.15 以上，混着比会得出
    "退步了"或"进步了"的假结论，而实际上什么都没变。

    比出一个假结论比不对比更糟：它会让人开始不信任这个机制。
    所以指纹对不上时**拒绝对比**，并说清是哪一项对不上。
    """
    try:
        base = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"\n⚠️ 读不到基线 {path}：{e}")
        return

    now_fp = _fingerprint(
        results, k, not deterministic, second_branch, second_query, rerank_query
    )
    base_fp = {key: base.get(key) for key in now_fp}

    # 老基线可能没有指纹字段——补出来，并且【明确说出来】，
    # 而不是当作"没问题"混过去。
    missing = [key for key, v in base_fp.items() if v is None]
    mismatched = [
        (key, base_fp[key], now_fp[key])
        for key in now_fp
        if base_fp[key] is not None and base_fp[key] != now_fp[key]
    ]

    if mismatched or missing:
        print()
        print("⚠️ 配置指纹对不上，拒绝对比：")
        for key, old, new in mismatched:
            print(f"   {key:<16} 基线 {old!r}  →  这次 {new!r}")
            hint = _FP_HINT.get(key)
            if hint:
                print(f"   {'':<16} └ {hint}")
        for key in missing:
            print(f"   {key:<16} 基线里没有这一项（可能是旧版存的）")
        print()
        print("   指标只有在【同一套配置】下才可比。要对，就把配置调回基线的样子，")
        print("   或者重新存一版基线：--save-baseline <路径>")
        return

    now = {
        "recall": recall_at_k(results, k),
        "hit": hit_at_k(results, k),
        "mrr": mrr(results),
        "ndcg": ndcg_at_k(results, k),
    }
    print()
    print(f"=== 与基线对比（{path}，指纹一致）===")
    for key, cur in now.items():
        old = base.get(key)
        if old is None or cur is None:
            continue
        delta = cur - old
        flag = "  ⚠️ 退步" if delta < -0.02 else ""
        print(f"  {key:<8} {old:.4f} -> {cur:.4f}   {delta:+.4f}{flag}")


if __name__ == "__main__":
    sys.exit(main())
