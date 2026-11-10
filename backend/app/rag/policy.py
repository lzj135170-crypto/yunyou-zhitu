"""检索策略的唯一定义：常量、比例规则和取值词表。

这些值是代码级设计决定，由 `eval/RESULTS.md` 的实测数据确定；
修改应带评估，不做成运行时环境变量。本模块刻意不 import 任何依赖，
让图节点、检索和接口都能安全读取。
"""

# 喂给模型的对话条数：6 条约等于最近三轮。三处使用，必须保持一致。
HISTORY_WINDOW = 6

# 每路召回占语料的比例。按比例而非固定条数，语料变化时自动适配；
# 0.23 使 130 块语料仍取出原来的 30 条。
CAND_FRACTION = 0.23

# 候选池上下界：小语料给精排留素材，大语料控制 SQL 和延迟。
CAND_MIN = 20
CAND_MAX = 200


def cand_per_branch(corpus_size: int) -> int:
    """每路召回条数；语料不可用时返回 CAND_MIN，避免下游误判成空候选池。"""
    if corpus_size <= 0:
        return CAND_MIN
    return max(CAND_MIN, min(CAND_MAX, round(CAND_FRACTION * corpus_size)))


def cand_total(corpus_size: int) -> int:
    """融合后送进精排的条数，规则同 `cand_per_branch`。"""
    return cand_per_branch(corpus_size)


# RRF 的 k，原论文默认值。这一层只生成候选，最终顺序由精排决定；
# 实测 k=10/60/100 的结果一致，不必在这里调参。
RRF_K = 60

# 精排使用的查询文本：
# union = 最小清洗原话 + 改写（默认）；minimal / rewritten / raw 为单段查询。
# union 必须去重：改写失败时两段相同，重复拼接会把分数压到拒答阈值以下。
RERANK_QUERIES: tuple[str, ...] = ("union", "minimal", "rewritten", "raw")

# 生产默认值，由 eval_quality 的实测结果决定。
RERANK_QUERY_DEFAULT = "union"

# 上下文预算（字符）。约 2500~3000 token；超预算时整块丢弃，不截断句子。
CONTEXT_BUDGET_CHARS = 3000

# 单条命中在 snippet 里的截断长度；完整资料走 context 通道。
SNIPPET_CHARS = 500

# 图里一次取几条。实测值，改动前先看 eval/RESULTS.md：
#   top_k=5 → hit@5=0.9868；top_k=8 → hit@8=1.0000；top_k=12 增益已饱和。
# 取 8 是为了让 76 条可答用例全部至少有一条相关资料。
TOP_K = 8


# 拒答阈值：最相关的一条低于此分，就不把检索结果当资料。
#
# 检索器永远返回 top-k，没有"不知道"状态，阈值补上这个判断。
# 它只对精排分有效：RRF 分只由名次决定，不含相关性信息；没有精排时
# `pipeline._has_material()` 直接返回 True（fail-open），代价是重排不可用时拒答失效。
#
# 0.084 是零代价区间 (0.0575, 0.1112) 的中点，0.111 太贴近可答最低分。
# 不能提到改写生效时的 0.57：改写失败后分数会回到旧分布，会大量误伤可答查询。
# 10 条不可答用例仍有 4 条高于阈值，分数分不出"相关"和"能回答"。
REFUSAL_THRESHOLD = 0.084


# 检索结局词表：`backend` 字段的唯一合法取值。
# eval_quality 的 self-check 会遍历这里，核对 retriever 是否都有对应说明。

# 混合检索流水线自身出错（取正文 / 精排 / 组装），区别于"没找到"和"向量库不可达"。
# 有了这个取值，retriever 和 rag_service 才能分别做降级和 503，而不是静默退化。
PIPELINE_ERROR = "pipeline-error"

# 一路都没跑起来，只可能出现在 second_branch=none 且嵌入模型不可用时。
NO_BRANCH = "no-branch"

_OUTCOMES: frozenset[str] = frozenset(
    {
        "empty-query",
        PIPELINE_ERROR,
        NO_BRANCH,
        # 最后一级降级：查业务库 documents 元数据表。
        # 正版定义在 retriever.BACKEND_FALLBACK；policy 不能反向 import，
        # 自检里有一条断言核对两处一致。
        "keyword-fallback",
    }
)

# 分支名是 SearchOutcome.branches、Hit.evidence 和 backend 串的共同主键，
# 用常量避免拼写错误导致某一项静默消失。
BRANCH_VEC = "vec"
BRANCH_SPARSE = "sparse"

# 手写 BM25，生产第二路。它和 sparse 互斥：两者都是词法召回，
# 一起跑只会提高候选重合度。选 BM25 是因为纯 CPU，嵌入模型挂掉时仍可用。
BRANCH_BM25 = "bm25"

# 不依赖模型的块级召回：content 上的 pg_trgm，只在嵌入模型加载失败时兜底。
BRANCH_TRGM = "trgm"

