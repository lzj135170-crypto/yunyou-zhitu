"""嵌入模型：把文本转成向量，使用本地 bge-m3。

模型不可用时返回 None，由检索层降级；加载失败不抛异常，不影响聊天接口。
"""

import logging
import os
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# 必须在导入 sentence_transformers 之前设置：huggingface_hub 在 import 时读取。
# 本地目录模型用不上，但兼容仓库 id 场景。
os.environ["HF_ENDPOINT"] = settings.HF_ENDPOINT
os.environ["HF_HOME"] = settings.HF_HOME


_model: Any = None
_load_failed = False
# 模型加载耗时且占内存，用锁避免并发重复加载。
_lock = threading.Lock()


def get_model():
    """加载嵌入模型；失败返回 None 并记住失败，避免反复重试。"""
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
                # 权重缺失/无显卡属于预期降级，只记 warning。
                logger.warning(
                    "嵌入模型加载失败，RAG 将退回关键词检索：%s。"
                    "若为首次运行，请确认权重已下载到 %s",
                    e,
                    settings.EMBEDDING_MODEL,
                )
                _load_failed = True
    return _model


def _build():
    """实际加载模型；延迟 import torch，避免模块导入即付出加载成本。"""
    from sentence_transformers import SentenceTransformer

    logger.info("正在加载嵌入模型 %s（首次加载需要几秒）", settings.EMBEDDING_MODEL)
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    logger.info(
        "嵌入模型就绪：%s，维度 %d，设备 %s",
        settings.EMBEDDING_MODEL,
        model.get_embedding_dimension(),
        model.device,
    )
    return model


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量编码文本；模型不可用返回 None，空输入返回空列表。

    normalize_embeddings=True 与余弦距离一致。None 和空列表语义不同：
    前者是模型不可用需要降级，后者是输入为空。
    """
    if not texts:
        return []

    model = get_model()
    if model is None:
        return None

    try:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        # numpy 数组不能直接进 JSONB。
        return [v.tolist() for v in vectors]
    except Exception as e:
        logger.warning("文本编码失败：%s", e)
        return None


def embed_query(text: str) -> list[float] | None:
    """编码单条查询；模型不可用返回 None，避免调用方对 None 取下标。"""
    result = embed_texts([text])
    if not result:
        return None
    return result[0]


def dimension() -> int | None:
    """返回向量维度；模型不可用时为 None。"""
    model = get_model()
    if model is None:
        return None
    # 用 get_embedding_dimension 而不是旧的 get_sentence_embedding_dimension：
    # 后者在 sentence-transformers 6.x 里已标记废弃，调用会打 FutureWarning。
    return model.get_embedding_dimension()


def is_available() -> bool:
    """模型是否可用；首次调用会实际加载，不希望加载时用 loaded()。"""
    return get_model() is not None


def loaded() -> bool:
    """模型是否已加载；不触发加载。当前无调用方，保留给健康检查。"""
    return _model is not None


def reset() -> None:
    """清空模型和失败标记，供测试切换加载状态。"""
    global _model, _load_failed
    with _lock:
        _model = None
        _load_failed = False
