from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Document(Base):
    """RAG 文档元数据表：记录知识库里每一份原始文档的登记信息。

    注意这张表只存"元数据"（文档叫什么、从哪来、切了多少块），
    不存文档正文，也不存切分后的向量——正文和向量通常放在向量库里（如 pgvector、Milvus），
    这张表负责把向量库里那一堆 chunk 反查回它们属于哪份文档。
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'indexed')", name="ck_documents_status"),
        CheckConstraint("chunk_count >= 0", name="ck_documents_chunk_count"),
        CheckConstraint("version > 0", name="ck_documents_version"),
        CheckConstraint(
            "file_size IS NULL OR file_size >= 0", name="ck_documents_file_size"
        ),
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_city", "city"),
        Index("ix_documents_doc_type", "doc_type"),
        {"comment": "RAG文档元数据表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    # 文档标题，展示给用户看的名字，比如"成都旅游攻略.pdf"
    title: Mapped[str] = mapped_column(String(255), comment="文档标题", nullable=False)

    # 文档来源：本地文件路径、网页 URL 或上传时的原始文件名。
    # 存下来是为了回答问题时能给出出处（"本回答参考自 xxx"），
    # 另一方面文档解析出错时，能顺着这个字段快速定位到原始文件。
    source: Mapped[str | None] = mapped_column(String(500), comment="文档来源(路径/URL/文件名)")

    # 入库脚本用文件名做文档身份（标题可能重名，source 也可能多篇共用）。
    # 唯一约束把“一个文件一行”这条规则交给数据库兜底。
    file_name: Mapped[str] = mapped_column(
        String(255), comment="原始文件名", nullable=False, unique=True
    )

    # 整篇文档内容的 SHA-256。入库脚本用它判断“内容有没有变”，
    # 没变就跳过，不用重新嵌入。
    content_hash: Mapped[str] = mapped_column(
        String(64), comment="内容SHA-256", nullable=False
    )

    source_type: Mapped[str] = mapped_column(
        String(20), comment="来源类型: markdown/pdf/url",
        nullable=False, default="markdown", server_default="markdown",
    )

    # pending-已登记但块还没写完；indexed-向量块已成功写入。
    status: Mapped[str] = mapped_column(
        String(20), comment="索引状态: pending-待入库, indexed-已入库",
        nullable=False, default="indexed", server_default="indexed",
    )

    # 内容版本号。内容哈希变化时 +1，便于排查“同一文件改了几版”。
    version: Mapped[int] = mapped_column(
        Integer, comment="内容版本号", nullable=False,
        default=1, server_default="1",
    )

    # 切分后的文本块(chunk)数量。
    # RAG 检索的最小单位是 chunk 而不是整篇文档，这个数字用来判断切分是否合理：
    # 切得太少说明每块太长（检索命中后塞不下提示词），切得太多说明每块太碎（语义不完整）。
    chunk_count: Mapped[int] = mapped_column(
        Integer, comment="切分后的文本块数量", nullable=False,
        default=0, server_default="0",
    )

    # 文件大小/类型。旧数据迁移时留空，下一次入库会补上。
    file_size: Mapped[int | None] = mapped_column(Integer, comment="文件大小(字节)")
    mime_type: Mapped[str | None] = mapped_column(String(100), comment="MIME类型")

    # 从 front matter 提取出来做成列，方便按城市/类型筛选知识库；
    # 完整扩展信息仍然留在 metadata JSONB 里。
    city: Mapped[str | None] = mapped_column(String(50), comment="城市")
    doc_type: Mapped[str | None] = mapped_column(String(30), comment="文档类型")
    language: Mapped[str] = mapped_column(
        String(10), comment="语言", nullable=False,
        default="zh", server_default="zh",
    )

    # 最近一次成功入库的时间。
    last_indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近入库时间"
    )

    # 附加元信息。Python 属性名用 meta：Declarative API 保留 metadata，
    # 列名仍通过第一个参数指定为 "metadata"。
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, comment="文档附加元信息(JSON)"
    )
