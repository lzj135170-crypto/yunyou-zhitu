"""把 knowledge/ 下的 Markdown 语料灌进知识库（业务库 + 向量库）。

用法（在 backend 目录下执行）：

    # 先看看会切成什么样，不连库、不加载模型
    .venv/Scripts/python.exe scripts/ingest_knowledge.py --dry-run

    # 正式入库
    .venv/Scripts/python.exe scripts/ingest_knowledge.py

    # 内容没变也强制重建（改了切块参数之后要这样跑一次）
    .venv/Scripts/python.exe scripts/ingest_knowledge.py --force

    # 顺带清掉源文件已经不存在了的文档
    .venv/Scripts/python.exe scripts/ingest_knowledge.py --prune

    # 看看三个库对不对得上
    .venv/Scripts/python.exe scripts/ingest_knowledge.py --verify

    # 改了 BM25 的分词之后：只重建词频表，不动块、id 和向量
    .venv/Scripts/python.exe scripts/ingest_knowledge.py --rebuild-terms

⚠️ 这个脚本是【唯一会写向量库】的地方。
   应用运行时只读它，不会写。

═══════════════════════════════════════════════════════════════════════
 一条必须遵守的纪律：嵌入和检索必须走同一条归一化路径
═══════════════════════════════════════════════════════════════════════

本脚本调 app.agents.embedding.embed_texts()，它内部是
normalize_embeddings=True。

【绝对不要】在这里写 get_model().encode(...) 直接编码。
那会绕开归一化，让库里的向量和查询时算出来的向量不在同一个尺度上，
于是余弦相似度的排序整体失真——而这一切【不会有任何报错】，
只表现为"检索结果莫名其妙地不准"，你会先去怀疑切块、怀疑提示词、
怀疑模型，最后才想到是入库那一步少了个参数。

**稀疏向量同理**：走 `app.rag.sparse.encode`，它和稠密路复用同一个模型实例、
同一个 tokenizer，而且入库侧和查询侧调的是**同一个函数**
（`sparse.to_literal` 与 `sparse.encode` 都只有一份）。

（BM25 重新成为生产第二路之后，这条纪律也回来了，但换了个形式：
入库和查询都调 `app.rag.tokenize.tokenize()`，两侧的**函数**是同一个，
所以不会一边切 A 一边切 B。真正的坑变成了"改了函数但没重建旧数据的词频表"——
旧块的 `chunk_terms` 还是按旧规则切的，查询按新规则切，两边对不上，
而且**不报错**。所以改完分词必须跑一次 `--rebuild-terms`，
它只重算 `token_count` 和 `chunk_terms`，不动块 id、正文和向量。）

═══════════════════════════════════════════════════════════════════════
 写入顺序是刻意设计的，任何中断点重跑都能自愈
═══════════════════════════════════════════════════════════════════════

    ① 业务库 INSERT/复用 documents 行，拿到 document_id
    ② 向量库 一个事务内：DELETE 旧的块 → INSERT 新的
    ③ 业务库 UPDATE chunk_count 和 meta.doc_hash

doc_hash 是"这一步成功了"的标记，【必须最后写】。

在 ② 和 ③ 之间崩溃：doc_hash 还是旧的 → 下次跑判定"内容变了" → 重做一遍。
多花一次嵌入，结果正确。

反过来如果先写 doc_hash 再写块，崩溃就会留下
"标记说已完成、实际没有块"的【永久性错位】——下次跑时会跳过这篇文档，
而那篇文档在检索里永远查不到，且没有任何报错。
"""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402
from collections import Counter  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.agents.embedding import dimension, embed_texts, is_available  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.rag import chunking as C  # noqa: E402
from app.rag import sparse  # noqa: E402
from app.rag import vector_store as vs  # noqa: E402
from app.rag.tokenize import tokenize  # noqa: E402

# Windows 控制台默认 cp936，打印 ❌/✅ 这类字符会直接 UnicodeEncodeError。
# 用 replace 兜底，避免因为一行日志中断整个入库流程。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

# 一次编码多少块。
#
# 16 是折中：太小会浪费批处理的前后处理开销，太大会在显存不足时直接 OOM。
# bge-m3 在 600 字的块上，16 条一批对消费级显卡是安全的。
DEFAULT_BATCH_SIZE = 16


