"""在线检索流水线：改写 → 过滤 → 召回 → 融合 → 精排 → 组装。

稠密路和 BM25 并行召回，RRF 融合后精排；嵌入模型不可用时稠密路消失，
BM25 仍可运行，只有第二路也不是 BM25 时才退到 pg_trgm。
每一步都可能单独失效，但只要有任意一路召回可用，检索就不应整体失败。
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.rag import assemble as assemble_mod
from app.rag import rewrite
from app.rag.assemble import Assembled
from app.rag.types import _BRANCH_RANK_FIELD, _BRANCH_SCORE_FIELD, Hit

logger = logging.getLogger(__name__)

# 常量统一放在 policy.py。这里用直接名字导入，因为 search() 的参数名 policy
# 会遮蔽模块导入。
from app.rag.policy import (  # noqa: E402
    BRANCH_BM25,
    BRANCH_LABEL,
    BRANCH_SPARSE,
    BRANCH_TRGM,
    BRANCH_VEC,
    CAND_MIN,
    NO_BRANCH,
    PIPELINE_ERROR,
    REFUSAL_THRESHOLD,
    RERANK_QUERIES,
    RERANK_QUERY_DEFAULT,
    RRF_K,
    SECOND_CHOICES,
    SECOND_DEFAULT,
    SECOND_QUERY_DEFAULT,
    SECOND_QUERIES,
)
from app.rag.policy import cand_per_branch as _calc_per_branch  # noqa: E402
from app.rag.policy import cand_total as _calc_total  # noqa: E402

# 两路都第 1 名时的 RRF 分，用于归一化到 0~1；必须用实际的 k 计算。
def _rrf_max(k: int = RRF_K) -> float:
    return 2.0 / (k + 1)

_VECTOR_SQL = text(
    """
SELECT id, embedding <=> CAST(:qvec AS vector) AS dist
FROM document_chunks
WHERE
    -- 关键守卫：:qvec 为 NULL 时 ORDER BY NULL 会返回任意行并赋予名次，
    -- 检索看起来正常但全是无关内容。必须显式拦住。
    CAST(:qvec AS vector) IS NOT NULL
    AND embedding IS NOT NULL
    AND (CAST(:filters AS jsonb) IS NULL OR meta @> CAST(:filters AS jsonb))
ORDER BY dist
LIMIT :vec_k
"""
)

# 稀疏路：bge-m3 词权重点积。`<#>` 返回负内积，升序即最相关在前。
# 两个 IS NOT NULL 守卫与向量路同理；chunk_sparse 是后加列，旧数据为 NULL。
_SPARSE_SQL = text(
    """
SELECT id, chunk_sparse <#> CAST(:qsparse AS sparsevec) AS neg_ip
FROM document_chunks
WHERE CAST(:qsparse AS sparsevec) IS NOT NULL
  AND chunk_sparse IS NOT NULL
  AND (CAST(:filters AS jsonb) IS NULL OR meta @> CAST(:filters AS jsonb))
ORDER BY neg_ip, id
LIMIT :kw_k
"""
)

# 无模型的块级召回：查询与 content 的三元组相似度。
# 用 word_similarity 而非 similarity：短查询和长块整串相似度过低（0.009~0.028）。
# 不加 % 阈值：默认阈值实测全部返回 0 行，降级路宁可多给几条。
_TRGM_SQL = text(
    """
SELECT id, word_similarity(CAST(:q AS text), content) AS sim
FROM document_chunks
WHERE content IS NOT NULL
  AND (CAST(:filters AS jsonb) IS NULL OR meta @> CAST(:filters AS jsonb))
ORDER BY sim DESC, id
LIMIT :kw_k
"""
)

_DETAIL_SQL = text(
    """
SELECT id, document_id, chunk_index, title, source, content, meta, content_hash
FROM document_chunks
WHERE id = ANY(CAST(:ids AS bigint[]))
"""
)

# 行程 POI 补充：按章节名直接取块。
#
# ⚠️ 这是【已知条目检索】，不是语义检索：canonical 已经和库里的
# `section_key` 对齐（poi.py 的词表由 check_poi_expand.py 对账），
# 所以不需要再跑一遍嵌入和精排。按 POI 顺序 + chunk_index 排序，
# 保证同一份输入每次取到同样的块。
_SECTION_SQL = text(
    """
SELECT id, document_id, chunk_index, title, source, content, meta, content_hash
FROM document_chunks
WHERE section_key = ANY(CAST(:sections AS text[]))
  AND (CAST(:filters AS jsonb) IS NULL OR meta @> CAST(:filters AS jsonb))
