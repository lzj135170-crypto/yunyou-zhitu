"""重排模型：用交叉编码器对召回候选逐对打分。

双塔嵌入负责广撒网，交叉编码器负责精细排序。模型按需加载，
失败只让检索少一道工序，不影响聊天接口。
"""

import logging
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 单对 token 上界；切块上限 900 字，1024 足够且能防止超长文本拖慢检索。
MAX_LENGTH = 1024

# 一次前向的批大小。
BATCH_SIZE = 32

_model: Any = None
_load_failed = False
# 加载耗时且占显存，用锁避免并发重复加载。
_lock = threading.Lock()


def get_model():
    """加载重排模型；失败返回 None 并记住失败，避免每次检索都重试。"""
    global _model, _load_failed

    if _model is not None:
        return _model
    if _load_failed:
        return None

    with _lock:
        if _model is None and not _load_failed:
            try:
                _model = _build()
            except Exception as e:
                # 权重缺失/显存不足属于预期降级，只记 warning。
                logger.warning(
                    "重排模型加载失败，检索将只用 RRF 分数排序：%s。"
                    "若为首次运行，请确认权重已下载到 %s",
                    e,
                    settings.RERANKER_MODEL,
                )
                _load_failed = True
    return _model


def _build():
    """真正加载模型。抽出来是为了让 get_model 的异常处理保持干净。"""
    from sentence_transformers import CrossEncoder

    logger.info("正在加载重排模型 %s（首次加载需要几秒）", settings.RERANKER_MODEL)
    model = CrossEncoder(
        settings.RERANKER_MODEL,
        max_length=MAX_LENGTH,
        # 不显式传 activation_fn/trust_remote_code：该模型是标准 XLM-R 架构，
        # 默认会落到 Sigmoid，且无需执行仓库代码。
        model_kwargs=_model_kwargs(),
        backend="torch",
    )
    logger.info("重排模型就绪：%s，设备 %s", settings.RERANKER_MODEL, _device_of(model))
    return model


def _model_kwargs() -> dict | None:
    """CUDA 可用时使用 fp16，节省与 bge-m3 共存的显存；CPU 上不启用。"""
    try:
        import torch

        if torch.cuda.is_available():
            return {"torch_dtype": torch.float16}
    except Exception:
        # torch 没装 / 驱动炸了，都不是这一层该管的事——
        # 交给 CrossEncoder 自己去报错，那边的错误信息更具体。
        pass
    return None


def _device_of(model: Any) -> str:
    """取模型所在设备，取不到就返回 unknown。纯日志用，不该因为取不到而报错。"""
    try:
        return str(model.model.device)
    except Exception:
        return "unknown"


def rerank(
    query: str, passages: list[str], top_k: int | None = None
) -> list[tuple[int, float]] | None:
    """重排候选，返回按分数降序的 (原下标, 0~1 分数)；模型不可用返回 None。

    返回下标而非文本，避免重复内容回查串位；None 让调用方区分"未重排"
    和"重排后顺序恰好未变"。
    """
    if not passages:
        return []

    model = get_model()
    if model is None:
        return None

    try:
        results = model.rank(
            query,
            passages,
            top_k=top_k,
            batch_size=BATCH_SIZE,
            # 服务进程里不需要进度条，它会往 stderr 刷一大串 '\r'，
            # 在日志里表现为一堆乱码。
            show_progress_bar=False,
        )
        return [(int(r["corpus_id"]), float(r["score"])) for r in results]
    except Exception as e:
        logger.warning("重排失败，本次检索只用 RRF 分数：%s", e)
        return None


def is_available() -> bool:
    """模型是否可用；首次调用会实际加载，不希望加载时用 loaded()。"""
    return get_model() is not None


def loaded() -> bool:
    """模型是否已加载；不触发加载。当前无调用方，保留给健康检查。

    并发加载时可能短暂返回 False，偏差方向是"少报一项能力"，不会误报正常。
    """
    return _model is not None


def reset() -> None:
    """清空模型和失败标记，供测试切换加载状态。"""
    global _model, _load_failed
    with _lock:
        _model = None
        _load_failed = False