def main() -> int:
    args = _parse_args()

    files = _scan(settings.KNOWLEDGE_DIR)
    if not files:
        print(f"❌ {settings.KNOWLEDGE_DIR} 下没有 .md 文件")
        return 1

    # --dry-run 在【所有前置检查之前】返回：
    # 它的用途正是"还没配好环境时先看看切块效果"，
    # 所以它不能连库、不能加载模型。
    if args.dry_run:
        return _dry_run(files)

    if not _preflight():
        return 1

    engine = vs.get_vector_engine()

    if args.reset_schema:
        _reset_schema(engine)
        # ⚠️ 重置结构【必须】同时强制重建，这两件事拆不开。
        #
        # 踩过一次：--reset-schema 把两张向量表删了，然后脚本一路报"未变更"，
        # 结果**向量库空着，而脚本说一切正常**。
        #
        # 原因：判断"这篇要不要重灌"依据的是 doc_hash，而 doc_hash 存在
        # 【业务库】的 documents.meta 里。删向量表动不到它，
        # 所以每一篇都还是"已经入过库"的状态，一条都不会重插。
        #
        # 这是"派生物 + 标记放在主库里"这个模式的固有陷阱：
        # **删掉派生数据不会让标记失效**，而标记错了的时候，
        # 系统会安静地认为自己是最新的。
        # 所以这里由代码把两件事绑死，不靠使用者记得同时加 --force。
        args.force = True

    vs.ensure_schema()

    if args.verify:
        return _verify(engine)

    if args.backfill_sparse:
        return _backfill_sparse(engine, args.batch_size)

    if args.rebuild_terms:
        return _rebuild_terms(engine)

    return _ingest(files, engine, args)


def _backfill_sparse(engine, batch_size: int) -> int:
    """给 `chunk_sparse` 还是 NULL 的块补上稀疏向量。

    ## 为什么需要它（而不是直接重跑入库）

    `chunk_sparse` 是 Phase 3 才加的列，而库里已经有一份数据了。
    本文件的既有纪律是"改表结构就 --reset-schema 重灌"，但那在这里**不合适**：
    `document_chunks.id` 是 BIGSERIAL，重灌之后会按插入顺序重新分配，
    而 `pipeline._fuse` 的排序键是 `(-rrf, chunk_id)`——**同分时的先后由 id 决定**。

    重灌一遍等于顺手改了一批同分候选的顺序。**换召回分支这个实验要一份
    逐位可比的基线**，让一个和分支无关的东西先动一遍，之后任何 delta
    都说不清是谁造成的。

    所以这一条路只动新列：行、id、content、embedding 全部原样。

    ## 幂等

    只选 `chunk_sparse IS NULL` 的行，所以跑第二遍是空操作。
    将来真做了"增量加列"的场景，这也是对的做法。

    ## ⚠️ 编码失败时【不写半份】

    和 `_write_chunks` 一样：拿不到稀疏向量就整体放弃并返回 1。
    "一部分块有稀疏向量、一部分没有"这个状态从外面看不出来，
    只会表现为"有些内容明明在知识库里却搜不到"——那正是入库脚本
    最该避免的失败形态。
    """
    from app.rag import sparse

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, content FROM document_chunks "
                "WHERE chunk_sparse IS NULL ORDER BY id"
            )
        ).fetchall()

    if not rows:
        print("没有需要补的块（chunk_sparse 已经全都有值）")
        _verify(engine)
        return 0

    print(f"需要补稀疏向量的块：{len(rows)}")

    updates: list[dict[str, object]] = []
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        encoded = sparse.encode([r[1] for r in batch])
        if encoded is None:
            print(
                "❌ 稀疏编码失败，本次未写入任何一行。\n"
                "   确认 models/bge-m3/sparse_linear.pt 在，且嵌入模型能加载。"
            )
            return 1
        updates.extend(
            {"i": int(r[0]), "v": sparse.to_literal(w)}
            for r, w in zip(batch, encoded)
        )

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE document_chunks SET chunk_sparse = CAST(:v AS sparsevec) "
                "WHERE id = :i"
            ),
            updates,
        )

    print(f"✅ 补完 {len(updates)} 块")
    print()
    _verify(engine)
    return 0


