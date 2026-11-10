"""稀疏头的自检：六项，每一项对应一个【不报错但结果全错】的失效方式。

## 为什么这个文件必须存在

`app/rag/sparse.py` 是我照 BGE-M3 的定义自己写的前向。项目里没有
`FlagEmbedding` 可以对照（装它会再占 2.27 GB 显存，而空闲只有 6.96 GB），
`modules.json` 里也没有 `SparseEncoder` 模块。**没有参考实现可以跑一遍对一下。**

所以"它算得对不对"这件事，唯一的证据就是这六项检查。它们的共同点是：

    错了之后，代码照样跑得通，检索照样返回结果，只是全部没有意义。

这类东西没法靠"跑一次看看"发现——返回的东西长得太正常了。

## 六项分别在防什么

| # | 检查 | 不做的话会怎样 |
|---|---|---|
| ① | 同输入两次**逐位**相同 | 每次查询给出略微不同的结果，看起来像"模型抖动"，实际是 bug |
| ② | 金标 token→权重**跨进程**一致 | 只在一个进程里自洽，入库和查询是两个进程 |
| ③ | argmax 落在**输入自身**的 token 上 | 隐藏状态取错层，权重整体错位而分数仍然是"合理"的 |
| ④ | 字面量经 **PG 往返**后下标不变 | 整体错一位，点积不变、分数不变，只有读回来时全错 |
| ⑤ | top token 是**实词** | 特殊 token / 词首标记霸榜，相似度被一份常数淹没 |
| ⑥ | nnz 在上限内 | 超了 PG 直接拒绝，入库到一半失败 |

**④ 是唯一能防住"错一位"的检查。** 其余五项都发现不了它——
见 `sparse.py` 模块开头第 1 条。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/check_sparse.py

⚠️ 会加载嵌入模型（几秒 + 2.27 GB 显存）。跑之前确认没有别的进程在用显卡。
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 用来验证语义的五条查询。挑的都是"内容词很清楚"的问法，
# 这样 top token 该长什么样是可以事先说出来的。
QUERIES = [
    "成都熊猫基地门票多少",
    "都江堰怎么去",
    "锦里有什么好吃的",
]

# ⑤ 用的那一条，以及它必须打到的几组词。
#
# ⚠️ 写成"词组"而不是"某个 token"：SentencePiece 怎么切是分词器决定的，
# 「熊猫」可能是一个 token，也可能被切成「熊」+「猫」（实测是后者）。
# 断言某个具体 token 会在分词器一升级时就误报。
SEMANTIC_QUERY = "成都熊猫基地门票多少"
SEMANTIC_CONCEPTS: dict[str, tuple[str, ...]] = {
    "成都": ("成都",),
    "熊猫": ("熊猫", "熊", "猫"),
    "基地": ("基地",),
    "门票": ("门票", "门", "票"),
}

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


def _f32(x: float) -> bytes:
    """把浮点按 float32 的位模式取出来，用来做"是不是同一个 float4"的判断。

    ⚠️ 不能写成 `float(np.float32(x))`。那个转换在 numpy 里返回的是
    float32 值对应的 float64，而 PG 打印的是**最短十进制表示**——
    两者不是同一个 float64。位模式比较才没有这个歧义。
    """
    import struct

    return struct.pack("f", x)


def _decode(tokenizer, weights: dict[int, float], n: int = 8):
    """把 {token_id: 权重} 排成可读的 (权重, 词面) 列表。"""
    top = sorted(weights.items(), key=lambda kv: -kv[1])[:n]
    return [(w, tokenizer.decode([tid]).replace("▁", "␣")) for tid, w in top]


def main() -> int:
    from app.agents import embedding
    from app.rag import sparse

    if not embedding.is_available():
        print("嵌入模型不可用，稀疏头自检无法进行。")
        print("先确认权重在 models/bge-m3/ 下，并且显卡没被别的进程占着。")
        return 1

    model = embedding.get_model()
    tokenizer = model.tokenizer

    # ---- ① 确定性：同一段文本两次编码必须逐位相同 ----
    print("\n[1] 确定性（同输入两次逐位相同）")
    first = sparse.encode([SEMANTIC_QUERY])[0]
    # ⚠️ 中间夹一条别的输入。只连跑两次一样的输入，可能命中某个缓存而显得确定；
    # 夹一条之后走的是完整的批处理路径（padding、batch 边界都不一样）。
    sparse.encode(["中间插一条无关文本"])
    second = sparse.encode([SEMANTIC_QUERY])[0]
    check(
        "两次编码结果逐位相同",
        first == second,
        f"差异：{ {k: (first.get(k), second.get(k)) for k in set(first) | set(second) if first.get(k) != second.get(k)} }",
    )
    check("结果非空", bool(first), "编码出来是空的字典——过滤条件可能把一切都滤掉了")

    # ---- ② 跨进程一致 ----
    #
    # ⚠️ 这条测的是"入库的进程和查询的进程算出来的是不是同一个东西"。
    # 入库在 scripts/ingest_knowledge.py，查询在 uvicorn 里，从来不是同一个进程。
    # 只要有任何一步依赖进程内的随机性（哈希种子、线程数、算子选择），
    # 库里的向量和查询向量就会不在同一套坐标系里——而两者的形状完全一样，
    # 点积照样是一个合理的数。
    print("\n[2] 跨进程一致（入库和查询不是同一个进程）")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys;"
            "sys.path.insert(0, r'%s');"
            "from app.rag import sparse;"
            "w = sparse.encode([%r])[0];"
            "print(json.dumps({str(k): v for k, v in sorted(w.items())}))"
            % (str(Path(__file__).resolve().parent.parent), SEMANTIC_QUERY),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    if child.returncode != 0:
        check("子进程编码成功", False, (child.stderr or "")[-500:])
    else:
        remote = {int(k): v for k, v in json.loads(child.stdout.strip().splitlines()[-1]).items()}
        check(
            "子进程算出的权重与本进程逐位相同",
            remote == first,
            f"两个进程给出了不同的权重（本进程 {len(first)} 项，子进程 {len(remote)} 项）",
        )

    # ---- ③ argmax 落在输入自身的 token 上 ----
    #
    # ⚠️ 如果隐藏状态取的是别的层（比如倒数第二层、或者没做最后的 LayerNorm），
    # 权重仍然是一堆"看起来合理"的数——但最大的那个可能落在某个
    # 根本不在输入里的 token 上。这条断言就是那个错法的探测器。
    print("\n[3] argmax 落在输入自身的 token 上")
    ids_in = set(tokenizer(SEMANTIC_QUERY)["input_ids"])
    argmax_id = max(first, key=lambda k: first[k])
    check(
        "权重最大的 token 确实出现在输入里",
        argmax_id in ids_in,
        f"argmax 的 token id={argmax_id}（{tokenizer.decode([argmax_id])!r}）不在输入 token 里",
    )
    check(
        "所有 token 都来自输入",
        set(first) <= ids_in,
        f"多出来的：{[tokenizer.decode([t]) for t in set(first) - ids_in]}",
    )

    # ---- ④ 字面量经 PG 往返后下标不变 ----
    #
    # ⚠️ **这是唯一能发现"整体错一位"的检查。**
    #
    # 错一位时的现象：查询侧和文档侧用同一个置换，点积完全不变，
    # 分数正常、排序正常、指标正常——只有"把字面量读回来看是哪几个词"
    # 会全部错位。所以这个错误可以一路活到线上，然后在某次排查里
    # 把所有人带偏。
    #
    # 往返之后必须逐位相同（包括权重），不只是"下标对得上"。
    print("\n[4] sparsevec 字面量经 PG 往返后下标不变")
    from app.rag import vector_store

    engine = vector_store.get_vector_engine()
    if engine is None:
        check("向量库可用", False, "连不上向量库，这一项和下面几项都要它")
    else:
        from sqlalchemy import text as sql_text

        literal = sparse.to_literal(first)
        with engine.connect() as conn:
            back = conn.execute(
                sql_text("SELECT CAST(:v AS sparsevec)::text"), {"v": literal}
            ).scalar()
        decoded = sparse.from_literal(back)
        check(
            "解出的 token_id 集合与原始完全一致",
            set(decoded) == set(first),
            f"少了 {sorted(set(first) - set(decoded))[:5]}，多了 {sorted(set(decoded) - set(first))[:5]}",
        )
        # ⚠️ 权重要按 **float32 位模式**比，不能按 float64 比。
        #
        # `sparsevec` 存的是 float4，打印时用 float4 的最短往返表示，
        # 于是 `float("0.24761982")` 得到的 float64 和算出来的
        # 0.24761982262134552 不相等（相对差 1e-8）——但它们是**同一个 float4**。
        # 按 float64 比会稳定地报一个"精度丢失"的假警，
        # 然后引导人去修一个根本没坏的东西。
        mismatched = [
            tid
            for tid in set(first) & set(decoded)
            if _f32(first[tid]) != _f32(decoded[tid])
        ]
        check(
            "每个权重在 float32 上逐位相同",
            not mismatched,
            f"这些 token 的权值在 float32 上都对不上：{mismatched[:5]}"
            "——说实话的值改了，或者字面量格式不是往返无损的",
        )
        # 把解码结果印出来，好让人肉眼比对：如果错位一位，
        # 这里显示的"词"会明显对不上下面的查询。
        print(f"     往返回来的前几个词：{[w for _, w in _decode(tokenizer, decoded, 5)]}")

    # ---- ⑤ top token 是实词 ----
    #
    # ⚠️ 这一项比指标更早发现问题。
    #
    # 实测过不排特殊 token 的样子：`<s>` 拿 0.1833、`</s>` 拿 0.1925，
    # 而「熊」只有 0.1785。它们霸榜之后，每个块之间都会共享一份
    # 0.19×0.19 的常数相似度，把真正的词法差异压掉——
    # 检索照样返回结果，只是"谁排前面"变得和内容无关。
    #
    # 这一项**要人看**：先打印，再自动断言。自动断言能覆盖的是
    # "该打到的实词组有没有打到"，格式上的怪异（比如出现了没见过的符号）
    # 只有打印出来才看得见。
    print("\n[5] top token 是实词（要人看，也要断言）")
    shown = _decode(tokenizer, first, 8)
    for w, word in shown:
        print(f"     {w:.4f}  {word!r}")

    # ⚠️ 分两层，因为"特殊 token 出现了"和"词首标记出现在很后面"是两件不同的事：
    #
    #   * 特殊 token（`<s>` / `</s>` / `<pad>` / `<unk>`）**一个都不该有**，
    #     它们是 `encode()` 里显式排掉的。出现了就是过滤失效。
    #   * 词首标记 `▁` 会出现，而且**允许**出现：参考实现也不排它。
    #     实测它只拿到 0.0497，而 bias 是 0.0452——也就是几乎没有信号。
    #     但"几乎没信号"必须是**断言出来的**，不能靠"它看起来很小"。
    #     不检查的话，某次改动让它涨到 0.2，每个块之间就会共享一份常数相似度。
    specials = {"<s>", "</s>", "<pad>", "<unk>", "<mask>"}
    hit_specials = [word for _, word in shown if word.strip() in specials]
    check("没有特殊 token 出现在结果里", not hit_specials, f"出现了：{hit_specials}")

    top5 = _decode(tokenizer, first, 5)
    check(
        "词首标记不在前 5 名里",
        not any(word.strip() in {"", "␣", "▁"} for _, word in top5),
        f"前 5 名里出现了词首标记：{[w for _, w in top5]}",
    )
    # 词首标记的权重必须**严格低于每一个内容词**。这是"它是噪声不是信号"的定义。
    base_id = tokenizer.convert_tokens_to_ids("▁")
    base_w = first.get(base_id, 0.0)
    content_below = [tid for tid in first if tid != base_id and first[tid] <= base_w]
    check(
        "词首标记的权重低于所有内容词（它是噪声，不是信号）",
        not content_below,
        f"这些 token 比词首标记还低：{_decode(tokenizer, {t: first[t] for t in content_below}, 3)}",
    )

    shown_set = {word for _, word in top5}
    missing = [
        concept
        for concept, forms in SEMANTIC_CONCEPTS.items()
        if not shown_set & set(forms)
    ]
    check(
        "四组内容词都至少打到一个",
        not missing,
        f"没打到的：{missing}（top token = {sorted(shown_set)}）",
    )

    # 再拿真实的块看一遍：文档侧的 top token 应该也是实词，不是标点。
    if engine is not None:
        with engine.connect() as conn:
            row = conn.execute(
                sql_text(
                    "SELECT content FROM document_chunks ORDER BY id LIMIT 1"
                )
            ).fetchone()
        doc_w = sparse.encode([row[0]])[0]
        doc_ids = set(tokenizer(row[0])["input_ids"])
        print("     一个真实块的前 5 个 token：")
        for w, word in _decode(tokenizer, doc_w, 5):
            print(f"       {w:.4f}  {word!r}")
        check(
            "文档侧所有 token 都来自文档本身",
            set(doc_w) <= doc_ids,
            f"多出来的：{[tokenizer.decode([t]) for t in set(doc_w) - doc_ids]}",
        )

    # ---- ⑥ nnz 在上限内 ----
    print("\n[6] nnz 在 pgvector 上限内")
    # ⚠️ 向量库不可用时**不能崩**，也不能拿 None 去编码。
    #
    # 这一条在容器没起来的机器上跑过一次，表现是
    # `sparse.encode([None])` 里抛 "text input must be of type str"，
    # 然后 `[0]` 变成 TypeError——一个**检查脚本自己先炸了**的报错。
    # 那种报错会把注意力引到稀疏头上，而真实原因是 Docker 没开。
    # 所以这里用查询本身做下限检查，库在的话再用最长的块做上限检查。
    query_nnz = sparse.nnz(sparse.encode(["成都"])[0])
    if engine is not None:
        with engine.connect() as conn:
            longest_b = conn.execute(
                sql_text(
                    "SELECT content FROM document_chunks ORDER BY length(content) DESC LIMIT 1"
                )
            ).scalar()
        encoded = sparse.encode([longest_b]) if longest_b else None
        if encoded is None:
            check("最长块编码成功", False, "稀疏编码返回 None，见上面的 warning")
            longest_nnz = None
        else:
            longest_nnz = sparse.nnz(encoded[0])
    else:
        longest_nnz = None

    if longest_nnz is None:
        print(f"     查询「成都」nnz={query_nnz}（向量库不可用，跳过最长块的检查）")
        check("最长块的 nnz 检查", False, "向量库不可用，这一项没跑成")
    else:
        print(f"     查询「成都」nnz={query_nnz}，最长的块 nnz={longest_nnz}")
        check(
            f"最长块的 nnz 没超上限 {sparse.MAX_NNZ}",
            longest_nnz <= sparse.MAX_NNZ,
            f"实际 {longest_nnz}（块里的 token 数是它的上界，一般远小于它）",
        )
    # 超限时 to_literal 必须抛，不能截断——截断是静默丢信息。
    over = {i: 1.0 for i in range(sparse.MAX_NNZ + 1)}
    try:
        sparse.to_literal(over)
        check("超限时 to_literal 抛异常", False, "它没抛——说明会静默截断")
    except ValueError:
        check("超限时 to_literal 抛异常", True)

    print(f"\n{'=' * 60}")
    print(f"通过 {_passed}，失败 {_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
