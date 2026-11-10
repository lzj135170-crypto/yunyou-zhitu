"""BM25 分支的离线对比尺：只算分支召回，不跑嵌入和精排。

## 为什么单独一个脚本

`eval_quality.py` 一次要加载 bge-m3、跑 86 条交叉编码器，几十秒起步。
而"换一种分词""k1 调成 1.5""查询用 raw+rewritten 拼接"这些问题
**只影响 BM25 自己**，和稠密路、精排无关。为了它们跑完整流水线，
试十个组合就是一小时，没人会那样调参。

这个脚本把 130 个块和 86 条查询读进内存，用 Python 复刻 BM25 打分，
一组变体不到一秒。**它只回答一个问题：这一路的 top-N 里命中了多少答案。**

## ⚠️ Python 复刻必须和 SQL 逐位一致

复刻的公式和 `app/rag/bm25.py` 的 SQL 是同一套，但"同一套公式"
写两遍就可能分叉（长度归一化、IDF、并列时的 chunk_id 次序）。
所以 `--self-check` 会用**真实 SQL** 跑前几条查询，逐条断言
top-30 的 chunk_id 完全一致——不是比指标，是比名次本身。

## 先定判据，再看结果

主判据：分支召回 @30（候选生成层的覆盖）。
并列时看 @10、@5（排序质量）。
**提升不足 2 条用例（≈0.026）不采纳**——76 条用例上单条涨跌是噪声，
这个脚本很快，很容易一路"调"到过拟合。

用法（在 backend 目录下执行）：

    .venv/Scripts/python.exe scripts/eval_bm25.py
    .venv/Scripts/python.exe scripts/eval_bm25.py --grid
    .venv/Scripts/python.exe scripts/eval_bm25.py --show-misses
    .venv/Scripts/python.exe scripts/eval_bm25.py --self-check
"""

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import jieba  # noqa: E402

from app.rag import bm25 as bm25_mod  # noqa: E402
from app.rag import policy as policy_mod  # noqa: E402
from app.rag import tokenize as tokenizer_mod  # noqa: E402
from app.rag import vector_store  # noqa: E402
from app.rag.query_text import minimal_clean  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent.parent / "eval"
QA_PATH = EVAL_DIR / "qa_set.jsonl"
CACHE_PATH = EVAL_DIR / "rewrite_cache.json"

CUTOFFS = (5, 10, 30)

# 不做领域词加词的独立分词器。`jieba.Tokenizer()` 有自己的词典副本，
# 不会受 `app.rag.tokenize` 在全局 jieba 上加词的影响。
_PLAIN = jieba.Tokenizer()
_SEARCH = jieba.Tokenizer()


@dataclass(frozen=True)
class Variant:
    """一组只影响 BM25 的旋钮。分词器入库和查询两侧必须相同。"""

    name: str
    tokenizer: str = "current"  # current（生产）/ precise（旧模式）/ search_no_domain
    query: str = "minimal"  # minimal（生产）/ raw / rewritten / union
    k1: float = bm25_mod.K1
    b: float = bm25_mod.B
    delta: float = 0.0


def main() -> int:
    args = _parse_args()
    chunks = _load_chunks()
    cases = _load_cases()

    if args.self_check:
        return _self_check(chunks, cases)
    if args.check_clean:
        return _check_clean(chunks, cases)

    variants = _grid_variants() if args.grid else _default_variants()
    rows = [_evaluate(v, chunks, cases) for v in variants]
    _report(rows, chunks, cases, show_misses=args.show_misses)
    return 0


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------