def _rebuild_terms(engine) -> int:
    """按当前分词规则重建 BM25 的词频表。**不动块、id、正文和向量。**

    ## 为什么需要它

    `chunk_terms` 和 `token_count` 是入库时按 `tokenize()` 算出来的。
    改了分词规则（比如从 `lcut` 换成 `lcut_for_search`）之后，
    新查询按新规则切词，旧词频表还是按旧规则切的——两边对不上，
    **BM25 不会报错，只会让一部分内容莫名其妙搜不到**。

    重跑一次完整入库也能修，但那会 DELETE + INSERT 所有块，
    `document_chunks.id` 重新分配；而 `pipeline._fuse` 的同分排序键是
    `(-rrf, chunk_id)`，换一批 id 等于顺手改了一批同分候选的顺序。
    做对照实验时，那会让"改了分词"和"id 变了"两件事混在一起。

    所以这条路只动两张派生数据：`token_count` 和 `chunk_terms`。
    幂等——跑第二遍结果相同。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, content FROM document_chunks ORDER BY id")
        ).fetchall()

    if not rows:
        print("向量库里没有块，先跑一次完整入库")
        return 1

    print(f"重建 {len(rows)} 块的 BM25 词频表（分词：app.rag.tokenize.tokenize）")

    updates: list[dict[str, object]] = []
    term_rows: list[dict[str, object]] = []
    for chunk_id, content in rows:
        tokens = tokenize(content or "")
        updates.append({"i": int(chunk_id), "n": len(tokens)})
        term_rows.extend(
            {"chunk_id": int(chunk_id), "term": term, "tf": tf}
            for term, tf in Counter(tokens).items()
        )

    # 一个事务：DELETE 旧词频 → 批量 INSERT → 更新 token_count。
    # 中途失败会整体回滚，不会留下"一半新规则一半旧规则"的词表——
    # 那种状态从外面看不出来，只会表现为排序悄悄变差。
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chunk_terms"))
        for i in range(0, len(term_rows), 1000):
            conn.execute(
                text(
                    "INSERT INTO chunk_terms (chunk_id, term, tf) "
                    "VALUES (:chunk_id, :term, :tf)"
                ),
                term_rows[i : i + 1000],
            )
        conn.execute(
            text("UPDATE document_chunks SET token_count = :n WHERE id = :i"),
            updates,
        )

    print(f"✅ {len(rows)} 块，{len(term_rows)} 行词频，token_count 已更新")
    print()
    _verify(engine)
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="把 knowledge/ 下的语料灌进知识库")
    p.add_argument("--dry-run", action="store_true", help="只解析和切块，打印统计，不连库")
    p.add_argument("--force", action="store_true", help="内容没变也重建")
    p.add_argument("--prune", action="store_true", help="删除源文件已不存在的文档（破坏性）")
    p.add_argument("--verify", action="store_true", help="只对账，不写入")
    p.add_argument(
        "--reset-schema",
        action="store_true",
        help="先 DROP 掉两张表再重建（改了 vector_store.py 的表结构时必须加这个，见下方注释）",
    )
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p.add_argument(
        "--backfill-sparse",
        action="store_true",
        help="只补 chunk_sparse 为 NULL 的块，不动行、id 和稠密向量",
    )
    p.add_argument(
        "--rebuild-terms",
        action="store_true",
        help="只按当前分词规则重建 BM25 的词频表（chunk_terms / token_count），"
        "不动块、id、正文和向量。改了 app/rag/tokenize.py 之后必须跑一次。",
    )
    return p.parse_args()


def _scan(root: str) -> list[Path]:
    """扫描语料目录。排序是为了让跑两次的顺序一致，输出可比对。"""
    return sorted(Path(root).glob("*.md"))


def _preflight() -> bool:
    """前置检查。这个脚本【不降级】，任何一条不满足就直接退出。

    理由：半入库的库比没入库更难查。部分文档有块、部分没有，
    表现是"有些内容明明在知识库里却搜不到"，而没有任何报错——
    排查时会先去怀疑检索逻辑，方向从一开始就错了。
    """
    ok = True

    if not is_available():
        print(f"❌ 嵌入模型不可用：{settings.EMBEDDING_MODEL}")
        print("   权重没下载或路径不对。没有它就没法生成向量，入库无从谈起。")
        ok = False
    else:
        dim = dimension()
        # 维度必须和建表时的 vector(1024) 一致。
        # 不一致时 INSERT 会当场报错（这是好事），但在这里拦住能给出更清楚的提示。
        if dim != 1024:
            print(f"❌ 嵌入模型维度是 {dim}，但向量表是按 vector(1024) 建的")
            print("   换模型的话要 DROP TABLE 重跑本脚本（向量库是派生物，重灌很便宜）")
            ok = False

    engine = vs.get_vector_engine()
    if engine is None:
        print(f"❌ 向量库连不上：{vs._masked_url()}")
        print("   先起容器：docker compose -f deploy/pgvector/docker-compose.yml up -d")
        ok = False

    return ok


# 会随时间变化、因此【禁止】进知识库的 doc_type。
#
# ⚠️ 为什么这条要卡在入库侧，而不是检索侧：
#
# 向量库里那份天气一旦灌进去，检索时它和攻略、景点介绍没有任何区别——
# 一样被当成【稳定的事实】取出来交给模型。而用户看到"明天多云转晴"时
# **不知道那是哪天取的**。三周前的预报和今天的预报，在库里长得一模一样。
#
# 这不是质量问题，是欺骗。而且它在检索层拦不住：数据已经在库里了，
# 除非在每个块上标明"这条是实时数据、几小时前过期"，否则检索层
# 没有任何依据去区别对待它。
#
# 所以闸门放在唯一的口子上——入库。检索侧不用做任何判断。
#
# 实时数据该走哪：走独立的数据源。天气现在由 weather 节点直连 Open-Meteo，
# 那条路拿到的数据带着"什么时候取的"这个上下文，而且不会沉淀进知识库。
#
# ⚠️ 这个集合【只负责给出一条好懂的报错】。真正拦得住东西的是下面那条
# 受控词表校验（`doc_type not in DOC_TYPES` 也拒绝）。原因：
#
# 黑名单永远比现实慢一步。"天气"被挡住了，写"天气预报""实时天气""天气2024"
# 照样进得来——实测过，"天气预报"在黑名单下是【放行】的。
# 而白名单不需要我想全所有叫法：不在受控词表里的一律不收。
#
# 两者都留着的理由：白名单能挡住，但报错只能说"不在词表里"，
# 而这份天气文档需要的解释是"实时数据为什么不能进知识库"。
# 一个说不清理由的拦截，下一个人会想办法绕过去。
VOLATILE_DOC_TYPES = frozenset({"天气", "票价", "班次", "余票", "汇率", "实时"})


def _reset_schema(engine) -> None:
    """把两张向量表删掉，让 ensure_schema 重建。

    ⚠️ 为什么需要这个开关：

    `vector_store.ensure_schema()` 里的 DDL 全是 `CREATE TABLE IF NOT EXISTS`，
    而 **IF NOT EXISTS 只管"表在不在"，不管"表的结构对不对"**。
    所以往 SCHEMA_SQL 里加一列（比如这次的 section_key）之后，
    对一个已经存在的表它什么都不会做——那一列永远也不会出现。

    而后果不是"少一列"，是**用它查的代码会报 column does not exist**。
    更糟的是如果那列有默认值语义（比如 section_key 默认空串），
    没有它的时候行程 POI 补充会查不到任何章节，静默地什么都不补。

    那为什么不做正经的 migration？因为这个库是【派生物】——
    里面的东西全部能从 knowledge/ 下的 Markdown 重灌出来，
    重灌一次的代价是几十秒。写一套 migration 机制的复杂度
    远远超过它要保护的那点东西。这和 vector_store.py 模块注释里
    "改表结构就 DROP 重跑"的说法是一致的。

    ⚠️ 这是破坏性操作，所以要显式加 --reset-schema 才会执行。
    不做成"检测到结构变了就自动重建"——自动 DROP 一张表这种事，
    出错的时候没有任何挽回余地。
    """
    from sqlalchemy import text

    print("⚠️ --reset-schema：正在删除向量表，接下来会全量重建")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS document_chunks"))
    print("   已删除")


def _load(path: Path) -> tuple[dict, str, str]:
    """读一个语料文件，返回 (front matter, 正文, doc_hash)。

    ⚠️ 先 normalize 再算 hash。
    不规范化的话，CRLF 和 LF 会算出不同的哈希，同一份文件在两台机器之间
    同步一次就触发一次全量重建——而全量重建意味着重新嵌入所有块。
    """
    raw = path.read_text(encoding="utf-8")
    normalized = C.normalize(raw)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    meta, body = C.parse_front_matter(normalized)
    _reject_volatile(path, meta)
    return meta, body, digest


def _reject_volatile(path: Path, meta: dict) -> None:
    """拦住实时数据，并挡住受控词表之外的 doc_type。

    抛异常而不是打警告：警告会淹没在几十行输出里，
    而"这份天气进了库"这件事在几周后没人能发现。
    宁可这次入库直接失败。

    ⚠️ 两道闸的顺序有讲究：先黑名单，再白名单。
    调过来的话，"天气"会先撞上白名单，拿到一条"不在受控词表里"的报错——
    技术上没错，但完全没解释清楚为什么，下一个人很可能把词表加上"天气"
    再跑一遍。先说清理由，再收口子。
    """
    from app.rag.policy import DOC_TYPES

    doc_type = str(meta.get("doc_type", "")).strip()

    if doc_type not in VOLATILE_DOC_TYPES:
        # 不在黑名单里，还没完——白名单也得过。
        # 这条是那次实测漏洞的补丁："天气预报"绕过了黑名单，
        # 因为它既不是"天气"也不是任何一个受控值。
        if doc_type not in DOC_TYPES:
            raise ValueError(
                f"doc_type 是「{doc_type}」，不在受控词表里。\n"
                f"\n"
                f"    受控词表只有这几个：{'、'.join(DOC_TYPES)}\n"
                f"\n"
                f"    为什么不用自由文本：doc_type 是元数据过滤的依据之一，"
                f"不在词表里的值写进去之后，任何带该字段的过滤都匹配不上，"
                f"表现为「这篇文档在带过滤的查询里静默消失」——\n"
                f"    看不到报错，只是搜不到。\n"
                f"\n"
                f"    如果你要说的是天气、票价、班次这类【实时数据】，那它们"
                f"本来就不该进知识库，理由见 VOLATILE_DOC_TYPES 上面的注释。\n"
                f"    如果是【历史气候统计】这类不随时间变的数据，把它归到"
                f"「攻略」或「景点」，并在正文里写明它是统计而不是预报。"
            )
        return

    raise ValueError(
        f"doc_type 是「{doc_type}」，属于实时数据，不能进知识库。\n"
        f"\n"
        f"    原因：实时数据变化快，而灌进向量库之后它和攻略、景点介绍\n"
        f"    在检索时完全一样——都会被当成稳定的事实取出来。用户看到\n"
        f"    「明天多云转晴」时不知道那是哪天取的，三周前的预报和今天的\n"
        f"    预报在库里长得一模一样。这不是质量问题，是欺骗。\n"
        f"\n"
        f"    实时数据应该走独立的数据源，不经过知识库。\n"
        f"    目前天气是 weather 节点直连 Open-Meteo，那条路带着\n"
        f"    「什么时候取的」这个上下文，也不会沉淀进库里。\n"
        f"\n"
        f"    如果你确实有一份【历史气候统计】想入库（那种数据不随时间变），\n"
        f"    把 doc_type 改成「攻略」或「景点」，并在正文里写明\n"
        f"    它是气候统计而不是预报。"
    )


def _dry_run(files: list[Path]) -> int:
    """只解析和切块，打印统计。不连库、不加载模型。"""
    total = 0
    lens: list[int] = []
    problems: list[tuple[str, str]] = []

    print(f"语料目录：{settings.KNOWLEDGE_DIR}")
    print(f"{'文件':<30} {'块数':>4}  {'块长中位':>8}  备注")
    print("-" * 72)

    for f in files:
        try:
            meta, body, _ = _load(f)
            chunks = C.chunk_document(meta, body)
        except Exception as e:
            problems.append((f.name, str(e)))
            continue

        sizes = [len(c.content) for c in chunks]
        lens += sizes
        total += len(chunks)
        short = sum(1 for s in sizes if s < C.MIN_CHARS)
        note = f"偏短 {short}" if short else ""
        print(
            f"{f.name:<30} {len(chunks):>4}  {sorted(sizes)[len(sizes)//2]:>8}  {note}"
        )

    if lens:
        print("-" * 72)
        print(
            f"合计 {total} 块，{sum(lens):,} 字；"
            f"块长 最小 {min(lens)} / 中位 {sorted(lens)[len(lens)//2]} / "
            f"最大 {max(lens)} / 平均 {sum(lens)//len(lens)}"
        )
        over = sum(1 for x in lens if x > C.MAX_CHARS)
        if over:
            print(f"⚠️ {over} 块超过 MAX_CHARS({C.MAX_CHARS})——通常是没被切开的表格，属正常")

    if problems:
        print()
        print("解析失败：")
        for name, err in problems:
            print(f"  ❌ {name}: {err}")
        print()
        print("（缺 city / doc_type 的文件会走到这里。那两个字段是过滤的依据，")
        print("  缺了会让这篇文档在带过滤的查询里静默消失，所以不猜、直接报错。）")
        return 1

    return 0


def _ingest(files: list[Path], engine, args: argparse.Namespace) -> int:
    db = SessionLocal()
    stats = {"new": 0, "updated": 0, "skipped": 0, "chunks": 0, "failed": 0}
    seen_ids: set[int] = set()

    try:
        for f in files:
            try:
                meta, body, digest = _load(f)
            except Exception as e:
                print(f"  ❌ {f.name}: {e}")
                stats["failed"] += 1
                continue

            doc, created = _upsert_document(db, f, meta, digest)
            seen_ids.add(doc.id)
            old_hash = doc.content_hash

            if not created and not args.force and old_hash == digest:
                stats["skipped"] += 1
                # 元数据（标题/城市等）可能变了，提交掉；不重建向量块。
                db.commit()
                print(f"  未变更  {f.name}")
                continue

            chunks = C.chunk_document(meta, body)
            crumb = _breadcrumb_meta(meta, f.name, digest)

            # 内容哈希变化才升版本；--force 且内容没变时不应该虚增版本号。
            if old_hash and old_hash != digest:
                doc.version = (doc.version or 1) + 1
            doc.content_hash = digest
            doc.status = "pending"

            written = _write_chunks(engine, doc.id, meta, chunks, crumb, args.batch_size)
            if written is None:
                # 嵌入失败。回滚 content_hash/status/version，下次跑会重做。
                print(f"  ❌ {f.name}: 嵌入失败，本次未入库（下次重跑会补上）")
                stats["failed"] += 1
                db.rollback()
                continue

            # ③ 最后才写"成功标记"
            doc.chunk_count = written
            doc.status = "indexed"
            doc.last_indexed_at = datetime.now(timezone.utc)
            doc.meta = {**(doc.meta or {}), **crumb}
            db.commit()

            stats["new" if created else "updated"] += 1
            stats["chunks"] += written
            print(f"  {'新增' if created else '更新'}    {f.name}  {written} 块")

        if args.prune:
            stats["pruned"] = _prune(db, engine, seen_ids)
    finally:
        db.close()

    print()
    print(
        f"新增 {stats['new']}，更新 {stats['updated']}，未变更 {stats['skipped']}，"
        f"失败 {stats['failed']}；本次写入 {stats['chunks']} 块"
    )
    if "pruned" in stats:
        print(f"清理了 {stats['pruned']} 篇源文件已不存在的文档")

    print()
    _verify(engine)
    return 0 if stats["failed"] == 0 else 1


def _upsert_document(db, path: Path, meta: dict, digest: str) -> tuple[Document, bool]:
    """找到或新建 documents 行。返回 (文档, 是否新建)。

    ⚠️ 用【文件名】而不是 title 或 source 做身份。

    title 可能重名，source（URL）也可能多篇文档共用同一个来源页。
    而文件名是这份语料的真实身份——语料文件才是事实来源，
    数据库里的每一行都是它的派生物。用别的字段做键，改个标题就会
    在库里多出一行"新文档"，而旧行和它的块会永远留在那儿变成孤儿。

    现在 file_name 已经提升成独立列并加了唯一约束，直接用列查询，
    不再走 JSONB 表达式。
    """
    filename = path.name
    file_size = path.stat().st_size
    mime_type = "text/markdown" if path.suffix.lower() in {".md", ".markdown"} else None
    city = str(meta.get("city", "")).strip() or None
    doc_type = str(meta.get("doc_type", "")).strip() or None

    doc = db.query(Document).filter(Document.file_name == filename).first()
    if doc is not None:
        # 标题/来源/城市等元数据可能改了，同步过去。
        # content_hash/version/status 由调用方在决定“要不要重建”之后再改。
        doc.title = str(meta.get("title", filename))
        doc.source = str(meta.get("source", "")) or None
        doc.file_name = filename
        doc.source_type = "markdown"
        doc.file_size = file_size
        doc.mime_type = mime_type
        doc.city = city
        doc.doc_type = doc_type
        doc.language = "zh"
        return doc, False

    doc = Document(
        title=str(meta.get("title", filename)),
        source=str(meta.get("source", "")) or None,
        file_name=filename,
        content_hash=digest,
        source_type="markdown",
        status="pending",
        version=1,
        chunk_count=0,
        file_size=file_size,
        mime_type=mime_type,
        city=city,
        doc_type=doc_type,
        language="zh",
        meta={"file": filename},
    )
    db.add(doc)
    db.flush()  # 拿自增 id；不 commit，让后面的失败能整体回滚
    return doc, True


def _breadcrumb_meta(meta: dict, filename: str, digest: str) -> dict:
    """写进 meta 的元数据。

    这些字段同时会进向量库那份 meta（两份都要写）：
    向量库那份是给检索时的 `meta @> :filters` 用的，
    业务库这份是给人看和给 --verify 对账用的。
    """
    return {
        "file": filename,
        "title": str(meta.get("title", "")),
        "city": str(meta.get("city", "")),
        "doc_type": str(meta.get("doc_type", "")),
        "tags": list(meta.get("tags") or []),
        "source": str(meta.get("source", "")),
        "doc_hash": digest,
    }


def _write_chunks(
    engine,
    document_id: int,
    meta: dict,
    chunks: list[C.Chunk],
    crumb: dict,
    batch_size: int,
) -> int | None:
    """把一个文档的所有块写进向量库。返回写入块数；嵌入失败返回 None。

    ⚠️ 整个文档的块在【一个事务】里先删后插。
    分多次提交的话，中途失败会留下"这篇文档一半是新块一半是旧块"的状态，
    而那种状态从外面看不出来——检索结果里会混着上一版的内容。
    """
    if not chunks:
        return 0

    # 嵌入走 embedding.embed_texts（内部 normalize_embeddings=True）。
    # 见文件顶部那条纪律。
    #
    # 稀疏向量走 sparse.encode——**同一个底座模型**（bge-m3），
    # 只是多过一个 `sparse_linear` 头。所以两者在同一个循环里算：
    # 分两个循环的话会多跑一遍完整的前向，白花一倍时间。
    vectors: list[list[float]] = []
    sparses: list[dict[int, float]] = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        contents = [c.content for c in batch]
        encoded = embed_texts(contents)
        if encoded is None:
            return None
        vectors.extend(encoded)

        # ⚠️ 稀疏编码失败和嵌入失败一样，整篇放弃。
        #
        # 只写一半的话，库里会出现"有的块有稀疏向量、有的没有"——
        # 而这个状态从外面看不出来，只表现为"有些内容明明在知识库里却搜不到"。
        # 那正是这个脚本最该避免的失败形态（见 ensure_schema 的注释）。
        encoded_sparse = sparse.encode(contents)
        if encoded_sparse is None:
            return None
        sparses.extend(encoded_sparse)

    if len(vectors) != len(chunks):
        print(f"      ⚠️ 嵌入返回 {len(vectors)} 条，块有 {len(chunks)} 条，放弃本次写入")
        return None

    # 每个块的 meta = 文档级元数据 + 这个块自己的位置。
    # section 单独存一份是为了让前端能展示"这段出自哪一节"，
    # 而不用去解析 content 开头那行面包屑。
    rows = []
    term_freqs: list[Counter] = []
    for chunk, vec, sp in zip(chunks, vectors, sparses):
        block_meta = {**crumb, "section": list(chunk.section)}

        # BM25 的词频。⚠️ 切词走 app.rag.tokenize，和查询侧是同一个函数——
        # 两边不一致的话词表对不上，BM25 会静默失效。
        tokens = tokenize(chunk.content)
        term_freqs.append(Counter(tokens))

        rows.append(
            {
                "document_id": document_id,
                "chunk_index": chunk.index,
                "title": crumb["title"],
                "source": crumb["source"] or None,
                "content": chunk.content,
                "meta": json.dumps(block_meta, ensure_ascii=False),
                "content_hash": chunk.content_hash,
                # 章节的字符串形式。和 meta 里的 section 是同一份信息，
                # 但这一列能建索引能等值查——行程 POI 补充靠它把
                # 同一节的块一次取全。
                "section_key": chunk.section_key,
                "embedding": vs.to_pg_vector(vec),
                # 稀疏词权重。下标是 token_id+1，见 sparse.to_literal。
                "chunk_sparse": sparse.to_literal(sp),
                # BM25 的长度归一化用。没有它 stats.avgdl 永远算不出来。
                "token_count": len(tokens),
            }
        )

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM document_chunks WHERE document_id = :d"), {"d": document_id}
        )

        for row, tf in zip(rows, term_freqs):
            chunk_id = conn.execute(
                text(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, title, source, content,
                         meta, content_hash, section_key, embedding,
                         chunk_sparse, token_count)
                    VALUES
                        (:document_id, :chunk_index, :title, :source, :content,
                         CAST(:meta AS jsonb), :content_hash,
                         :section_key, CAST(:embedding AS vector),
                         CAST(:chunk_sparse AS sparsevec), :token_count)
                    RETURNING id
                    """
                ),
                row,
            ).scalar()

            if tf:
                conn.execute(
                    text(
                        """
                        INSERT INTO chunk_terms (chunk_id, term, tf)
                        VALUES (:chunk_id, :term, :tf)
                        ON CONFLICT (chunk_id, term)
                        DO UPDATE SET tf = EXCLUDED.tf
                        """
                    ),
                    [
                        {"chunk_id": chunk_id, "term": term, "tf": count}
                        for term, count in tf.items()
                    ],
                )


    return len(rows)


