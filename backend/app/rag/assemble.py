"""上下文组装：把精排后的块拼成带出处、长度可控、去重后的提示词文本。

预算不足时丢弃整块而不是截断句子；每块带编号和出处，便于引用和回溯。
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 预算常量统一定义在 policy.py。
from app.rag.policy import CONTEXT_BUDGET_CHARS  # noqa: E402
from app.rag.types import Hit  # noqa: E402


@dataclass(frozen=True)
class Citation:
    """一条出处。给前端展示和落库用。"""

    index: int
    document_id: int
    chunk_id: int
    title: str
    source: str | None
    section: tuple[str, ...]


@dataclass(frozen=True)
class Assembled:
    """组装结果。"""

    context: str
    """拼好的上下文；无命中时为空字符串，不是 None。"""

    citations: list[Citation]
    """与 context 编号一一对应的出处。"""

    used: int
    """实际放入上下文的块数。"""

    dropped_duplicates: int
    """内容重复被丢弃的块数；持续偏高说明语料有重叠。"""

    dropped_budget: int
    """超出预算被丢弃的块数；持续偏高说明预算或块长度需要调整。"""


def assemble(chunks: list[Hit], budget: int = CONTEXT_BUDGET_CHARS) -> Assembled:
    """把已按相关度排序的块拼成带出处的上下文；本函数不重新排序。"""
    deduped: list[Hit] = []
    seen: set[str] = set()
    dropped_duplicates = 0

    for chunk in chunks:
        if chunk.content_hash in seen:
            dropped_duplicates += 1
            continue
        seen.add(chunk.content_hash)
        deduped.append(chunk)

    parts: list[str] = []
    citations: list[Citation] = []
    used_chars = 0
    dropped_budget = 0

    for chunk in deduped:
        block = _format_block(len(citations) + 1, chunk)
        # 整块放入或整块丢弃，不截断句子。
        if used_chars + len(block) > budget:
            dropped_budget += 1
            continue

        parts.append(block)
        citations.append(
            Citation(
                index=len(citations) + 1,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                source=chunk.source,
                section=tuple(chunk.section or ()),
            )
        )
        used_chars += len(block)

    return Assembled(
        context="\n\n".join(parts),
        citations=citations,
        used=len(citations),
        dropped_duplicates=dropped_duplicates,
        dropped_budget=dropped_budget,
    )


def _format_block(index: int, chunk: Hit) -> str:
    """拼一个块：编号 + 文档标题/章节 + 正文；URL 只放在 citations 里。"""
    where = " > ".join((chunk.title, *[s for s in (chunk.section or ()) if s]))
    return f"【{index}】《{where}》\n{_strip_breadcrumb(chunk.content, where)}"


def _strip_breadcrumb(content: str, where: str) -> str:
    """去掉正文开头与标题行完全一致的面包屑，避免同一出处出现两次。

    面包屑必须保留在 content 里供嵌入和稀疏头识别章节；
    只有多节拼接导致不一致时保留，以区分各段来源。
    """
    first, sep, rest = content.partition("\n")
    if sep and first == f"【{where}】":
        return rest
    return content
