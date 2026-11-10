"""BGE-M3 稀疏头：把文本编码成 `{token_id: 权重}`，存入 PG 的 `sparsevec`。

`sparse_linear` 将每个 token 的 1024 维隐藏状态压成词权重，查询和文档做点积
得到词法匹配分。它替代手写 BM25，权重由模型学习，不需要分词和 IDF 维护。

关键约定：
1. `sparsevec` 下标从 1 开始，token id 从 0 开始，映射为 `token_id + 1`；
2. 权重文件是 fp16，加载时必须升到 fp32，不能降低底座精度；
3. 只保留 `w > 0`，并排除 `<s>`、`</s>` 等特殊 token，否则块之间会共享虚假相似度。

没有原版实现可对照，改动前先跑 `scripts/check_sparse.py`。
"""

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 必须和建表时的 sparsevec(250002) 以及字面量末尾的维度一致。
VOCAB_SIZE = 250002

# pgvector 对 sparsevec 非零元素的上限。
MAX_NNZ = 16000

# 稀疏头权重文件，相对 models/bge-m3/。
_HEAD_FILE = "sparse_linear.pt"

# 一次前向的批大小；显存随 batch × seq_len 线性增长，块变长时要下调。
_BATCH = 16

_head: Any = None
_head_failed = False
_lock = threading.Lock()


def _load_head():
    """加载 `sparse_linear` 权重，返回 fp32 的 `(W, b)`；失败时缓存失败状态。"""
    global _head, _head_failed

    if _head is not None:
        return _head
    if _head_failed:
        return None

    with _lock:
        if _head is None and not _head_failed:
            try:
                _head = _build_head()
            except Exception as e:
                logger.warning(
                    "稀疏头加载失败，稀疏召回路将不可用：%s。"
                    "权重应在 %s",
                    e,
                    _head_path(),
                )
                _head_failed = True
    return _head


def _head_path() -> Path:
    from app.config import settings

    return Path(settings.EMBEDDING_MODEL) / _HEAD_FILE


def _build_head():
    import torch

    path = _head_path()
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")

    state = torch.load(path, map_location="cpu", weights_only=False)

    # 必须升到 fp32；跟随权重降到 fp16 会拖累稠密路共用的底座。
    weight = state["weight"].to(torch.float32)
    bias = state["bias"].to(torch.float32)
    if tuple(weight.shape) != (1, 1024):
        raise ValueError(f"sparse_linear.weight 形状是 {tuple(weight.shape)}，期望 (1, 1024)")

    logger.info("稀疏头就绪：%s", path)
    return weight, bias


def encode(texts: list[str]) -> list[dict[int, float]] | None:
    """把一批文本编码成 `{token_id: 权重}`；模型或稀疏头不可用时返回 None。

    复用 `embedding.get_model()` 的模型，避免再占 2.27 GB 显存，也保证
    tokenizer 与稠密路一致——tokenizer 不同会让库里存的权重静默错位。
    """
    if not texts:
        return []

    head = _load_head()
    if head is None:
        return None

    # 延迟 import：embedding 顶层会加载 torch；这里也刻意复用同一个模型底座。
    from app.agents import embedding

    model = embedding.get_model()
    if model is None:
        return None

    try:
        return _encode_batches(model, head, texts)
    except Exception as e:
        # 和稠密路一样只记 warning：编码失败是可降级的，不该让整条检索炸掉。
        logger.warning("稀疏编码失败：%s", e)
        return None


