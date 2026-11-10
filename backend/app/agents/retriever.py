"""知识检索层：RAG Agent 的唯一取数入口。

真正的实现都在 `app/rag/`，本文件是门面：决定走哪条路，以及路不通时怎么退。
`search_full()` 永远返回 SearchResult，不返回 None，并把实际路径写进 backend/note。

降级顺序：
- 全都可用：稠密 + BM25 → RRF → 精排；
- 重排不可用：双路 → RRF；
- 嵌入不可用：第二路 BM25 仍可跑；第二路不是 BM25 时退到 pg_trgm；
- 流水线或向量库不可用：退到本文件的 `_keyword_fallback`，只匹配 documents 标题。

空结果不等于降级：pipeline 返回 None 才是整条路不可用；返回空候选表示路通了、
只是没检索到，不能拿降级路的标题凑数。`db` 只给降级路使用。
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.document import Document
from app.rag.assemble import Citation
from app.rag.types import Hit

logger = logging.getLogger(__name__)

# 降级到最后一级时的 backend 值。
BACKEND_FALLBACK = "keyword-fallback"

# 词汇表定义在 policy.BACKENDS；这里只导入常用取值。
from app.rag.policy import NO_BRANCH, PIPELINE_ERROR  # noqa: E402

# 降级路没有块，用 0 作为文档级结果的哨兵；document_chunks.id 从 1 开始。
FALLBACK_CHUNK_ID = 0


@dataclass(frozen=True)
class SearchResult:
    """一次检索的完整产出：结果、上下文、诊断字段和说明。"""

    retrieved: list[Hit]
    """按相关度排序的结果；降级时是文档级结果，见 `_keyword_fallback`。"""

    context: str
    """拼好的上下文。降级或没检索到内容时是空字符串，不是 None。"""

    citations: list[Citation]
    """和 context 里【编号】一一对应的出处。没组装时是空列表。"""

    rewritten: str
    """本次实际拿去检索的话。和原问题相同时表示没改写或改写没生效。"""

    filters: dict[str, str] = field(default_factory=dict)
    """本次实际生效的元数据过滤。降级时是空的。"""

    backend: str = BACKEND_FALLBACK
    """本次实际走了哪条路。取值见模块 docstring 的那张表。"""

    note: str = ""
    """backend 的人话版本，给 agent_runs.output 和用户看。"""

    hits: int = 0
    """真实召回的条数。可能大于 len(retrieved)——retrieved 会被 top_k 截断。"""

    has_material: bool = True
    """这些结果够不够格当资料；False 时 context/citations 清空，retrieved 保留用于排查。"""

    timings_ms: dict[str, float] = field(default_factory=dict)

    branches: dict[str, list[int]] = field(default_factory=dict)
    """各路完整召回列表，只供评估和排查，不参与排序；降级路为空。"""


def search_full(
    db: Session,
    query: str,
    *,
    history: list[tuple[str, str]] | None = None,
    filters: dict[str, str] | None = None,
    top_k: int = 5,
    rewrite_query: bool = True,
) -> SearchResult:
    """检索知识库，返回完整结果和诊断信息，永远不返回 None。

    走不通就降级，并在 backend/note 里如实说明。空结果不是降级。
    `rewrite_query` 用于测试和评估：改写会调 LLM，不关掉就无法复现。
    """
    if not query or not query.strip():
        return SearchResult(
            retrieved=[],
            context="",
            citations=[],
            rewritten=query or "",
            backend="empty-query",
            note="没有可检索的问题（空查询）",
        )

    from app.rag import pipeline

    try:
        outcome = pipeline.search(
            query,
            filters=filters,
            top_k=top_k,
            history=history,
            rewrite_query=rewrite_query,
        )
    except Exception:
        # 降级必须留堆栈：只打 warning 会让 SQL 错误看起来像"检索结果变差"。
        logger.exception("混合检索异常，退回关键词检索")
        return _fallback_result(db, query, top_k)

    if outcome is None:
        # 向量库整条路不可用（engine 或召回查询失败）。
        return _fallback_result(db, query, top_k)

    if outcome.backend == PIPELINE_ERROR:
        # 图里降级：下游是模型，能看到 context 为空，退到标题匹配仍能回答。
        # 接口不降级（rag_service 抛 503）：它的调用方会把标题当成正文。
        logger.error("检索流水线内部出错，图里退回关键词检索：%s", outcome.backend)
        return _fallback_result(db, query, top_k)

    note = _describe(outcome.backend)
    if outcome.poi_expanded:
        # 补充块不在 retrieved 里，说明来源，避免排查时误以为 context 多了。
        note += f"；行程 POI 已补充：{'、'.join(outcome.poi_expanded)}"

    return SearchResult(
        retrieved=list(outcome.candidates),
        # 不够格当资料时清空 context/citations，但保留 retrieved：
        # 前者防止模型照低分内容编，后者用于区分"检索到了但不相关"和"没检索到"。
        context=outcome.assembled.context if outcome.has_material else "",
        citations=list(outcome.assembled.citations) if outcome.has_material else [],
        rewritten=outcome.rewritten,
        filters=outcome.filters,
        backend=outcome.backend,
        note=note,
        has_material=outcome.has_material,
        hits=max(len(outcome.candidates), outcome.assembled.used),
        timings_ms=outcome.timings_ms,
        branches=outcome.branches,
    )


def _fallback_result(db: Session, query: str, top_k: int) -> SearchResult:
    """退到最后一层：查业务库的 documents 元数据表。"""
    chunks = _keyword_fallback(db, query, top_k)
    return SearchResult(
        retrieved=chunks,
        context="",
        citations=[],
        rewritten=query,
        backend=BACKEND_FALLBACK,
        note=_describe(BACKEND_FALLBACK),
        hits=len(chunks),
        # 降级路不套用 REFUSAL_THRESHOLD：Jaccard 分和精排分是两套尺度，
        # 有一篇匹配就算有资料。
        has_material=bool(chunks),
    )


def _describe(backend: str) -> str:
    """把本次实际走的 backend 翻成人话，用于展示和排查。"""
    table = {
        BACKEND_FALLBACK: (
            "降级检索：向量库不可用，退回关键词匹配 documents 元数据表"
            "（只能匹配标题和来源，匹配不到正文）"
        ),
        PIPELINE_ERROR: (
            "降级检索：混合检索流水线内部出错，退回关键词匹配"
            "（具体原因在后端日志里，这一条说明本次结果不是正常检索来的）"
        ),
        NO_BRANCH: (
            "没有可用的检索路径：嵌入模型不可用，而且第二路被显式关掉了"
            "（second_branch=none，那是评估专用的配置）"
        ),
        # trgm 能扫正文，但排序质量不如语义检索，说明里要讲清楚。
        "trgm+rerank": (
            "正文关键词兜底 + 精排（嵌入模型不可用；这一路是三元组相似度，"
            "能扫到正文但排序不如语义检索）"
        ),
        "trgm": (
            "正文关键词兜底（嵌入模型不可用，重排模型也不可用；"
            "三元组相似度排序，质量明显低于语义检索）"
        ),
        # 稀疏路和稠密路共用 bge-m3，通常同生共死；单 sparse 组合仍保留说明。
        "pgvector+sparse+rerank": "混合检索（pgvector 向量 + BGE-M3 稀疏词权重 + 交叉编码器精排）",
        "pgvector+sparse": "混合检索（pgvector 向量 + BGE-M3 稀疏词权重；重排模型不可用，没有精排）",
        # BM25 是生产默认的第二路（不依赖嵌入模型）。
        "pgvector+bm25+rerank": "混合检索（pgvector 向量 + BM25 关键词 + 交叉编码器精排）",
        "pgvector+bm25": "混合检索（pgvector 向量 + BM25 关键词；重排模型不可用，没有精排）",
        # BM25 有 IDF，嵌入不可用时比 trgm 更可靠。
        "bm25+rerank": "BM25 关键词检索 + 精排（嵌入模型不可用，稠密路没跑起来）",
        "bm25": "BM25 关键词检索（嵌入模型不可用，稠密路没跑起来；也没有精排）",
        "sparse+rerank": "块级稀疏词权重检索 + 精排（稠密路没跑起来）",
        "sparse": "块级稀疏词权重检索（稠密路没跑起来）",
        # 没跑第二路。只在评估里出现（second_branch=none），见 policy.SECOND_NONE。
        "pgvector+rerank": "只有 pgvector 向量路 + 精排（第二路被关掉，评估配置）",
        "pgvector": "只有 pgvector 向量路（第二路被关掉，评估配置）",
        "empty-query": "没有可检索的问题（空查询）",
    }
    # 查不到就返回机器串，不编造描述；self-check 会核对词表覆盖。
    return table.get(backend, f"检索（{backend}）")


# 以下为最后一层降级：向量库整体不可用时，按文档标题匹配。
# 不删的原因：Docker 容器可能启动慢、被误停或未启动，
# 退化成"按标题找到文档"比完全找不到好。


def _bigrams(text: str) -> set[str]:
    """把文本切成相邻两字的集合。

    中文无需分词器，两字滑窗能匹配查询里嵌着的词；先去掉标点和空格。
    """
    cleaned = "".join(ch for ch in text if ch.isalnum())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _keyword_fallback(db: Session, query: str, top_k: int = 5) -> list[Hit]:
    """按标题/来源做关键词匹配，只作为最后一层降级。

    documents 只有元数据，没有正文；这一路也是字面匹配，无法处理同义表达。
    返回文档级 Hit，chunk_id 固定为 FALLBACK_CHUNK_ID，下游据此识别降级结果。
    """
    query_grams = _bigrams(query)
    if not query_grams:
        return []

    # 二字片段用 OR 交给 SQL 粗筛，避免把整张 documents 表拉进内存。
    conditions = [Document.title.ilike(f"%{g}%") for g in query_grams]
    conditions += [Document.source.ilike(f"%{g}%") for g in query_grams]

    rows = db.query(Document).filter(or_(*conditions)).limit(top_k * 4).all()

    results: list[Hit] = []
    for doc in rows:
        doc_grams = _bigrams(f"{doc.title or ''} {doc.source or ''}")
        matched = len(query_grams & doc_grams)
        if not matched:
            # LIKE 是粗筛，用集合交集再确认一次。
            continue
        results.append(
            Hit(
                chunk_id=FALLBACK_CHUNK_ID,
                document_id=doc.id,
                chunk_index=0,
                title=doc.title,
                source=doc.source,
                section=(),
                meta={},
                content_hash="",
                # 没有正文，用标题充当内容。
                content=doc.title,
                # Jaccard 相似度对长查询更稳定，量纲与混合检索一致（0~1）。
                score=round(matched / len(query_grams | doc_grams), 3),
                rrf=0.0,
                # 这一路没有走任何召回分支，所有分支名次和分数都为空。
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

    results.sort(key=lambda c: c.score, reverse=True)
    return results[:top_k]