def _prune(db, engine, seen_ids: set[int]) -> int:
    """删掉源文件已经不见了 documents 行及其块。

    ⚠️ 跨库没有外键，删业务库的行【不会】带走向量库里的块。
    少了这一步，删掉一篇文档之后它的内容还会被检索出来，
    而且用户看到的出处指向一份"数据库里已经不存在"的文档。

    真正要做删除功能时（比如将来的上传接口），必须照这个顺序：
    先删向量库的块，再删业务库的行。反过来的话，中间崩溃会留下
    "块还在、文档没了"的孤儿，而那些块的 meta 里连来源都查不全。
    """
    gone = db.query(Document).filter(~Document.id.in_(seen_ids)).all()
    if not gone:
        return 0

    with engine.begin() as conn:
        for doc in gone:
            conn.execute(
                text("DELETE FROM document_chunks WHERE document_id = :d"), {"d": doc.id}
            )

    for doc in gone:
        print(f"  清理    {doc.title}")
        db.delete(doc)
    db.commit()
    return len(gone)


def _verify(engine) -> int:
    """对账：业务库 documents 和向量库 document_chunks 对不对得上。

    查的是四类不一致里最常见的那几类：
      - 行数对不上（chunk_count 和实际块数不一致）
      - doc_hash 对不上（块的 meta 里那份和文档的元数据不一致，
        通常是删了文档、序列号又把同一个 id 发给了另一篇文档）
      - 业务库有文档但向量库没块
      - 向量库有块但业务库没文档（孤儿，上面那种情况的产物）
    """
    db = SessionLocal()
    try:
        docs = {
            d.id: (d.title, d.chunk_count, d.content_hash or "", d.file_name or "")
            for d in db.query(Document).all()
        }
    finally:
        db.close()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT document_id,
                       count(*) AS n,
                       min(meta->>'doc_hash') AS h
                FROM document_chunks
                GROUP BY document_id
                """
            )
        ).fetchall()
        total_chunks = conn.execute(
            text("SELECT count(*) FROM document_chunks")
        ).scalar()
        # 稀疏列的覆盖情况。它是后加的列，所以"有多少块补上了"这件事
        # 只能在这里看到——少了它，忘了跑 --backfill-sparse 的表现是
        # "稀疏路召回得很少"，而不会报任何错。
        sparse_missing = conn.execute(
            text("SELECT count(*) FROM document_chunks WHERE chunk_sparse IS NULL")
        ).scalar()
        # BM25 词频覆盖。改了分词但忘了 --rebuild-terms 时，
        # 这里会显示有块没有词频——而检索侧不会报错，只会漏内容。
        term_missing = conn.execute(
            text(
                "SELECT count(*) FROM document_chunks c "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM chunk_terms t WHERE t.chunk_id = c.id"
                ")"
            )
        ).scalar()

    chunks = {int(r[0]): (int(r[1]), r[2] or "") for r in rows}

    print("对账：")
    print(f"  业务库 documents      {len(docs)} 行")
    print(f"  向量库 document_chunks {total_chunks} 块")
    print(
        f"  稀疏向量 chunk_sparse  {total_chunks - sparse_missing} / {total_chunks} 块"
        + (f"  ⚠️ 缺 {sparse_missing} 块" if sparse_missing else "")
    )
    print(
        f"  BM25 词频 chunk_terms  {total_chunks - term_missing} / {total_chunks} 块"
        + (f"  ⚠️ 缺 {term_missing} 块" if term_missing else "")
    )

    problems = []
    if term_missing:
        problems.append(
            f"  ⚠️ 有 {term_missing} 块没有 BM25 词频——这些块在关键词路里永远搜不到。"
            " 修法：跑一次 ingest_knowledge.py --rebuild-terms"
        )
    missing_meta = sum(
        1 for _title, _declared, ch, fn in docs.values() if not ch or not fn
    )
    if missing_meta:
        problems.append(
            f"  ⚠️ 有 {missing_meta} 篇文档缺 file_name/content_hash——"
            "新列回填不完整"
        )
    for doc_id, (title, declared, content_hash, file_name) in docs.items():
        if doc_id not in chunks:
            problems.append(
                f"  ⚠️ 文档 {doc_id}「{title}」（{file_name}）在向量库里没有任何块"
            )
            continue
        actual, chunk_hash = chunks[doc_id]
        if declared != actual:
            problems.append(
                f"  ⚠️ 文档 {doc_id}「{title}」记录 {declared} 块，实际 {actual} 块"
            )
        if content_hash and chunk_hash and content_hash != chunk_hash:
            problems.append(
                f"  ⚠️ 文档 {doc_id}「{title}」content_hash 不一致——"
                f"这个 id 可能被回收给了另一篇文档"
            )

    orphans = sorted(set(chunks) - set(docs))
    if orphans:
        problems.append(
            f"  ⚠️ 向量库里有 {len(orphans)} 组孤儿块（业务库查不到对应文档）：{orphans[:5]}"
        )

    if problems:
        print()
        for p in problems:
            print(p)
        print()
        print("  修法：跑一次 ingest_knowledge.py；孤儿块用 --prune 清掉。")
        return 1

    print("  ✅ 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
