"""检索层公共类型：命中形状及对外投影。

Hit = 身份 + 正文 + 证据。证据字段用于区分语义命中和关键词兜底；
命中级证据只看幸存结果，整路召回情况在 `pipeline.SearchOutcome.branches`。

本模块只依赖标准库和 `app.rag.policy`，避免与 assemble/pipeline 循环依赖。
"""

from dataclasses import dataclass, fields
from typing import Any

from app.rag.policy import SNIPPET_CHARS

# 命中级名次从 1 开始；判断是否召回要写 `is not None`，不能做真值判断。
# 实际断言在 eval_quality._check_rank_base()。

# 分支名 → 名次字段，唯一映射处。
_BRANCH_RANK_FIELD: dict[str, str] = {
    "vec": "vec_rank",
    "sparse": "sparse_rank",
    "bm25": "bm25_rank",
    "trgm": "trgm_rank",
}

# 分支名 → 原始分字段。vec 没有：余弦距离和归一化 score 方向相反，存进来只会混淆。
_BRANCH_SCORE_FIELD: dict[str, str] = {
    "sparse": "sparse_score",
    "bm25": "bm25_score",
    "trgm": "trgm_score",
}


@dataclass(frozen=True)
class Hit:
    """检索层唯一的命中形状；对外结构通过 to_finding_chunk/to_api_hit 显式投影。

    frozen=True：检索结果是只读快照。新字段的去向由 unprojected_fields() 检查。
    """

    # ---- 身份：这是哪一块 ----
    chunk_id: int
    document_id: int
    chunk_index: int
    title: str
    source: str | None
    section: tuple[str, ...]
    """章节路径，如 `("第三天", "交通")`；内部用 tuple，投影到边界时转 list。"""
    meta: dict[str, Any]
    """块的元数据，进过滤的就是它（`meta @> filters`）。内部用，不对外。"""
    content_hash: str
    """正文的哈希，组装阶段靠它去重。内部用，不对外。"""

    # ---- 正文：这一段说了什么 ----
    content: str
    """完整正文，开头保留【文档 > 章节】面包屑；对外投影时再处理。"""

    # ---- 证据：为什么它排在前面 ----
    score: float
    """最终分数，0~1；精排时是重排分，否则是 RRF 分，可用 rerank_score is None 区分。"""
    rrf: float
    """RRF 融合分，只看名次。"""
    vec_rank: int | None
    """向量路名次（1 起）；None 表示未召回。"""
    sparse_rank: int | None
    """稀疏路名次（1 起）；None 表示未跑或未召回。"""
    sparse_score: float | None
    """稀疏路内积分，无上界、跨语料不可比，仅用于排查。"""
    bm25_rank: int | None
    """BM25 路名次（1 起）；嵌入不可用时 BM25 单独运行仍有值。"""
    bm25_score: float | None
    """BM25 原始分，无上界、跨语料不可比，仅用于排查。"""
    trgm_rank: int | None
    """三元组兜底路名次（1 起）；None 表示未跑或未召回。"""
    trgm_score: float | None
    """三元组相似度，0~1；只有这一路的分数有界，不要跨分支比较。"""
    rerank_score: float | None
    """重排模型原始分；None 表示本次没有精排。"""

    @property
    def section_key(self) -> str:
        """章节字符串，规则必须与 chunking.Chunk.section_key 保持一致。"""
        return " > ".join(self.section)

    @property
    def evidence(self) -> dict[str, int]:
        """返回召回本条的 (分支, 名次)，如 {"vec": 3, "sparse": 11}。

        只包含真正召回的分支；判断是否存在要用 `"vec" in hit.evidence`。
        用 dict 统一表达，新增一路只需改 `_BRANCH_RANK_FIELD`。
        """
        out: dict[str, int] = {}
        for branch, field_name in _BRANCH_RANK_FIELD.items():
            rank = getattr(self, field_name)
            if rank is not None:
                out[branch] = rank
        return out


def snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    """给下游的短片段；截断时加省略号，明确表示内容不完整。"""
    text = (content or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "……"


# 投影函数只返回普通 dict，不 import 上层类型，保持 app/rag 的依赖方向。


def to_finding_chunk(hit: Hit) -> dict[str, Any]:
    """投影为图里 finding.chunks 的元素。

    给截图后的 snippet，完整资料走 finding.context；保留面包屑，模型靠它
    判断价格等数字属于哪个景点。
    """
    return {
        "document_id": hit.document_id,
        "title": hit.title,
        "source": hit.source,
        "section": list(hit.section),
        "snippet": snippet(hit.content),
        "score": hit.score,
    }


def to_api_hit(hit: Hit) -> dict[str, Any]:
    """投影为接口返回的命中，额外带各分支名次和分数供排查。

    整列为 null 表示本次没跑那一路，不是"什么都没召回"；具体看 backend。
    """
    return {
        "chunk_id": hit.chunk_id,
        "document_id": hit.document_id,
        "title": hit.title,
        "source": hit.source,
        "section": list(hit.section),
        "content": hit.content,
        "score": hit.score,
        "rerank_score": hit.rerank_score,
        "sparse_score": hit.sparse_score,
        "bm25_score": hit.bm25_score,
        "trgm_score": hit.trgm_score,
        "vec_rank": hit.vec_rank,
        "sparse_rank": hit.sparse_rank,
        "bm25_rank": hit.bm25_rank,
        "trgm_rank": hit.trgm_rank,
    }


# Hit 上刻意不投影的字段及理由。
_INTERNAL_FIELDS: dict[str, str] = {
    "chunk_index": "内部排序/相邻扩展用，两个出口都不需要",
    "meta": "可能含语料内部标记，不进对外结构",
    "content_hash": "组装去重用，不对外",
    "rrf": "融合中间值，两个出口都有更合适的分数",
}


def unprojected_fields() -> list[str]:
    """返回既未投影也未登记为内部字段的 Hit 字段。

    自检断言为空；新增字段必须二选一：加入投影函数，或登记并说明原因。
    """
    projected = _sample_hit_projection()
    declared = set(_INTERNAL_FIELDS)
    return sorted(
        f.name for f in fields(Hit) if f.name not in projected | declared
    )


def _sample_hit_projection() -> set[str]:
    """两个投影函数输出字段的并集，仅用于读取键名。"""
    placeholder = Hit(
        chunk_id=0,
        document_id=0,
        chunk_index=0,
        title="",
        source=None,
        section=(),
        meta={},
        content="",
        content_hash="",
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
    return set(to_finding_chunk(placeholder)) | set(to_api_hit(placeholder))