ORDER BY array_position(CAST(:sections AS text[]), section_key),
         chunk_index, id
LIMIT :limit
"""
)


@dataclass
class SearchOutcome:
    """一次完整检索的产出。"""

    query: str
    rewritten: str
    filters: dict[str, str]
    candidates: list[Hit]
    assembled: Assembled
    backend: str
    timings_ms: dict[str, float] = field(default_factory=dict)

    has_material: bool = True
    """这次检索到的东西**够不够格当资料用**。

    ⚠️ 它和 `candidates` 是两件事，不要合并：
    `candidates` 回答"检索到了什么"，`has_material` 回答"这些能不能用"。
    把不够格的结果从 `candidates` 里删掉的话，**检索层就不可观测了**——
    评估、排查、`branches` 归因全都要看那些"低分但真实存在"的候选。

    判定只发生在 `retriever` / 图节点 / 接口那一层，检索本身保持纯粹。

    ⚠️ 无精排时**恒为 True**（fail-open）。理由见 `policy.REFUSAL_THRESHOLD`：
    RRF 分数只由名次决定，不含相关性信息，拿阈值卡它是自欺。
    """

    branches: dict[str, list[int]] = field(default_factory=dict)
    """每一路召回的【完整】chunk_id 列表，按该路自己的名次排（0 是第一名）。

    键是分支名：`"vec"` / `"sparse"` / `"trgm"`。**没跑的那一路不会出现**。

    ═══════════════════════════════════════════════════════════════════
     ⚠️ 为什么必须有这个字段——它是"能不能评价一个召回分支"的前提。
    ═══════════════════════════════════════════════════════════════════

    `Hit.vec_rank` / `sparse_rank` **只对进了融合 top-N 的候选有值**。
    而"这条召回路径有没有价值"的答案，恰恰藏在**没进 top-N 的那些**候选里。

    只统计幸存者，等于问"哪支球队更强"却只看进了决赛的那几个——
    决赛里当然两边都有，看不出差距。实测踩过这个坑：
    `weight_second=0` 和等权在 `top_k=5` 上四项指标逐位相同，就是因为
    两路各自的候选池都占全语料 23%，融合后留下的那 30 条几乎一样。

    有了每路的**完整**名次，`branch_recall@n(gold)` 就是一个直接可算的数：
    "这一路的 top-n 里，到底命中了多少正确答案"。**那才是分支自己的成绩。**

    ⚠️ 别用它来排序或做任何决策——它是【诊断信息】，检索结果仍然只由
    `candidates` 决定。混进去会让"为什么这条结果排在前面"变得无法解释。
    """

    poi_expanded: tuple[str, ...] = ()
    """这次为行程类 query 额外补进 `assembled.context` 的 POI 章节。

    ⚠️ 它**不进 `candidates`**：`candidates` 仍然是问题精排后的 top-k，
    这些块是按行程提到的 POI 从章节直接取出来的补充材料。两者分开，
    "这次为什么多给了几段资料"才说得清。
    """

    @property
    def oracle(self) -> list[int]:
        """两路并集（按各路的 RRF 分排）。

        这是"候选生成"这一层的**上限**：精排再厉害，也只能在候选池里挑。
        如果 `oracle` 都没命中，问题在召回；命中了但最终没进 top-k，问题在精排。
        **这两个数分开看才知道该修哪一层。**
        """
        seen: dict[int, float] = {}
        for name, ids in self.branches.items():
            for rank, cid in enumerate(ids):
                # 用 1/(rank+1) 做权重只是为了让两路结果有个确定的合并顺序，
                # 不是什么打分——这里要的是【集合】，顺序无关紧要。
                seen[cid] = seen.get(cid, 0.0) + 1.0 / (rank + 1)
        return sorted(seen, key=lambda c: (-seen[c], c))


@dataclass(frozen=True)
class RetrievalPolicy:
    """检索策略开关，默认值等同于现有生产行为。

    这些开关用于评估"某个组件是否值得保留"，不放进 config.py；
    调整应通过 eval_quality.py --sweep 并附带评估结果。
    """

    rrf_k: int = RRF_K
    """RRF 的 k。原论文默认 60。"""

    weight_vec: float = 1.0
    """向量路在 RRF 里的权重。"""

    weight_second: float = 1.0
    """第二路在 RRF 里的权重；原论文是等权，这里是加权改造。"""

    second_branch: str = SECOND_DEFAULT
    """第二路：sparse / bm25 / none。生产用 bm25，因为它不依赖嵌入模型；
    none 仅用于评估，会失去无模型兜底。"""

    second_query: str = SECOND_QUERY_DEFAULT
    """第二路查询文本：minimal（生产）、raw、rewritten。
    只影响 BM25 查询，city 过滤仍来自改写结果。"""

    rerank_query: str = RERANK_QUERY_DEFAULT
    """精排查询文本，默认 union（原话 + 改写）；拼接顺序会影响指标。"""

    rerank: bool = True
    """是否使用交叉编码器精排；关闭后按 RRF 分数排序。"""

    expand_pois: bool = True
    """行程类 query 是否补充 POI 章节到 context；只影响 context/citations。"""

    cand_per_branch: int = 0
    """每路候选数；0 表示按语料比例 round(0.23 × N) 自动计算，上下界 20/200。"""

    cand_total: int = 0
    """融合后进入精排的条数；0 表示与 cand_per_branch 相同。"""

    def __post_init__(self) -> None:
        """构造时校验枚举值，避免拼错分支名后静默少跑一路。"""
        if self.second_branch not in SECOND_CHOICES:
            raise ValueError(
                f"second_branch={self.second_branch!r} 不是合法取值，"
                f"只能是 {SECOND_CHOICES} 之一"
            )
        if self.second_query not in SECOND_QUERIES:
            raise ValueError(
                f"second_query={self.second_query!r} 不是合法取值，"
                f"只能是 {SECOND_QUERIES} 之一"
            )
        if self.rerank_query not in RERANK_QUERIES:
            raise ValueError(
                f"rerank_query={self.rerank_query!r} 不是合法取值，"
                f"只能是 {RERANK_QUERIES} 之一"
            )


def search(
    query: str,
    *,
    filters: dict[str, str] | None = None,
    top_k: int = 5,
    history: list[tuple[str, str]] | None = None,
    rewrite_query: bool = True,
    with_context: bool = True,
    policy: RetrievalPolicy | None = None,
    plan: "rewrite.QueryPlan | None" = None,
) -> SearchOutcome | None:
    """跑完整条流水线。

    None 表示向量库整条路不可用，调用方应降级；返回空 candidates 表示
    路通了但没检索到。两者必须分开，否则降级会拿标题冒充检索结果。
    """
    policy = policy or RetrievalPolicy()
    from app.rag import vector_store

    engine = vector_store.get_vector_engine()
    if engine is None:
        return None

    timings: dict[str, float] = {}

    # ---- 1. 改写 ----
    started = time.monotonic()
    if plan is not None:
        # 评估用：调用方传进来一份**缓存的**改写结果，让两个配置
        # （sparse vs bm25）看到完全相同的 rewritten，避免改写抖动污染对比。
        rewrite_ms = 0.0
    elif rewrite_query:
        plan, rewrite_ms = rewrite.plan(query, history)
    else:
        # 显式关掉改写时，退化成"原查询 + 无过滤"，
        # 和改写失败时的降级路径完全一样。这样 --no-rewrite 的对比实验
        # 测的才是"没有改写"本身，而不是"另一套降级逻辑"。
        plan = rewrite.QueryPlan(rewritten=query, filters=rewrite._ExtractedFilters())
        rewrite_ms = 0.0
    timings["rewrite"] = rewrite_ms

    effective_filters = rewrite.merge_filters(filters, plan.filters)

    # ---- 1.5 候选池大小：按语料规模算，不是写死的条数 ----
    #
    # ⚠️ `policy.cand_per_branch == 0` 表示"按规则算"。显式传一个正数才是覆盖。
    # 这个取值方式让默认行为既**自适应**（语料变大自动跟着变），
    # 又**逐位无损**（语料 130 块时算出来正好是 30，等于改造前的写死值）。
    corpus_n = vector_store.corpus_size()
    per_branch = policy.cand_per_branch or _calc_per_branch(corpus_n)
    total = policy.cand_total or _calc_total(corpus_n)

    # ---- 2. 多路召回 ----
    started = time.monotonic()
    try:
        recalled, embed_used = _recall(
            engine, plan.rewritten, effective_filters,
            per_branch=per_branch, second_branch=policy.second_branch,
            raw_query=query, second_query=policy.second_query,
        )
    except Exception as e:
        # 查询期间出错（连接断了、表被删了）也要让上层知道"这条路不可用"，
        # 而且要把 engine 标成 down，否则每个请求都会重走一遍三秒超时。
        vector_store.mark_down(f"检索查询失败：{e}")
        logger.exception("向量库查询失败")
        return None
    timings["recall"] = _ms(started)

    # 每路在 RRF 里的权重。向量路和"第二路"各一个，
    # 而第二路具体是哪条路由 second_branch 决定——权重不跟着名字变，
    # 因为这个旋钮问的是"第二路值不值钱"，和它具体是哪条路无关。
    branches = [
        BranchRecall(name, rows, policy.weight_vec if name == BRANCH_VEC else policy.weight_second)
        for name, rows in recalled
    ]

    # 融合前保留每路完整名次：未跑的分支不出现，跑了但为空的是空列表。
    branch_ids = {b.name: [cid for cid, _ in b.rows] for b in branches}
    branch_names = [b.name for b in branches]

    # ---- 3. RRF 融合 ----
    fused = _fuse(branches, k=policy.rrf_k, total=total)
    if not fused:
        return SearchOutcome(
            query=query,
            rewritten=plan.rewritten,
            filters=effective_filters or {},
            candidates=[],
            assembled=Assembled("", [], 0, 0, 0),
            backend=_backend(branch_names, reranked=False),
            timings_ms=timings,
            # 一条候选都没有 → 没有资料可用。不需要看分数。
            has_material=False,
            branches=branch_ids,
        )

    # ---- 4~6. 取正文 → 精排 → 组装 ----
    # 与召回分开 try：召回失败是向量库不可用，这三步失败是代码问题，
    # 返回 pipeline-error 由调用方决定降级或 503。
    try:
        # ---- 4. 取正文 ----
        started = time.monotonic()
        details = _fetch_details(engine, [f.chunk_id for f in fused])
        timings["fetch"] = _ms(started)

        # ---- 5. 精排 ----
        candidates = _build_candidates(fused, details, rrf_k=policy.rrf_k)

        started = time.monotonic()
        if policy.rerank:
            from app.rag.query_text import minimal_clean

            clean_query = minimal_clean(query)
            clean_rewritten = minimal_clean(plan.rewritten)
            if policy.rerank_query == "raw":
                rerank_text = query
            elif policy.rerank_query == "minimal":
                rerank_text = clean_query
            elif policy.rerank_query == "union":
                # 相同文本只留一份：重复拼接会把可答查询分数压到拒答阈值以下。
                rerank_text = (
                    clean_query
                    if clean_query == clean_rewritten
                    else f"{clean_query} {clean_rewritten}"
                )
            else:
                # rewritten 模式也过一遍表示层清洗：LLM 偶尔会吐全角标点/
                # 零宽字符，清掉它们不改变词项，和 minimal 的纪律一致。
                rerank_text = clean_rewritten
            candidates, reranked = _rerank(rerank_text, candidates)
        else:
            reranked = False
        timings["rerank"] = _ms(started)

        candidates = candidates[:top_k]

        # ---- 6. 组装 ----
        started = time.monotonic()
        assembled = (
            assemble_mod.assemble(list(candidates))
            if with_context
            else Assembled("", [], 0, 0, 0)
        )
        timings["assemble"] = _ms(started)
    except Exception:
        # 这里出错说明代码或 SQL 有问题，必须留堆栈。
        # 不标记 vector_store down：否则会把代码 bug 伪装成环境抖动并自动恢复。
        logger.exception("检索流水线在取正文/精排/组装阶段出错")
        return SearchOutcome(
            query=query,
            rewritten=plan.rewritten,
            filters=effective_filters or {},
            candidates=[],
            assembled=Assembled("", [], 0, 0, 0),
            backend=PIPELINE_ERROR,
            timings_ms=timings,
            # 流水线崩了 → 这次没有可用的资料。这一条独立于 `backend`：
            # `backend` 说的是"为什么没资料"，`has_material` 说的是"有没有"。
            has_material=False,
            # ⚠️ branches 照给。归因信息在这一刻是有价值的——
            # "召回明明成功了、是后面坏的"这件事，只有它说得清。
            branches=branch_ids,
        )

    has_material = _has_material(candidates, reranked)

    # ---- 7. 行程类 query：把提到的 POI 章节补进 context ----
    #
    # ⚠️ 只改 `assembled`，不改 `candidates`。行程骨架里提到的景点可能
    # 排在 top-k 之外（实测大熊猫基地排第 9），但它们是回答门票/时长
    # 必需的原始资料。补进来的块是章节原文，不是专家措辞。
    #
    # 失败只 warning，不改变本轮检索结果：补充资料是增强，不是主路径。
    poi_expanded: tuple[str, ...] = ()
    if policy.expand_pois and with_context and has_material:
        started = time.monotonic()
        try:
            extra_hits, poi_expanded = _expand_pois(
                engine,
                query,
                candidates,
                assembled.context,
                effective_filters,
            )
            if extra_hits:
                # 预算由 assemble 统一控制：放不下的整块丢弃，不截半句。
                assembled = assemble_mod.assemble([*candidates, *extra_hits])
                # ⚠️ `poi_expanded` 只报【实际进了 context】的 POI。
                # 取到了但被预算丢掉的那些，不能在 note 里说"已补充"——
                # 排查的人会去 context 里找，然后找不到。
                used_sections = {
                    " > ".join(c.section) for c in assembled.citations
                }
                poi_expanded = tuple(
                    n for n in poi_expanded if n in used_sections
                )
        except Exception:
            logger.exception("行程 POI 补充失败，保留原上下文")
            poi_expanded = ()
        timings["poi_expand"] = _ms(started)

    return SearchOutcome(
        query=query,
        rewritten=plan.rewritten,
        filters=effective_filters or {},
        candidates=candidates,
        assembled=assembled,
        backend=_backend(branch_names, reranked),
        timings_ms=timings,
        has_material=has_material,
        branches=branch_ids,
        poi_expanded=poi_expanded,
    )


@dataclass(frozen=True)
class BranchRecall:
    """一条召回路径的结果：按名次排列的 (chunk_id, 原始分) 和 RRF 权重。

    原始分只用于展示；权重为 0 时候选仍进融合池，但最终会被截断。
    """

    name: str
    rows: list[tuple[int, float]]
    weight: float = 1.0


@dataclass(frozen=True)
class Fused:
    """融合之后的一条候选。"""

    chunk_id: int
    rrf: float
    ranks: dict[str, int]
    """分支名 → 在这条路上的名次（**1 起**）。只含真的召回了它的路。"""
    scores: dict[str, float]
    """分支名 → 这一路上的原始分。含义见 `BranchRecall.rows`。"""


def _recall(
    engine: Any,
    rewritten: str,
    filters: dict[str, str] | None,
    *,
    per_branch: int = CAND_MIN,
    second_branch: str = SECOND_DEFAULT,
    raw_query: str = "",
    second_query: str = SECOND_QUERY_DEFAULT,
) -> tuple[list[tuple[str, list[tuple[int, float]]]], bool]:
    """跑各路召回，返回 (分支结果, 是否用上稠密路)。

    向量库不可用由调用方整体降级；嵌入模型不可用时稠密路消失，
    BM25 仍可独立运行，只有 BM25 也失败才退到 trgm。
    """
    out: list[tuple[str, list[tuple[int, float]]]] = []
    embed_used = False

    # 延迟 import：embedding 模块顶层会 import torch（几秒 + 几百 MB 内存），
    # 而"只想跑一次纯关键词检索"的场景不该付这个代价。
    from app.agents import embedding

    qvec = embedding.embed_query(rewritten)
    if qvec is not None:
        embed_used = True
        params = {
            "qvec": _to_vector(qvec),
            "filters": _json_or_none(filters),
            "vec_k": per_branch,
        }
        with engine.connect() as conn:
            rows = [
                (int(r[0]), float(r[1])) for r in conn.execute(_VECTOR_SQL, params)
            ]
        out.append((BRANCH_VEC, rows))
    else:
        logger.warning(
            "嵌入模型不可用，本次检索没有稠密路（第二路是 %s 的话还剩一路）",
            second_branch,
        )

    if qvec is None:
        # 嵌入不可用时优先 BM25：纯 CPU、有 IDF，排序明显好于 trgm。
        if second_branch == BRANCH_BM25:
            rows = _bm25_search(
                engine, rewritten, filters, per_branch, raw_query, second_query
            )
            if rows:
                out.append((BRANCH_BM25, rows))
                return out, embed_used
            logger.warning("嵌入模型不可用，BM25 也没有召回任何块，改用 trgm 兜底")

        # BM25 无结果时退到 pg_trgm：纯 SQL，不需要模型、分词器或词表。
        out.append((BRANCH_TRGM, _trgm_search(engine, rewritten, filters, per_branch)))
        return out, embed_used

    if second_branch == BRANCH_SPARSE:
        out.append((BRANCH_SPARSE, _sparse_search(engine, rewritten, filters, per_branch)))
    elif second_branch == BRANCH_BM25:
        out.append(
            (
                BRANCH_BM25,
                _bm25_search(
                    engine, rewritten, filters, per_branch, raw_query, second_query
                ),
            )
        )

    # `SECOND_NONE`（以及任何没被上面命中的取值，虽然构造 policy 时
    # 已经拦过一遍）都落到这里：不跑第二路。**不加空列表。**
    # 见 search() 里 branch_ids 的注释——"没跑"和"跑了但没有"必须能区分。

    return out, embed_used


def _trgm_search(
    engine: Any,
    rewritten: str,
    filters: dict[str, str] | None,
    per_branch: int,
) -> list[tuple[int, float]]:
    """pg_trgm 三元组召回，只在嵌入不可用且 BM25 也失败时运行。

    排序质量低于 BM25（没有 IDF，常见字会干扰），但不需要模型或词表，
    对手是只能匹配标题的 keyword-fallback。不加阈值：默认阈值实测会
    把可答问题全部过滤成 0 行；大表顺序扫描只在故障时发生。
    """
    text = (rewritten or "").strip()
    if not text:
        # ⚠️ 空查询必须在这里挡住。`word_similarity(NULL, content)` 对每一行
        # 都返回 NULL，而 `ORDER BY NULL` 是空操作——LIMIT 会返回**任意** N 行
        # 并给它们编号 1..N，然后带着接近满分的位置进 RRF。
        # 和 `_VECTOR_SQL` / `_SPARSE_SQL` 里那两个守卫是同一条教训。
        return []

    params = {
        "q": text,
        "filters": _json_or_none(filters),
        "kw_k": per_branch,
    }
    with engine.connect() as conn:
        rows = conn.execute(_TRGM_SQL, params).fetchall()
    return [(int(r[0]), float(r[1])) for r in rows]


def _bm25_search(
    engine: Any,
    rewritten: str,
    filters: dict[str, str] | None,
    per_branch: int,
    raw_query: str = "",
    second_query: str = SECOND_QUERY_DEFAULT,
) -> list[tuple[int, float]]:
    """BM25 召回。**不依赖嵌入模型，模型挂了也能跑。**

    ⚠️ 延迟 import 有两个理由：
      1. jieba 首次加载要构建词典（几百毫秒），不在 BM25 配置下不该付这笔钱；
      2. 评估脚本跑 `--second-branch none` 时根本不需要分词器。
    """
    from app.rag import bm25 as bm25_mod
    from app.rag.tokenize import tokenize

    # 生产用 `minimal`：保留全部内容词，只修表示层噪声。
    # `raw` 是字节级原样，`rewritten` 会增删词项；两者都保留为评估开关。
    if second_query in ("minimal", "raw") and raw_query:
        if second_query == "minimal":
            from app.rag.query_text import minimal_clean

            branch_query = minimal_clean(raw_query)
        else:
            branch_query = raw_query
        if not branch_query:
            branch_query = rewritten
    else:
        branch_query = rewritten
    logger.debug("BM25 分支查询文本：%s", branch_query)
    return bm25_mod.search(engine, tokenize(branch_query), filters, per_branch)


def _sparse_search(
    engine: Any,
    rewritten: str,
    filters: dict[str, str] | None,
    per_branch: int,
) -> list[tuple[int, float]]:
    """稀疏路召回。编码不出来时返回空列表（和"没有命中"同义，由调用方区分）。"""
    from app.rag import sparse

    qsparse = sparse.encode_query(rewritten)
    if qsparse is None:
        # 和"嵌入模型不可用"是同一个原因（共用底座），所以不重复记 warning，
        # 上面 `_recall` 已经记过一次了。
        return []

    params = {
        "qsparse": sparse.to_literal(qsparse),
        "filters": _json_or_none(filters),
        "kw_k": per_branch,
    }
    with engine.connect() as conn:
        rows = conn.execute(_SPARSE_SQL, params).fetchall()

    # ⚠️ `<#>` 给的是**负**内积，这里取反还原成"越大越相关"。
    # 不取反的话 `Hit.sparse_score` 是一串负数，读的人和写排序的人
    # 都会先愣一下——而那一下正是出错的地方。
    return [(int(r[0]), -float(r[1])) for r in rows]


def _fuse(
    branches: list[BranchRecall],
    *,
    k: int = RRF_K,
    total: int = CAND_MIN,
) -> list[Fused]:
    """RRF 融合多路结果，按 rrf 降序返回。

    只用名次，不混原始分数：各分支量纲不同，归一化映射比 RRF 更难解释。
    名次从 1 开始，同分按 chunk_id 排序以保证可复现。
    """
    ranks = {b.name: {cid: i + 1 for i, (cid, _) in enumerate(b.rows)} for b in branches}
    scores = {b.name: {cid: s for cid, s in b.rows} for b in branches}

    all_ids: set[int] = set()
    for table in ranks.values():
        all_ids |= set(table)

    fused: list[Fused] = []
    for cid in all_ids:
        rrf = 0.0
        row_ranks: dict[str, int] = {}
        row_scores: dict[str, float] = {}
        for b in branches:
            rank = ranks[b.name].get(cid)
            if rank is None:
                continue
            row_ranks[b.name] = rank
            row_scores[b.name] = scores[b.name][cid]
            if b.weight:
                rrf += b.weight / (k + rank)
        fused.append(Fused(cid, rrf, row_ranks, row_scores))

    # 第二排序键用 cid：分数相同时没有它，set 的迭代顺序会让结果在不同进程间抖动，
    # 测试就没法断言了（set 的顺序在同一个进程里是稳定的，跨进程不是）。
    fused.sort(key=lambda f: (-f.rrf, f.chunk_id))
    return fused[:total]


def _has_material(candidates: list[Hit], reranked: bool) -> bool:
    """判断这批结果能否当资料使用。

    空结果返回 False；无精排时返回 True（RRF 分无相关性信息，卡阈值只会误伤）；
    有精排时看 top-1 是否达到 REFUSAL_THRESHOLD。
    低分候选不从 candidates 删除，保留给评估和排查。
    """
    if not candidates:
        return False
    if not reranked:
        return True
    return candidates[0].score >= REFUSAL_THRESHOLD


def _fetch_details(engine: Any, ids: list[int]) -> dict[int, Any]:
    """一次把候选块的正文取回来。"""
    if not ids:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(_DETAIL_SQL, {"ids": ids}).fetchall()
    return {int(r[0]): r for r in rows}


def _fetch_section_hits(
    engine: Any,
    sections: list[str],
    filters: dict[str, str] | None,
) -> list[Hit]:
    """按章节名直接取块。给行程 POI 补充用。

    这些块**不经过嵌入/精排**：canonical 已经和 `section_key` 对齐，
    属于已知条目检索。`score` 设成 0、`rerank_score=None`，因为它们
    不参与 `candidates` 的排序，只进 `assembled.context`。
    """
    if not sections:
        return []

    from app.rag import poi as poi_mod

    params = {
        "sections": sections,
        "filters": _json_or_none(filters),
        "limit": poi_mod.MAX_POIS * poi_mod.MAX_CHUNKS_PER_POI,
    }
    with engine.connect() as conn:
        rows = conn.execute(_SECTION_SQL, params).fetchall()

    out: list[Hit] = []
    for row in rows:
        meta = row[6] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)
        out.append(
            Hit(
                chunk_id=int(row[0]),
                document_id=int(row[1]),
                chunk_index=int(row[2]),
                title=row[3] or "",
                source=row[4],
                content=row[5] or "",
                section=tuple(meta.get("section") or ()),
                meta=meta,
                content_hash=row[7] or "",
                # 不参与排序，也不是精排分。这两个字段只用于展示，
                # 所以给 0/None 不会影响任何决策。
                score=0.0,
                rrf=0.0,
                vec_rank=None,
                sparse_rank=None,
                sparse_score=None,
                bm25_rank=None,
                bm25_score=None,
                trgm_rank=None,
                trgm_score=None,
                rerank_score=None,
            )
        )
    return out


def _expand_pois(
    engine: Any,
    query: str,
    candidates: list[Hit],
    context: str,
    filters: dict[str, str] | None,
) -> tuple[list[Hit], tuple[str, ...]]:
    """行程类 query：把 context 里提到、但没进 top-k 的 POI 章节补回来。

    返回 `(补充的块, 实际补到的 POI 名)`。
    """
    from app.rag import poi as poi_mod

    if not poi_mod.is_itinerary_query(query):
        return [], ()

    names = poi_mod.extract_pois(f"{query}\n{context}")
    if not names:
        return [], ()

    have = {c.section_key for c in candidates}
    missing = [n for n in names if n not in have][: poi_mod.MAX_POIS]
    if not missing:
        return [], ()

    extras = _fetch_section_hits(engine, missing, filters)
    if not extras:
        return [], ()

    got = {h.section_key for h in extras}
    found = tuple(n for n in missing if n in got)
    return extras, found


def _build_candidates(
    fused: list[Fused],
    details: dict[int, Any],
    rrf_k: int = RRF_K,
) -> list[Hit]:
    """把融合结果和正文拼成 Hit。

    取不到正文的候选直接跳过（理论上不该发生，但两段 SQL 之间
    有可能被另一条 DELETE 插进来，这时候跳过比塞一个空正文进去好）。
    """
    out: list[Hit] = []
    for f in fused:
        row = details.get(f.chunk_id)
        if row is None:
            logger.warning("候选块 %s 在详情查询里没找到，已跳过", f.chunk_id)
            continue

        meta = row[6] or {}
        if isinstance(meta, str):
            # psycopg2 正常情况下会把 jsonb 反序列化成 dict，
            # 但如果哪天换成不自动反序列化的驱动，这里会拿到字符串。
            # 静默地当成空 dict 会让"带过滤的检索永远查不到东西"，
            # 所以宁可多一次解析。
            meta = json.loads(meta)

        out.append(
            Hit(
                chunk_id=f.chunk_id,
                document_id=int(row[1]),
                chunk_index=int(row[2]),
                title=row[3] or "",
                source=row[4],
                content=row[5] or "",
                section=tuple(meta.get("section") or ()),
                meta=meta,
                content_hash=row[7] or "",
                # 先用归一化的 RRF 分数占位；有精排时会被覆盖
                score=round(f.rrf / _rrf_max(rrf_k), 6),
                rrf=f.rrf,
                # 分支→字段映射在 types.py，查不到即为未召回。
                **_branch_fields(f),
                rerank_score=None,
            )
        )
    return out


def _branch_fields(f: Fused) -> dict[str, Any]:
    """把分支名次和原始分展开成 Hit 字段。

    未跑的分支也写入 None，满足 Hit 构造要求；映射缺失由 self-check 兜底。
    """
    out: dict[str, Any] = dict.fromkeys(_BRANCH_RANK_FIELD.values())
    out.update(dict.fromkeys(_BRANCH_SCORE_FIELD.values()))

    for name, rank in f.ranks.items():
        field_name = _BRANCH_RANK_FIELD.get(name)
        if field_name:
            out[field_name] = rank
    for name, score in f.scores.items():
        field_name = _BRANCH_SCORE_FIELD.get(name)
        if field_name:
            out[field_name] = score
    return out


def _rerank(query: str, candidates: list[Hit]) -> tuple[list[Hit], bool]:
    """用交叉编码器重排；模型不可用时原样返回。

    返回 (结果, 是否真的重排过)，后者会写入 backend 说明。
    """
    from app.agents import reranker

    # 喂全部候选取其精华，不要只给 top_k，否则精排没有发挥空间。
    ranked = reranker.rerank(query, [c.content for c in candidates], top_k=len(candidates))
    if ranked is None:
        # 没有精排，保持 RRF 的顺序（candidates 本来就是按 rrf 降序建的）
        return candidates, False

    out: list[Hit] = []
    for idx, score in ranked:
        if not (0 <= idx < len(candidates)):
            # 越界下标会导致结果错位，必须记录并跳过。
            logger.error("reranker 返回了越界的下标 %s（候选数 %s），已跳过", idx, len(candidates))
            continue
        original = candidates[idx]
        out.append(
            Hit(
                **{
                    **_as_dict(original),
                    "score": round(score, 6),
                    "rerank_score": round(score, 6),
                }
            )
        )
    return out, True


def _as_dict(c: Hit) -> dict[str, Any]:
    """把 frozen dataclass 转成 dict，便于替换个别字段。"""
    return {f: getattr(c, f) for f in c.__dataclass_fields__}


def _backend(names: list[str], reranked: bool) -> str:
    """根据本次实际运行的分支生成 backend 描述，必须属于 policy.BACKENDS。

    用实际运行列表而不是配置开关，避免"配置说 sparse、实际跑的是 trgm"。
    无分支时返回 NO_BRANCH，不返回空串。
    """
    if not names:
        return NO_BRANCH

    parts = [BRANCH_LABEL[n] for n in names]
    if reranked:
        parts.append("rerank")
    return "+".join(parts)


def _to_vector(vec: list[float]) -> str:
    """向量转 pgvector 字面量；复用 vector_store 的实现，避免两处精度不一致。"""
    from app.rag.vector_store import to_pg_vector

    return to_pg_vector(vec)


def _json_or_none(filters: dict[str, str] | None) -> str | None:
    """过滤条件转 JSON；空过滤返回 None，供 SQL 的 IS NULL 判断使用。"""
    if not filters:
        return None
    return json.dumps(filters, ensure_ascii=False)


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)
