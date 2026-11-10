"""验证降级路径：把向量库停掉之后，检索会退化成什么、以及会不会报错。

## 为什么这件事要单独测

**降级路径是平时永远走不到的代码。** 它只在真出故障的那一天执行一次，
而那时候没人有余裕去调试它——所以必须在一切正常的时候主动走一遍。

它坏起来也特别隐蔽：正常路径照样工作，测试全绿，直到某天 pgvector 挂了，
用户看到的是 500，或者更糟——看到一段看起来正常但其实是文档标题拼出来的
胡话。**这两种失败在代码里只差一个分支。**

## 它做什么

会**真的停掉 `travel-pgvector` 容器**，跑完在 `finally` 里重启，
所以中途 Ctrl+C 也不会把容器留在停着的状态。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_degradation.py

⚠️ 跑之前确认没有别的进程在用向量库（比如正在跑评估）。
"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTAINER = "travel-pgvector"
QUERY = "成都熊猫基地门票多少"

_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}")
        if detail:
            print(f"     {detail}")


def _docker(*args: str) -> None:
    subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True, timeout=120
    )


def _wait_ready(timeout: float = 90.0) -> bool:
    """等向量库真的能接受连接。

    ⚠️ `docker start` 返回不代表库能用了——pg 还要跑一遍恢复。
    不等的话"恢复"那一步会随机失败，看起来像代码坏了。
    """
    from app.rag import vector_store

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        vector_store.reset()
        if vector_store.get_vector_engine() is not None:
            return True
        time.sleep(2)
    return False


def main() -> int:
    from app.agents import retriever
    from app.database import SessionLocal
    from app.rag import vector_store

    db = SessionLocal()
    try:
        # ── 0. 起点：确认现在是好的 ──
        print("\n[0] 前提：向量库正常时")
        vector_store.reset()
        normal = retriever.search_full(db, QUERY, top_k=5)
        check(
            "backend 里含 pgvector",
            "pgvector" in normal.backend,
            f"实际 backend={normal.backend!r}",
        )
        check(
            "context 非空（有【编号】的上下文）",
            bool(normal.context) and "【" in normal.context,
            f"context 长度 {len(normal.context)}",
        )
        check("citations 非空", bool(normal.citations))
        print(f"     backend={normal.backend!r} hits={normal.hits}")

        try:
            # ── 1. 容器在应用运行中途挂掉 ──
            # 这是最真实的故障形态：engine 已经缓存好了，连接池里还有活连接。
            print(f"\n[1] 停掉 {CONTAINER}（engine 已缓存，模拟运行中途挂掉）")
            _docker("stop", CONTAINER)

            down = retriever.search_full(db, QUERY, top_k=5)
            check("没有抛异常", True)
            check(
                f"backend 变成 {retriever.BACKEND_FALLBACK!r}",
                down.backend == retriever.BACKEND_FALLBACK,
                f"实际 backend={down.backend!r}",
            )
            check(
                "context 为空（降级路没有候选池，给不出编号上下文）",
                down.context == "",
                f"实际长度 {len(down.context)}",
            )
            check("citations 为空", down.citations == [])
            check(
                "note 如实说明了降级",
                "降级" in down.note or "fallback" in down.note,
                f"实际 note={down.note!r}",
            )
            print(f"     backend={down.backend!r} hits={down.hits}")
            print(f"     note={down.note}")

            # ── 2. engine 没缓存的情况（进程刚起来，容器就是停的）──
            print("\n[2] reset 之后再查（模拟进程启动时容器就是停的）")
            vector_store.reset()
            cold = retriever.search_full(db, QUERY, top_k=5)
            check(
                f"backend 也是 {retriever.BACKEND_FALLBACK!r}",
                cold.backend == retriever.BACKEND_FALLBACK,
                f"实际 backend={cold.backend!r}",
            )
            check("没有抛异常", True)

            # ── 3. 空查询：不该走降级 ──
            print("\n[3] 空查询（这条【不该】说成降级）")
            empty = retriever.search_full(db, "   ", top_k=5)
            check(
                "backend 是 empty-query 而不是降级",
                empty.backend == "empty-query",
                f"实际 backend={empty.backend!r}——空查询被判成后端故障的话，"
                f"用户会看到'知识库不可用'，而真实原因是问题本身是空的",
            )

        finally:
            # 不管上面怎么炸，容器都要回来
            print(f"\n[9] 重启 {CONTAINER}")
            _docker("start", CONTAINER)

        # ── 4. 恢复 ──
        print("\n[4] 恢复之后")
        if not _wait_ready():
            check("向量库在 90 秒内恢复", False, "可能还在恢复，手工确认一下")
            return 1
        back = retriever.search_full(db, QUERY, top_k=5)
        check(
            "backend 回到含 pgvector",
            "pgvector" in back.backend,
            f"实际 backend={back.backend!r}",
        )
        check(
            "context 又有了",
            bool(back.context) and "【" in back.context,
            f"context 长度 {len(back.context)}",
        )

    finally:
        db.close()

    # 这一段【不碰 Docker】，所以放在容器重启之后
    _check_pipeline_error()
    _check_trgm_fallback()

    print(f"\n{'=' * 60}")
    print(f"通过 {_passed}，失败 {_failed}")
    return 1 if _failed else 0


def _check_pipeline_error() -> None:
    """流水线**内部**出错时会怎样——和"容器停了"是两件完全不同的事。

    ═══════════════════════════════════════════════════════════════════
     ⚠️ 为什么这一条必须单独造一个故障来测
    ═══════════════════════════════════════════════════════════════════

    上面测的都是"向量库没了"，那条路的触发条件是**环境的**，
    停一下容器就能复现。

    但"取正文 / 精排 / 组装"这三步出错是**代码的**问题——正常跑永远不会发生，
    所以那段 except 是**平时一行都不执行**的代码。它写错了没人会发现，
    直到某天真出 bug 的那一天，而那一天正是最不该再出别的问题的时候。

    这就是"降级路径是平时永远走不到的代码"那句话的极端情形：
    容器停了至少还能手动复现，这个连复现都要先写个故障注入。

    所以这里 monkeypatch 掉 `_fetch_details` 让它抛异常——那是"库好好的、
    召回也成功了，但取详情时崩了"，正是这个分支存在的理由。
    """
    print("\n[5] 流水线【内部】出错（库是好的，是代码崩了）")
    from app.agents import retriever
    from app.database import SessionLocal
    from app.rag import pipeline, policy, vector_store

    db = SessionLocal()
    original = pipeline._fetch_details
    try:
        def boom(*_args, **_kwargs):
            raise RuntimeError("故障注入：假装取正文时崩了")

        pipeline._fetch_details = boom
        # ⚠️ 改写要关掉。开着的话这里会去调 LLM，而故障注入测的是检索层，
        # 中间夹一次网络调用只会让失败原因变得二义。
        result = retriever.search_full(db, QUERY, top_k=5, rewrite_query=False)
    finally:
        pipeline._fetch_details = original
        db.close()

    # 1) 不能抛异常出去——图节点靠它保证"检索挂了也不影响整轮对话"
    check("没有抛异常", True)

    # 2) 图这条路【要降级】，而不是把空结果当成正常结果
    check(
        f"backend 变成 {retriever.BACKEND_FALLBACK!r}（图里要退化，不能装作查到了）",
        result.backend == retriever.BACKEND_FALLBACK,
        f"实际 backend={result.backend!r}",
    )
    check(
        "note 如实说明了这次不是正常检索",
        "降级" in result.note,
        f"实际 note={result.note!r}",
    )
    check("context 为空", result.context == "")

    # 3) ⚠️ 最关键的一条：pipeline.search() 自己【必须】从返回值上说出
    #    "我内部崩了"，而不是抛异常或返回 None。
    #
    #    因为这两件事对调用方的含义完全不同：
    #      None               → 向量库不可用（环境问题）
    #      backend=内部错误    → 代码有问题
    #    接口（rag_service）要靠这个区别给出不同的话术——"容器没起"
    #    和"代码有 bug"对调用方是两件事，而它们的 HTTP 码都是 503。
    original2 = pipeline._fetch_details
    try:
        pipeline._fetch_details = boom
        outcome = pipeline.search(QUERY, top_k=5, rewrite_query=False)
    finally:
        pipeline._fetch_details = original2

    check(
        "pipeline.search 返回了 outcome 而不是 None（内部错误要能和'库不可用'分开）",
        outcome is not None,
        "返回 None 的话，调用方只会以为向量库挂了，而真实原因是代码 bug",
    )
    if outcome is not None:
        check(
            f"backend 正好是 {policy.PIPELINE_ERROR!r}",
            outcome.backend == policy.PIPELINE_ERROR,
            f"实际 backend={outcome.backend!r}",
        )
        check(
            "branches 仍然带回来了（召回是成功的，坏在后面）",
            bool(outcome.branches.get("vec")),
            f"branches={outcome.branches}",
        )
        check(
            "没有把向量库标记为 down（代码 bug 不该伪装成环境抖动）",
            vector_store.get_vector_engine() is not None,
            "标记 down 会让它 60 秒后自愈，然后没人知道这里出过错",
        )


def _check_trgm_fallback() -> None:
    """嵌入模型加载不出来时会怎样。**现在有三层，要分别验证。**

    ═══════════════════════════════════════════════════════════════════
     ⚠️ 这一条测的是"降级链有没有被换默认分支弄断"
    ═══════════════════════════════════════════════════════════════════

    第一层：**BM25**。生产默认第二路是它，纯 CPU、不碰模型。
    稠密路没了它还在，而且有 IDF——这是它比 trgm 强的地方。

    第二层：**trgm**。只有第二路被显式换成 sparse/none（或 BM25 也没
    召回任何东西）时才轮到它。稠密路和稀疏路共用 bge-m3 一个底座，
    模型挂了两条一起没；trgm 是纯 SQL 的三元组相似度，不需要模型。

    第三层：`_keyword_fallback`（文档标题匹配）。前两层都没了才走到，
    `retriever` 的注释里写了为什么它只能算最后兜底。

    所以这里分两段测：
        a) 默认配置（BM25）→ backend 以 bm25 开头，给出块级正文
        b) 显式 second_branch=sparse → backend 以 trgm 开头
    """
    print("\n[6] 嵌入模型加载不出来（BM25 先顶，sparse 配置下 trgm 顶）")
    from app.agents import embedding, retriever
    from app.database import SessionLocal
    from app.rag import pipeline

    original = embedding.get_model

    # ---- a) 默认配置：BM25 是纯 CPU 的，不依赖模型 ----
    db = SessionLocal()
    try:
        # ⚠️ 换成 `get_model` 而不是 `embed_query`：
        # 前者才是真实故障点（权重没下、显存不够），而且它同时覆盖
        # 稠密路和稀疏路——稀疏头就是从这个模型里拿到 tokenizer 和底座的。
        embedding.get_model = lambda: None  # type: ignore[assignment]
        result = retriever.search_full(db, QUERY, top_k=5, rewrite_query=False)
    finally:
        embedding.get_model = original
        db.close()

    check("backend 以 bm25 开头（模型挂了，BM25 仍然在）",
          result.backend.startswith("bm25"),
          f"实际 backend={result.backend!r}——"
          f"说明模型不可用时 BM25 没有接上，又退回了 {retriever.BACKEND_FALLBACK!r}")
    check("没有抛异常", True)
    check("context 非空（BM25 给得出块级上下文）",
          bool(result.context),
          f"context 长度 {len(result.context)}")
    check("note 里说明了稠密路没跑起来",
          "BM25" in result.note or "稠密" in result.note,
          f"实际 note={result.note!r}")
    # ⚠️ 这一条是最要紧的：区分"真的扫了正文"和"只是换了个地方匹配标题"。
    check("返回的是【块】不是文档级的标题（section 有值）",
          any(h.section for h in result.retrieved),
          "所有命中的 section 都是空的，说明走的还是文档级标题匹配")

    # ---- b) 第二路不是 BM25 时，trgm 仍然是最后一块块级召回 ----
    db = SessionLocal()
    try:
        embedding.get_model = lambda: None  # type: ignore[assignment]
        outcome = pipeline.search(
            QUERY,
            top_k=5,
            rewrite_query=False,
            policy=pipeline.RetrievalPolicy(second_branch="sparse"),
        )
    finally:
        embedding.get_model = original
        db.close()

    check("second_branch=sparse 且模型不可用时 backend 以 trgm 开头",
          outcome is not None and outcome.backend.startswith("trgm"),
          f"实际 backend={getattr(outcome, 'backend', None)!r}——"
          f"trgm 那一路没接上的话，模型故障时就没有块级召回了")
    check("trgm 分支真的召回了块",
          bool(outcome and outcome.branches.get("trgm")),
          f"branches={getattr(outcome, 'branches', None)}")


if __name__ == "__main__":
    sys.exit(main())
