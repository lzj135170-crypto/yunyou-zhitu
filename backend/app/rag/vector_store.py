"""向量库连接与结构：engine、可用性探测、建表 DDL。

表可以随时删掉重灌，因此没有迁移逻辑。两条硬约束：
1. 向量表不能继承 app.database.Base，否则 Alembic 会在业务库执行 vector DDL；
   DDL 只在入库脚本调用，不在应用启动时执行。
2. 向量必须转成字符串传参，psycopg2 会把 list[float] 适配成 PG 数组。

跨库没有外键：删除文档时必须同时处理两边的数据。
"""

import logging
import threading
import time
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 连接失败后静默 60 秒再重试：向量库是独立进程，常会自行恢复；
# 永久记忆会把后端钉死在降级路上。60 秒也避免每个请求都撞 3 秒超时。
DOWN_RETRY_SECONDS = 60.0

# 向量库端口/库名，与业务库分开。
VECTOR_DB_PORT = 5433
VECTOR_DB_NAME = "travel_vector"

_engine: Any = None
_down_until: float = 0.0
_lock = threading.Lock()

# document_chunks 行数缓存。候选池按语料比例计算，不能每次 count(*)；
# 入库后需重启进程或调用 reset() 刷新。
_corpus_size: int | None = None


def resolve_url() -> str:
    """返回向量库连接串。

    优先使用 VECTOR_DATABASE_URL，否则从 DATABASE_URL 推导端口和库名。
    不写死默认连接串：密码只存在 .env，不进入版本库。
    推导失败返回空串，由 get_vector_engine 统一走降级。
    """
    explicit = (settings.VECTOR_DATABASE_URL or "").strip()
    if explicit:
        return explicit

    from sqlalchemy.engine import make_url

    try:
        url = make_url(settings.DATABASE_URL)
    except Exception as e:
        logger.warning("DATABASE_URL 解析失败，无法推导向量库连接串：%s", e)
        return ""

    # 必须 hide_password=False：str(url) 会把 *** 当成真实密码，导致认证失败且难查。
    return url.set(port=VECTOR_DB_PORT, database=VECTOR_DB_NAME).render_as_string(
        hide_password=False
    )


def get_vector_engine():
    """返回向量库 engine；连不上返回 None 并静默 60 秒。

    首次调用会实际连接，避免懒加载把连接失败拖到检索流水线中间才暴露。
    """
    global _engine, _down_until

    if _engine is not None:
        return _engine
    if time.monotonic() < _down_until:
        return None

    with _lock:
        # 双重检查：等锁的这段时间里可能已经有人建好了
        if _engine is not None:
            return _engine
        if time.monotonic() < _down_until:
            return None
        try:
            _engine = _build()
            logger.info("向量库已连接：%s", _masked_url())
            return _engine
        except Exception as e:
            mark_down(f"连接失败：{e}")
            return None


def _build():
    """建 engine 并验证真的能连上。"""
    from sqlalchemy import create_engine, text

    engine = create_engine(
        resolve_url(),
        pool_pre_ping=True,
        # 短查询、小连接池；向量库挂掉时不会堆积等待连接。
        pool_size=2,
        max_overflow=2,
        connect_args={
            # 避免防火墙丢包时挂到 TCP 超时，拖垮降级时间。
            "connect_timeout": 3,
        },
    )
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def mark_down(reason: str) -> None:
    """标记向量库不可用并静默 60 秒；连接失败和查询失败都要调用。"""
    global _down_until
    _down_until = time.monotonic() + DOWN_RETRY_SECONDS
    logger.warning("向量库不可用，%d 秒内不再重试：%s", int(DOWN_RETRY_SECONDS), reason)


def is_available() -> bool:
    """向量库当前是否可用。

    首次调用会实际连接；成功或失败后均缓存/退避，不要在热路径反复调用。
    当前没有调用方，保留给健康检查和诊断脚本。
    """
    return get_vector_engine() is not None


def corpus_size() -> int:
    """返回缓存的 document_chunks 行数；不可用或统计失败时返回 0。

    0 同时表示"语料为空"和"统计失败"，调用方都按下界处理；
    不要改成抛异常，那会多出一条故障路径。
    """
    global _corpus_size

    if _corpus_size is not None:
        return _corpus_size

    engine = get_vector_engine()
    if engine is None:
        return 0

    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            n = conn.execute(text("SELECT count(*) FROM document_chunks")).scalar()
        _corpus_size = int(n or 0)
        return _corpus_size
    except Exception as e:
        # 只记 warning 并返回 0：算不出语料大小不该让检索整体失败，
        # 候选池退回下界（policy.CAND_MIN）照样能跑。
        logger.warning("统计语料块数失败，候选池按默认下界算：%s", e)
        return 0


def reset() -> None:
    """丢掉缓存的 engine、语料大小和失败标记，供测试使用。"""
    global _engine, _down_until, _corpus_size
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _down_until = 0.0
        _corpus_size = None


def close() -> None:
    """释放连接池。和 checkpointer.close() 一样，由 app/main.py 的 lifespan 在退出时调用。"""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None