# 分支名 → backend 串里的可读标签；映射只在这里定义，避免日志出现两种写法。
BRANCH_LABEL: dict[str, str] = {
    BRANCH_VEC: "pgvector",
    BRANCH_SPARSE: "sparse",
    BRANCH_BM25: "bm25",
    BRANCH_TRGM: "trgm",
}

# 能同时存活的分支组合。稠密和稀疏共用 bge-m3，trgm 只在两者都不可用时顶上；
# 不做笛卡尔积，避免产生 pgvector+trgm 这类不会发生的组合。
BRANCH_COMBOS: tuple[tuple[str, ...], ...] = (
    (BRANCH_VEC,),
    (BRANCH_VEC, BRANCH_SPARSE),
    (BRANCH_VEC, BRANCH_BM25),
    (BRANCH_BM25,),
    (BRANCH_TRGM,),
)

# 第二路可以是哪些真正会执行的路径，不含 none。
SECOND_BRANCHES: tuple[str, ...] = (BRANCH_SPARSE, BRANCH_BM25)

# 生产默认第二路，和 eval 共用，防止两处默认值漂移。
SECOND_DEFAULT = BRANCH_BM25

# 不跑第二路，用于把"候选池变化"和"分支变化"分开评估。
SECOND_NONE = "none"

SECOND_CHOICES: tuple[str, ...] = (*SECOND_BRANCHES, SECOND_NONE)

# 第二路使用的查询文本：minimal 保留原话词项，raw 完全原样，rewritten 允许增删词。
# 生产用 minimal：干净输入下与 raw 指标相同，脏输入下会规范化零宽和全角字符。
# 只影响 BM25 查询文本；filters（city）仍来自改写结果。
SECOND_QUERIES: tuple[str, ...] = ("minimal", "raw", "rewritten")

# 生产默认查询文本，pipeline 和 eval 共用。
SECOND_QUERY_DEFAULT = "minimal"


def _composed_backends() -> frozenset[str]:
    """把分支组合 × 是否有精排展开成全部合法的 backend 字符串。"""
    out: set[str] = set()
    for combo in BRANCH_COMBOS:
        labels = [BRANCH_LABEL[b] for b in combo]
        for rerank in (("rerank",), ()):
            out.add("+".join([*labels, *rerank]))
    return frozenset(out)


# 所有合法取值；pipeline._backend() 只能产出集合里的值，自检有断言核对。
BACKENDS: frozenset[str] = _OUTCOMES | _composed_backends()


# 元数据受控词表。放在 policy 是为了让接口校验不必依赖文本处理模块，
# 也让 chunking（使用方）和检索过滤共享同一份定义。

# city 以省份 / 大区域 / 重点旅游城市为主：一份《云南七日游》同时覆盖
# 昆明、大理、丽江，只能挂"云南"。词表外的值会让 meta 过滤永远匹配不到。
CITIES = (
    "成都",
    "西安",
    "云南",
    "三亚",
    "北京",
    "川西",
    "上海",
    "杭州",
    "重庆",
    "厦门",
    "南京",
    "青岛",
    "广州",
    "深圳",
    "苏州",
    "武汉",
    "长沙",
    "桂林",
    "张家界",
    "哈尔滨",
    "洛阳",
    "敦煌",
)

# 文档类型。作用是让"住宿推荐"这类问题能收敛到住宿类文档，
# 而不是在攻略正文里碰运气。
DOC_TYPES = ("攻略", "景点", "美食", "交通", "住宿")

# 只允许按 city 过滤。刻意不含 doc_type：它标的是文档体裁，
# 攻略类文档横跨所有体裁，硬过滤实测会掉 0.15 召回。
FILTER_FIELDS = ("city",)


def validate_filters(filters: dict[str, str] | None) -> tuple[dict[str, str], list[str]]:
    """按受控词表校验过滤条件，返回 (合法条件, 被丢弃的原因)。

    静默丢弃会让调用方误以为过滤生效；返回原因才能报 422 或记日志。
    不做模糊匹配：把"成都市区"猜成"成都"会掩盖调用方的错误。
    """
    if not filters:
        return {}, []

    ok: dict[str, str] = {}
    dropped: list[str] = []

    for key, value in filters.items():
        if not value:
            # 空值等于没传。不算错——调用方用一个空串表示"这个条件不要了"
            # 是个常见写法。
            continue
        if key not in FILTER_FIELDS:
            if key == "doc_type":
                dropped.append(
                    "doc_type 不支持过滤：它标的是文档体裁，而攻略类文档横跨所有体裁，"
                    "硬过滤实测掉 0.15 召回。详见 rewrite.merge_filters 的注释"
                )
            else:
                dropped.append(
                    f"不认识的过滤字段 {key!r}；受控字段只有 {list(FILTER_FIELDS)}"
                )
            continue
        if key == "city" and value not in CITIES:
            dropped.append(
                f"city={value!r} 不在受控词表里；只能是 {list(CITIES)}。"
                "词表外的值会让 meta 匹配永远为空，表现为「知识库没有资料」"
            )
            continue
        ok[key] = value

    return ok, dropped