def _encode_batches(model: Any, head: Any, texts: list[str]) -> list[dict[int, float]]:
    import torch

    weight, bias = head
    tokenizer = model.tokenizer
    auto = model[0].auto_model
    device = next(auto.parameters()).device

    # 特殊 token 从 tokenizer 现取并排除，否则块之间会共享虚假的词法相似度。
    unused = {
        tokenizer.cls_token_id,
        tokenizer.sep_token_id,
        tokenizer.pad_token_id,
        tokenizer.unk_token_id,
        tokenizer.mask_token_id,
    }
    unused.discard(None)

    # 和稠密路共用模型配置的 max_seq_length，避免两条路截断行为不一致。
    max_len = int(model.max_seq_length)
    truncated = 0
    out: list[dict[int, float]] = []

    for start in range(0, len(texts), _BATCH):
        batch = texts[start : start + _BATCH]
        enc = tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=max_len,
            return_tensors="pt",
        )
        # 截断必须能被发现：用 attention_mask 的和判断，截断后真实 token 数
        # 恰好等于 max_length，且不受左右 padding 影响。
        for mask in enc["attention_mask"]:
            if int(mask.sum()) >= max_len:
                truncated += 1

        moved = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            hidden = auto(
                input_ids=moved["input_ids"], attention_mask=moved["attention_mask"]
            ).last_hidden_state
            # 先在设备上算成 (B, L) 再搬回 CPU：搬 (B, L, 1024) 是白搬 1024 倍。
            weights = torch.relu(
                torch.nn.functional.linear(
                    hidden.to(torch.float32), weight.to(device), bias.to(device)
                )
            ).squeeze(-1)
            # 掩掉 padding 位，避免 <pad> 的权重参与 max 聚合。
            weights = weights * moved["attention_mask"].to(weights.dtype)
            weights = weights.cpu()
            ids_cpu = enc["input_ids"]

        for row_w, row_ids in zip(weights, ids_cpu):
            acc: dict[int, float] = {}
            for tid, w in zip(row_ids.tolist(), row_w.tolist()):
                # 参考实现按 w > 0 过滤，不取 top-n，避免长短文档分数尺度不一致。
                if w <= 0 or tid in unused:
                    continue
                # 同一 token 出现多次取 max：BGE-M3 的定义，mean 会摊薄长文中的词。
                if w > acc.get(tid, 0.0):
                    acc[tid] = w
            out.append(acc)

    if truncated:
        logger.warning(
            "稀疏编码时有 %d 条文本超过 max_seq_length=%d 被截断，"
            "这些文本尾部的词法信号已经丢失",
            truncated,
            max_len,
        )
    return out


def encode_query(text: str) -> dict[int, float] | None:
    """单条查询编码。单独开一个函数是为了"模型不可用"只有一处判断。"""
    result = encode([text])
    if not result:
        return None
    return result[0]


# ---------------------------------------------------------------------------
# 字面量：{token_id: 权重} ←→ PG 的 sparsevec 文本形式
#
# 收在一个地方的理由和 `to_pg_vector` 一样：入库侧和查询侧必须是同一套映射。
# 两边各写一遍的话，错一位这件事会因为"两边错得一样"而彻底躲过所有测试。
# ---------------------------------------------------------------------------


def to_literal(weights: dict[int, float]) -> str:
    """转成 pgvector 的 sparsevec 文本形式，如 `{1:0.5,3:2.25}/250002`。

    下标是 `token_id + 1`：PG 从 1 起、token 从 0 起。整体错一位不会报错，
    也不会影响点积，只能靠 `check_sparse.py` 的往返检查发现。
    权重用 `str(float)` 保留最短往返表示；存储精度是 float4。
    nnz 超限时抛异常，不截断，避免块悄悄丢失词法信号。
    """
    items = sorted((tid, w) for tid, w in weights.items() if w > 0)
    if len(items) > MAX_NNZ:
        raise ValueError(
            f"稀疏向量有 {len(items)} 个非零元素，超过 pgvector 上限 {MAX_NNZ}"
        )
    body = ",".join(f"{tid + 1}:{float(w)}" for tid, w in items)
    return "{" + body + "}/" + str(VOCAB_SIZE)


def from_literal(literal: str) -> dict[int, float]:
    """把 sparsevec 文本解回 `{token_id: 权重}`，供自检做往返校验。"""
    text = (literal or "").strip()
    if not text.startswith("{") or "}/" not in text:
        raise ValueError(f"不是 sparsevec 字面量：{literal!r}")

    body, _, dim = text[1:].partition("}/")
    if int(dim) != VOCAB_SIZE:
        raise ValueError(f"维度是 {dim}，期望 {VOCAB_SIZE}")

    out: dict[int, float] = {}
    for pair in (p for p in body.split(",") if p):
        idx, _, weight = pair.partition(":")
        # 反向的那一步：index 减回 token id。
        out[int(idx) - 1] = float(weight)
    return out


def nnz(weights: dict[int, float]) -> int:
    """非零元素个数，给自检和排查用。"""
    return sum(1 for w in weights.values() if w > 0)


def reset() -> None:
    """丢弃已加载的稀疏头，供测试使用；不影响 embedding 的稠密模型缓存。"""
    global _head, _head_failed
    with _lock:
        _head = None
        _head_failed = False