def _masked_url() -> str:
    """返回脱敏后的连接串；日志和报错信息一律使用它，不打印明文密码。

    用 SQLAlchemy 的 hide_password，不手写按 @ 切分（密码可能含 @）。
    """
    from sqlalchemy.engine import make_url

    try:
        return make_url(resolve_url()).render_as_string(hide_password=True)
    except Exception:
        # 连接串本身就解析不了时，只报一个笼统的占位，
        # 不要退回去打印原串——那正好是要避免的事。
        return "(连接串无法解析)"


def to_pg_vector(vec: list[float]) -> str:
    """把向量转成 pgvector 字面量 "[0.1,0.2]"。

    必须传字符串：psycopg2 会把 list[float] 适配成 PG 数组。
    用 str(float) 保留最短往返表示，截断会引入误差。
    """
    return "[" + ",".join(map(str, vec)) + "]"


# ---------- 建表 ----------
#
# 全部幂等（IF NOT EXISTS），重复执行没有副作用。
#
# 只在 scripts/ingest_knowledge.py 里调用，【不在应用启动时调用】——
# 见模块开头第 1 条。

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm 只给"模型加载不出来"那条降级路用，见下面那两处注释。
-- 和 vector 一样要扩展，一样需要超级用户。
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS document_chunks (
    id           BIGSERIAL   PRIMARY KEY,

    -- 业务库 documents.id。跨库，没有外键，也不可能有。
    -- 这个值对不对只能靠入库脚本保证，所以 meta 里额外存了一份 doc_hash
    -- 供事后对账（见 ingest_knowledge.py 的 --verify）。
    document_id  INTEGER     NOT NULL,
    chunk_index  INTEGER     NOT NULL,

    -- 从 documents 冗余过来的展示字段。
    -- 冗余不是为了省一次查询，而是让【检索结果完全不依赖业务库】：
    -- 业务库挂了、某行被删了，检索照样能说清这段内容出自哪份文档。
    title        TEXT        NOT NULL DEFAULT '',
    source       TEXT,

    content      TEXT        NOT NULL,
    meta         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    content_hash CHAR(64)    NOT NULL,

    -- 章节的字符串形式（和 meta->'section' 是同一份信息，但能建索引能等值查）。
    -- 用途是"行程 POI 补充"：按 POI 的 section_key 直接取该章节的块。
    -- 有了这一列，"把这一节的块全取出来"就是一句带索引的等值查询。
    -- 没有章节的块是空串，它们会被归成一组——这是对的，因为确实都是"无章节"。
    section_key  TEXT        NOT NULL DEFAULT '',


    embedding    vector(1024),

    -- bge-m3 稀疏词权重，下标是 token_id+1；映射见 sparse.to_literal。
    -- 旧数据为 NULL，查询侧用 IS NOT NULL 兜底；补数据用 --backfill-sparse。
    chunk_sparse sparsevec(250002),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 保证同一文档同一序号唯一，并发入库也不会产生重复块。
    UNIQUE (document_id, chunk_index)
);

-- 老库需要 ALTER 补列，CREATE TABLE IF NOT EXISTS 不会自动补。
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS token_count INTEGER NOT NULL DEFAULT 0;

-- BM25 词频表；CASCADE 保证整篇删除时旧词频一起消失。
CREATE TABLE IF NOT EXISTS chunk_terms (
    chunk_id BIGINT  NOT NULL REFERENCES document_chunks(id) ON DELETE CASCADE,
    term     TEXT    NOT NULL,
    tf       INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, term)
);

-- df 统计按 term 分组，没有它每次 BM25 都要全表扫。
CREATE INDEX IF NOT EXISTS idx_chunk_terms_term
    ON chunk_terms (term);

-- 向量索引；当前数据量下 PG 可能选择顺序扫描，那是精确结果，属正常。
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- 元数据过滤；jsonb_path_ops 足够覆盖 @> 且更小更快。
CREATE INDEX IF NOT EXISTS idx_chunks_meta_gin
    ON document_chunks USING gin (meta jsonb_path_ops);

-- 稀疏路索引，使用内积；当前规模下顺序扫描仍是精确结果。
CREATE INDEX IF NOT EXISTS idx_chunks_sparse_hnsw
    ON document_chunks USING hnsw (chunk_sparse sparsevec_ip_ops);

-- pg_trgm 块级兜底，只在嵌入模型不可用时使用。
-- 不用 PG 全文检索：ts_rank 没有 IDF；trgm 无需词表或分词对称。
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm
    ON document_chunks USING gin (content gin_trgm_ops);

-- (document_id, section_key) 复合索引，供同文档同章节查询使用。
CREATE INDEX IF NOT EXISTS idx_chunks_doc_section
    ON document_chunks (document_id, section_key);
"""


def ensure_schema() -> None:
    """建扩展、表、索引，幂等；失败直接抛异常。

    入库要么全成要么全不成，半入库会让部分内容静默搜不到。
    """
    from sqlalchemy import text

    engine = get_vector_engine()
    if engine is None:
        raise RuntimeError(
            f"向量库连不上（{_masked_url()}）。"
            "先确认容器起来了：docker compose -f deploy/pgvector/docker-compose.yml up -d"
        )

    # DDL 必须一条条执行：psycopg2 的 execute 不支持一次多条语句。
    # 按分号切是安全的，因为这个常量里没有函数体、没有字符串字面量里的分号。
    with engine.begin() as conn:
        for statement in (s.strip() for s in SCHEMA_SQL.split(";")):
            if statement:
                conn.execute(text(statement))
    logger.info("向量库表结构就绪")