def _load_chunks() -> list[dict]:
    """从向量库读全部块。key 和用例里的 expect 是同一套 (文件, 章节) 键。"""
    engine = vector_store.get_vector_engine()
    if engine is None:
        print(f"❌ 向量库连不上：{vector_store._masked_url()}")
        raise SystemExit(1)

    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, content, meta FROM document_chunks ORDER BY id")
        ).fetchall()

    out = []
    for chunk_id, content, meta in rows:
        if isinstance(meta, str):
            meta = json.loads(meta)
        meta = meta or {}
        out.append(
            {
                "id": int(chunk_id),
                "content": content or "",
                "meta": meta,
                "key": (meta.get("file", ""), tuple(meta.get("section") or [])),
            }
        )
    if not out:
        print("❌ document_chunks 是空的，先跑 ingest_knowledge.py")
        raise SystemExit(1)
    return out


def _load_cases() -> list[dict]:
    cases = []
    with open(QA_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _rewrite_cache() -> dict:
    if not CACHE_PATH.exists():
        print(f"⚠️ 没有改写缓存 {CACHE_PATH}；rewritten 变体会退回原查询。")
        print("   先跑一次：eval_quality.py --rewrite-cache eval/rewrite_cache.json ...")
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _case_key(case: dict) -> str:
    return case["query"] + "|" + json.dumps(
        case.get("history", []), ensure_ascii=False, sort_keys=True
    )


def _query_text(case: dict, mode: str, cache: dict) -> str:
    cached = cache.get(_case_key(case), {})
    rewritten = cached.get("rewritten") or case["query"]
    raw = case["query"]
    if mode == "raw":
        return raw
    if mode == "minimal":
        return minimal_clean(raw)
    if mode == "union":
        return f"{rewritten} {minimal_clean(raw)}"
    return rewritten


# ---------------------------------------------------------------------------
# 分词：只换分词器，后处理走生产同一个函数
# ---------------------------------------------------------------------------


def _tokens(text: str, mode: str) -> list[str]:
    lower = (text or "").lower()
    if mode == "current":
        return tokenizer_mod.tokenize(text)
    if mode == "precise":
        # 旧模式（Phase 3 的 lcut）。留着是为了复现历史数字，
        # 不是为了再切回去——实测它在 @10 上低 0.061。
        return tokenizer_mod.postprocess(_PLAIN.lcut(lower))
    if mode == "search_no_domain":
        # 独立 tokenizer 的搜索模式。用来验证"生产用的全局 jieba
        # 没有被偷偷加词"——两者现在应该逐位相同。
        return tokenizer_mod.postprocess(_SEARCH.lcut_for_search(lower))
    raise ValueError(f"未知分词器模式 {mode!r}")


class Index:
    """一个分词模式下的全部块。tf/df/dl 只算一次，供所有 k1/b 变体复用。"""

    def __init__(self, chunks: list[dict], mode: str) -> None:
        self.ids = [c["id"] for c in chunks]
        self.keys = [c["key"] for c in chunks]
        self.tokens = [_tokens(c["content"], mode) for c in chunks]
        self.tfs = [Counter(t) for t in self.tokens]
        self.dls = [len(t) for t in self.tokens]
        self.n = float(len(chunks))
        self.avgdl = max(sum(self.dls) / len(self.dls), 1.0) if self.dls else 1.0
        self.df: Counter = Counter()
        for tf in self.tfs:
            self.df.update(tf.keys())


_INDEX_CACHE: dict[tuple, Index] = {}


def _matches(meta: dict, filters: dict) -> bool:
    """等价于 SQL 里的 `meta @> filters`（这里只会用到 city）。"""
    return all(meta.get(k) == v for k, v in filters.items())


def _index(chunks: list[dict], mode: str, filters: dict | None = None) -> Index:
    """按 (分词模式, 过滤条件) 缓存索引。

    ⚠️ 过滤必须发生在统计【之前】。SQL 里的 N、avgdl、df 都是在
    `meta @> filters` 的子集上算的；先建全量索引再过滤候选，
    IDF 就会拿全库的 df 去比子集的 N，分数和线上不一致。
    """
    filters = filters or {}
    key = (mode, tuple(sorted(filters.items())))
    if key not in _INDEX_CACHE:
        subset = [c for c in chunks if _matches(c["meta"], filters)]
        _INDEX_CACHE[key] = Index(subset, mode)
    return _INDEX_CACHE[key]


# ---------------------------------------------------------------------------
# 打分：和 bm25.py 的 SQL 同一套公式
# ---------------------------------------------------------------------------


def _score(
    terms: list[str],
    idx: Index,
    *,
    k1: float,
    b: float,
    delta: float,
) -> list[int]:
    """返回按分数降序（同分按 chunk_id）的 chunk_id 列表。"""
    unique = list(dict.fromkeys(terms))[: bm25_mod.MAX_TERMS]
    if not unique:
        return []

    scores = [0.0] * len(idx.ids)
    for term in unique:
        df = idx.df.get(term, 0)
        if not df:
            continue
        idf = math.log((idx.n - df + 0.5) / (df + 0.5) + 1)
        for i, tf in enumerate(idx.tfs):
            freq = tf.get(term)
            if not freq:
                continue
            denom = freq + k1 * (1 - b + b * idx.dls[i] / idx.avgdl)
            scores[i] += idf * (delta + (freq * (k1 + 1)) / denom)

    order = sorted(range(len(scores)), key=lambda i: (-scores[i], idx.ids[i]))
    return [idx.ids[i] for i in order if scores[i] > 0]


def _evaluate(variant: Variant, chunks: list[dict], cases: list[dict]) -> dict:
    cache = _rewrite_cache()

    # ⚠️ 按【期望条目】计数，不是按用例平均。
    # `eval_quality.branch_recall` 是 micro-average：一条用例有两个期望章节时，
    # 它在分母里占两项。按用例平均会让单位用例权重变大，和报告里的数字对不上，
    # 两个尺子量出来的"提升"就没法互相验证。
    hits = {k: 0 for k in CUTOFFS}
    total_items = 0
    n = 0
    misses: list[dict] = []

    for case in cases:
        if case.get("kind") == "unanswerable":
            continue
        expected = {(e["file"], tuple(e["section"])) for e in case["expect"]}
        cached = cache.get(_case_key(case), {})
        # ⚠️ 缓存里存的是改写模型抽出来的【原始】过滤条件（含 doc_type），
        # 而生产走 `rewrite.merge_filters` 会把 doc_type 丢掉（实测掉 0.15 召回）。
        # 这里必须走同一个校验，否则本脚本和线上看到的候选集不是一回事。
        filters, _ = policy_mod.validate_filters(cached.get("filters") or {})
        idx = _index(chunks, variant.tokenizer, filters)
        id2key = dict(zip(idx.ids, idx.keys))
        q = _query_text(case, variant.query, cache)
        ranked = _score(
            _tokens(q, variant.tokenizer), idx, k1=variant.k1, b=variant.b, delta=variant.delta
        )
        n += 1
        total_items += len(expected)
        for k in CUTOFFS:
            got = {id2key[cid] for cid in ranked[:k]}
            hits[k] += len(expected & got)

        got30 = {id2key[cid] for cid in ranked[:30]}
        missing = expected - got30
        if missing:
            misses.append(
                {
                    "query": case["query"],
                    "rewritten": _query_text(case, "rewritten", cache),
                    "terms": _tokens(q, variant.tokenizer),
                    "missing": sorted(missing),
                    "best": _best_rank(ranked, id2key, expected),
                }
            )

    return {
        "variant": variant,
        "n": n,
        "expected_items": total_items,
        "recall": {k: hits[k] / total_items for k in CUTOFFS},
        "misses": misses,
    }


def _best_rank(ranked: list[int], id2key: dict, expected: set) -> dict:
    """每个期望章节在这一路里最早出现在第几名（1 起）；没出现记 None。"""
    best = {key: None for key in expected}
    for i, cid in enumerate(ranked, 1):
        key = id2key[cid]
        if key in best and best[key] is None:
            best[key] = i
    return best


# ---------------------------------------------------------------------------
# 变体
# ---------------------------------------------------------------------------


def _default_variants() -> list[Variant]:
    return [
        # 生产配置：BM25 用最小清洗版原话。
        Variant("baseline"),
        Variant("raw", query="raw"),
        Variant("rewritten", query="rewritten"),
        Variant("union_query", query="union"),
        Variant("precise", tokenizer="precise"),
        Variant("precise_rewritten", tokenizer="precise", query="rewritten"),
        Variant("search_no_domain", tokenizer="search_no_domain"),
    ]


def _grid_variants() -> list[Variant]:
    out = []
    for k1 in (0.9, 1.2, 1.5, 1.8):
        for b in (0.5, 0.75, 0.9):
            for delta in (0.0, 0.5, 1.0):
                out.append(
                    Variant(
                        f"k1={k1:g} b={b:g} d={delta:g}",
                        query="minimal",
                        k1=k1,
                        b=b,
                        delta=delta,
                    )
                )
    return out


# ---------------------------------------------------------------------------
# 输出与自检
# ---------------------------------------------------------------------------


def _report(rows: list[dict], chunks: list[dict], cases: list[dict], *, show_misses: bool) -> None:
    base = next((r for r in rows if r["variant"].name == "baseline"), rows[0])
    rows = sorted(
        rows,
        key=lambda r: (-r["recall"][30], -r["recall"][10], -r["recall"][5], r["variant"].name),
    )

    print(
        f"语料 {len(chunks)} 块；可答用例 {base['n']} 条；"
        f"改写来自 eval/rewrite_cache.json，各变体按自己的 query 取文本"
    )
    print()
    print(f"{'变体':<22} {'@5':>8} {'@10':>8} {'@30':>8} {'vs 基线@30':>12} {'缺@30':>6}")
    print("-" * 72)
    for r in rows:
        delta = r["recall"][30] - base["recall"][30]
        print(
            f"{r['variant'].name:<22} "
            f"{r['recall'][5]:>8.4f} {r['recall'][10]:>8.4f} {r['recall'][30]:>8.4f} "
            f"{delta:>+12.4f} {len(r['misses']):>6}"
        )
    print()
    print("判据：主看 @30；提升不足 2 条用例（≈0.026）不采纳。")
    print("      @5/@10 只用来判断排序质量有没有被牺牲。")

    if show_misses:
        print()
        print("=== baseline 的 @30 未命中 ===")
        for m in base["misses"]:
            print(f"\nQ: {m['query']}")
            print(f"R: {m['rewritten']}")
            print(f"词: {m['terms']}")
            for key in m["missing"]:
                print(f"  缺: {key}   最近名次: {m['best'].get(key)}")


def _self_check(chunks: list[dict], cases: list[dict]) -> int:
    """用真实 SQL 对账 Python 打分，逐条比 top-30 的 chunk_id。"""
    from app.rag.pipeline import _bm25_search

    engine = vector_store.get_vector_engine()
    cache = _rewrite_cache()
    v = Variant("baseline")

    checked = 0
    for case in cases:
        if case.get("kind") == "unanswerable":
            continue
        cached = cache.get(_case_key(case), {})
        filters, _ = policy_mod.validate_filters(cached.get("filters") or {})
        idx = _index(chunks, "current", filters)
        q = _query_text(case, v.query, cache)
        terms = _tokens(q, "current")
        py = _score(terms, idx, k1=v.k1, b=v.b, delta=v.delta)[:30]
        sql = [cid for cid, _ in _bm25_search(engine, q, filters, 30)]
        checked += 1
        if py != sql:
            print(f"❌ 第 {checked} 条对不上：{case['query']}")
            print(f"   Python {py[:10]}")
            print(f"   SQL    {sql[:10]}")
            return 1
        if checked >= 10:
            break

    print(f"✅ Python 打分和真实 SQL 对账通过（{checked} 条，top-30 的 chunk_id 逐位一致）")
    return 0


def _check_clean(chunks: list[dict], cases: list[dict]) -> int:
    """验证 `minimal_clean` 只做表示层标准化，不增删词项。

    三条检查：
      1. 固定样例：全角、零宽、空白、标识符
      2. 标注 query：清洗前后 `tokenize` 的词项 **多重集完全相等**
      3. 语料块：清洗后不允许**丢词**（允许 NFKC 把全角规范成半角）
    """
    from collections import Counter

    expected = {
        "成都　熊猫基地": "成都 熊猫基地",  # 全角空格
        "３Ｕ８６３３ 航班": "3U8633 航班",  # 全角数字/字母
        "ＭＵ－５４０１": "MU-5401",  # 精确标识符 + 全角连字符
        "成都​熊猫": "成都熊猫",  # 零宽空格
        "  成都\n\t熊猫  ": "成都 熊猫",  # 换行/制表/多余空白
        "８０—９０元": "80—90元",  # 全角数字 + 范围连接符
    }
    bad = 0
    for src, want in expected.items():
        got = minimal_clean(src)
        if got != want:
            bad += 1
            print(f"❌ 清洗样例：{src!r} -> {got!r}，期望 {want!r}")

    for case in cases:
        q = case["query"]
        before = Counter(tokenizer_mod.tokenize(q))
        after = Counter(tokenizer_mod.tokenize(minimal_clean(q)))
        if before != after:
            bad += 1
            print(f"❌ 标注 query 词项变了：{q!r}")
            print(f"   前 {dict(before)}")
            print(f"   后 {dict(after)}")

    lost = 0
    changed = 0
    for chunk in chunks:
        before = set(tokenizer_mod.tokenize(chunk["content"]))
        after = set(tokenizer_mod.tokenize(minimal_clean(chunk["content"])))
        missing = before - after
        if missing:
            lost += 1
            if lost <= 3:
                print(f"❌ 块 {chunk['id']} 丢词：{sorted(missing)[:10]}")
        if before != after:
            changed += 1

    # 零宽扰动：在用户输入的每个字符之间插 U+200B。
    #   raw  → jieba 会把这些粘着零宽字符的 token 当成噪声，词表直接变空
    #   minimal → 去掉零宽后，排名必须和干净 query 完全一致
    idx = _index(chunks, "current", {})
    answerable = [c for c in cases if c.get("expect")][:5]
    raw_ok = minimal_ok = 0
    for case in answerable:
        clean = case["query"]
        dirty = "​".join(clean)
        clean_top = _score(
            _tokens(clean, "current"), idx, k1=bm25_mod.K1, b=bm25_mod.B, delta=0.0
        )[:10]
        raw_top = _score(
            _tokens(dirty, "current"), idx, k1=bm25_mod.K1, b=bm25_mod.B, delta=0.0
        )[:10]
        min_top = _score(
            _tokens(minimal_clean(dirty), "current"),
            idx,
            k1=bm25_mod.K1,
            b=bm25_mod.B,
            delta=0.0,
        )[:10]
        raw_ok += int(raw_top == clean_top)
        minimal_ok += int(min_top == clean_top)

    print()
    print(f"固定样例 + 标注 query：{'✅ 通过' if bad == 0 else f'❌ {bad} 处失败'}")
    print(
        f"语料块：{len(chunks)} 块，清洗后丢词 {lost} 块，"
        f"词项集合有变化 {changed} 块（全角/兼容字符被 NFKC 规范化属正常）"
    )
    print(
        f"零宽扰动（{len(answerable)} 条）：raw 恢复 {raw_ok} 条，"
        f"minimal 恢复 {minimal_ok} 条"
    )
    return 0 if bad == 0 and lost == 0 and minimal_ok == len(answerable) else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BM25 分支离线对比")
    p.add_argument("--grid", action="store_true", help="跑 k1 × b × delta 网格")
    p.add_argument("--show-misses", action="store_true", help="打印 baseline 的 @30 未命中")
    p.add_argument("--self-check", action="store_true", help="用真实 SQL 对账 Python 打分")
    p.add_argument(
        "--check-clean",
        action="store_true",
        help="验证 minimal_clean 只做表示层标准化（不增删词项）",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
