"""BM25 关键词检索打分。

PG 的 ts_rank 没有 IDF，常见词和稀有词权重相同，旅游语料下会把精确匹配
压成噪音，因此手写 BM25。词频来自 chunk_terms，df 按查询词实时统计，
N/avgdl 从 document_chunks 聚合。

IDF 末尾的 +1 防止高频词变成负分。k1=1.2、b=0.75 为通行默认值，
实测网格搜索收益可忽略，调参预算应放在分词和查询文本上。
"""

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 词频饱和系数。越大越"词频说了算"，越小越快饱和。
K1 = 1.2

# 长度归一化强度。0 = 完全不归一化，1 = 完全归一化。0.75 是通行默认值。
B = 0.75

# 查询词上限。LLM 改写时可能吐出一大串关键词，全塞进来会让 SQL 变重，
# 而且 IDF 极低的词加进去只会稀释排序。30 个词已经远超正常查询的规模。
MAX_TERMS = 30


# SQL 绑定参数必须写 CAST(:x AS type)：SQLAlchemy 会把 :x::type 误解析成 :x。
# 向量参数也不能直接传 list，psycopg2 会适配成 PG 数组，须先用 to_pg_vector 转字符串。
_BM25_SQL = text(
    """
WITH qt AS (
    -- 查询词列表。用 unnest 展开成行，下面按 term 关联。
    SELECT unnest(CAST(:terms AS text[])) AS term
),
stats AS (
    -- N/avgdl。过滤条件必须与 df、scored 完全一致，否则 IDF 统计不自洽且不报错。
    SELECT count(*)::float8 AS n,
           GREATEST(avg(token_count), 1)::float8 AS avgdl
    FROM document_chunks
    WHERE CAST(:filters AS jsonb) IS NULL
       OR meta @> CAST(:filters AS jsonb)
),
df AS (
    -- 只统计查询词的 df；实时计算避免额外统计表与主表不同步。
    SELECT ct.term, count(*)::float8 AS df
    FROM chunk_terms ct
    JOIN document_chunks c ON c.id = ct.chunk_id
    WHERE ct.term IN (SELECT term FROM qt)
      AND (CAST(:filters AS jsonb) IS NULL OR c.meta @> CAST(:filters AS jsonb))
    GROUP BY ct.term
),
scored AS (
    SELECT ct.chunk_id,
           SUM(
               ln((s.n - d.df + 0.5) / (d.df + 0.5) + 1)
               * (ct.tf * (:k1 + 1))
               / (ct.tf + :k1 * (1 - :b + :b * c.token_count / s.avgdl))
           ) AS score
    FROM chunk_terms ct
    JOIN qt ON qt.term = ct.term
    JOIN df d ON d.term = ct.term
    JOIN document_chunks c ON c.id = ct.chunk_id
    CROSS JOIN stats s
    WHERE CAST(:filters AS jsonb) IS NULL OR c.meta @> CAST(:filters AS jsonb)
    GROUP BY ct.chunk_id
)
SELECT chunk_id, score
FROM scored
-- 第二排序键保证同分结果稳定，测试可复现。
ORDER BY score DESC, chunk_id
LIMIT :kw_k
"""
)


def search(
    engine: Any,
    terms: list[str],
    filters: dict[str, Any] | None = None,
    limit: int = 30,
) -> list[tuple[int, float]]:
    """按 BM25 给块打分，返回按分数降序的 [(chunk_id, score)]。

    engine 为空或 terms 为空时返回 []；空列表和"没命中"由调用方区分。
    """
    if not terms:
        return []

    # 去重并限长。重复词会让 tf 之外的项重复累加，把分数抬高一倍。
    unique = list(dict.fromkeys(terms))[:MAX_TERMS]

    import json

    params = {
        "terms": unique,
        "filters": json.dumps(filters, ensure_ascii=False) if filters else None,
        "k1": K1,
        "b": B,
        "kw_k": limit,
    }

    with engine.connect() as conn:
        rows = conn.execute(_BM25_SQL, params).fetchall()

    return [(int(r[0]), float(r[1])) for r in rows]
