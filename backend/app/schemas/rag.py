"""知识库检索接口的请求和响应结构。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.policy import validate_filters

# 单次检索最多返回多少条。
#
# 上限 20 不是拍的：上下文组装的预算是 3000 字，而块的平均长度约 200 字，
# 20 条已经会超出预算，后面几条第会被组装阶段丢掉。
# 允许要更多但组不出来，等于给调用方一个看起来有效、实际无效的参数。
MAX_TOP_K = 20


class RagSearchRequest(BaseModel):
    """检索请求。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(
        min_length=1,
        max_length=500,
        description="要检索的问题。可以是不完整的一句话（'那住宿呢'），"
        "传了 conversation_id 时会结合上下文补全",
    )

    conversation_id: int | None = Field(
        default=None,
        description="会话 id。传了就取最近几条消息做 Query 改写（消解'那边''呢'这类指代）；"
        "不传就是单轮检索，问题必须自包含",
    )

    filters: dict[str, str] | None = Field(
        default=None,
        description="元数据过滤，如 {'city': '成都'}。"
        "受控字段只有 city。这里传的值【优先于】模型从问题里抽的——"
        "调用方说的是确定的事实，模型是从自然语言里猜的",
    )

    @field_validator("filters")
    @classmethod
    def _check_filters(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        """按受控词表校验 filters，非法值直接 422。

        接口边界 fail-fast；流水线内部的 merge_filters 只丢弃并记日志，
        两者分工是有意的。doc_type 已不支持过滤，避免调用方按旧文档传值。
        """
        _accepted, dropped = validate_filters(v)
        if dropped:
            raise ValueError("；".join(dropped))
        return v

    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K, description="返回几条")

    rewrite: bool = Field(
        default=True,
        description="要不要做 Query 改写。关掉能省一次模型调用（约 1 秒），"
        "但多轮里省略主语的问题会搜不到东西",
    )

    with_context: bool = Field(
        default=False,
        description="要不要返回拼好的上下文文本。"
        "上下文是给下游模型消费的，不是给人看的——前端展示用 hits 就好",
    )


class RagHit(BaseModel):
    """一条检索命中。

    除正文和分数外还带诊断字段，用于区分语义检索和降级结果。
    """

    chunk_id: int
    document_id: int
    title: str
    source: str | None = None
    section: list[str] = Field(default_factory=list, description="章节路径，如 ['第三天', '交通']")

    content: str = Field(description="块的正文，开头一行是【文档 > 章节】形式的面包屑")
    score: float = Field(description="最终分数，0~1。有精排时是重排模型的打分，没有时是 RRF 分数")

    rerank_score: float | None = Field(
        default=None, description="重排模型的原始打分。为 null 表示【这次没有精排】"
    )
    vec_rank: int | None = Field(
        default=None, description="在向量路里的名次（从 1 起）。为 null 表示向量路没召回"
    )

    # 下面几项和 `types.to_api_hit` 一一对应。之前只投影到 dict、没进 schema，
    # Pydantic 默认忽略多余字段，所以接口上一直看不到它们——排查分支归因时
    # 只能去读日志。补全。
    sparse_rank: int | None = Field(
        default=None, description="在 BGE-M3 稀疏路里的名次（从 1 起）"
    )
    sparse_score: float | None = Field(default=None, description="稀疏路内积分，无上界，仅排查用")
    bm25_rank: int | None = Field(
        default=None, description="在 BM25 路里的名次（从 1 起）；只有评估配置会跑这一路"
    )
    bm25_score: float | None = Field(default=None, description="BM25 原始分，无上界，仅排查用")
    trgm_rank: int | None = Field(
        default=None, description="在 pg_trgm 兜底路里的名次（从 1 起）"
    )
    trgm_score: float | None = Field(default=None, description="三元组相似度，0~1，仅排查用")


class RagCitation(BaseModel):
    """一条出处。和 context 里的【编号】一一对应。"""

    index: int
    chunk_id: int
    document_id: int
    title: str
    source: str | None = None
    section: list[str] = Field(default_factory=list)


class RagSearchResponse(BaseModel):
    """检索结果。"""

    query: str = Field(description="用户原本问的")
    rewritten: str = Field(description="实际拿去检索的（改写后的）。和 query 相同时表示没改写或改写没生效")
    filters: dict[str, str] = Field(
        default_factory=dict, description="实际生效的过滤条件（模型抽的和调用方传的合并之后）"
    )

    hits: list[RagHit] = Field(default_factory=list)

    has_material: bool = Field(
        default=True,
        description="检索到的东西够不够格当资料用。"
        "false 表示一条都没有、或者最相关的那条低于拒答阈值——"
        "此时 hits 里可能仍有内容（为了排查保留），但它们**不适合当资料**。"
        "⚠️ 只在有精排时才会判出 false；没有精排时恒为 true（RRF 分数不含相关性信息）",
    )

    context: str | None = Field(
        default=None, description="拼好的上下文，带【编号】和出处。只在 with_context=true 时返回"
    )
    citations: list[RagCitation] = Field(
        default_factory=list, description="和 context 里编号对应的出处"
    )

    backend: str = Field(
        description="这次实际走了哪条路，如 'pgvector+sparse+rerank'。"
        "降级时这里会如实变短——它是调用方判断结果可信度的唯一依据"
    )
    timings_ms: dict[str, float] = Field(
        default_factory=dict, description="各阶段耗时（毫秒）：rewrite / recall / fetch / rerank / assemble"
    )
