"""文本处理：front matter 解析 → 清洗 → 切块，纯函数，不碰数据库和模型。

分词在 app/rag/tokenize.py，入库和检索必须使用同一个函数。
修改分词后需要重建词频表：ingest_knowledge.py --rebuild-terms。
"""

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 切块目标长度：600 字平衡语义完整性和向量聚焦，不追求用满 8192 上下文。
TARGET_CHARS = 600

# 硬上限，也是重排模型 max_length=1024 的安全边界；超长段落强制拆开。
MAX_CHARS = 900

# 低于此长度的块并入同章节上一块，避免碎片块挤占候选集。
MIN_CHARS = 200

# 段落被强制拆开时补 100 字重叠；不跨章节重叠。
OVERLAP_CHARS = 100

# front matter 必需字段；缺失直接报错，不猜默认值。
REQUIRED_FIELDS = ("title", "city", "doc_type", "source")

# ATX 标题：## 标题 / ### 子标题
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# 只有 # 和 ## 作为章节边界，### 及更深保留为正文行。
# 按任意层级切会把攻略切成大量碎片块；保留小标题还能让"后海村"这类
# 只出现在标题里的名字进入正文，避免检索不到。
SECTION_HEADING_LEVEL = 2

# 句末标点后切句。用 lookbehind 保留标点本身，免得拆出来的句子没有结尾。
_SENTENCE_RE = re.compile(r"(?<=[。！？；!?;])[ \t]*")

# 网页复制常见的零宽字符会导致 content_hash 每次都不同，触发无意义全量重建。
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


@dataclass(frozen=True)
class Chunk:
    """一个切好的块，整条流水线内的只读快照。"""

    index: int
    """文档内序号，从 0 起。"""

    content: str
    """块正文，已带面包屑；检索、嵌入和入库必须看到同一份内容。"""

    section: tuple[str, ...]
    """面包屑路径（不含文档标题），如 ("第三天", "交通")。给前端展示用。"""

    content_hash: str
    """content 的 sha256。用于跨文档去重和入库幂等判断。"""

    @property
    def section_key(self) -> str:
        """章节字符串，存入 TEXT 列供按章节等值查询；分隔符与展示格式一致。"""
        return " > ".join(self.section)

def sha256(text: str) -> str:
    """文本的 sha256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    """统一换行、去零宽字符、合并空行。

    必须在计算 doc_hash 前调用，否则 CRLF/LF 差异会触发无意义的全量重建。
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(_ZERO_WIDTH)
    text = text.replace(" ", " ").replace("　", " ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # 三个以上连续空行压成两个。Markdown 里空行只表示"分段"，
    # 多出来的空行不影响渲染，却会让切块的段落切分多出一堆空段。
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    """解析 front matter，返回 (元数据, 正文)。

    只支持 `key: value` 和 `key: [a, b]`，不引入 PyYAML。
    缺 title/city/doc_type/source 时直接抛异常：缺字段的块会让带过滤的
    检索永远查不到，且不报错，入库时失败比入库后静默消失更容易定位。
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError(
            "文件开头没有 front matter（首行必须是 ---）。"
            f"必需的字段：{'、'.join(REQUIRED_FIELDS)}"
        )

    meta: dict[str, object] = {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        meta[key.strip()] = _parse_value(value.strip())

    if end is None:
        raise ValueError("front matter 没有闭合（找不到第二个 ---）")

    missing = [f for f in REQUIRED_FIELDS if not meta.get(f)]
    if missing:
        raise ValueError(
            f"front matter 缺少必需字段：{'、'.join(missing)}。"
            "这些字段是检索过滤的依据，缺了会让这篇文档在带过滤的查询里静默消失"
        )

    body = "\n".join(lines[end + 1 :])
    return meta, normalize(body)


def _parse_value(raw: str) -> object:
    """解析行内列表或字符串；不做类型推断，避免 city 等值被转成 int。"""
    if raw.startswith("[") and raw.endswith("]"):
        items = [x.strip().strip("'\"") for x in raw[1:-1].split(",")]
        return [x for x in items if x]
    return raw.strip("'\"")


# ---------- 切块 ----------


def chunk_document(meta: dict[str, object], body: str) -> list[Chunk]:
    """把一篇文档的正文切成块，带上元数据和面包屑。

    流程：按标题切章节 → 章内按段落贪心装箱 → 补面包屑 → 分词。
    """
    title = str(meta.get("title", "")).strip()
    pieces: list[tuple[tuple[str, ...], str]] = []

    for path, lines in _split_sections(body):
        # 去掉与 title 重复的首段，避免出处里标题出现两次。
        if path and path[0] == title:
            path = path[1:]
        paragraphs = _split_paragraphs(lines)
        for bucket in _pack(paragraphs):
            pieces.append((path, bucket))

    pieces = _merge_tiny(pieces)

    chunks: list[Chunk] = []
    for i, (path, text) in enumerate(pieces):
        content = _with_breadcrumb(title, path, text)
        chunks.append(
            Chunk(
                index=i,
                content=content,
                section=path,
                content_hash=sha256(content),
            )
        )
    return chunks


def _split_sections(body: str) -> list[tuple[tuple[str, ...], list[str]]]:
    """按标题切章节，返回 (面包屑路径, 行列表)。

    用标题栈计算路径；层级大于 SECTION_HEADING_LEVEL 的标题保留为正文。
    """
    sections: list[tuple[tuple[str, ...], list[str]]] = []
    stack: list[tuple[int, str]] = []
    path: tuple[str, ...] = ()
    buf: list[str] = []

    def flush() -> None:
        if any(line.strip() for line in buf):
            sections.append((path, list(buf)))
        buf.clear()

    for line in body.split("\n"):
        match = _HEADING_RE.match(line)
        if match is None or len(match.group(1)) > SECTION_HEADING_LEVEL:
            # 不是章节边界就原样留着——深标题要作为正文的一部分进块，
            # 丢掉它会让"后海村"这类只在小标题里出现的名字彻底消失。
            buf.append(line)
            continue

        flush()
        level = len(match.group(1))
        text = match.group(2).strip()
        # 同级或更高级的标题把栈里比它深的都弹掉
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        path = tuple(t for _, t in stack)

    flush()
    return sections


def _split_paragraphs(lines: list[str]) -> list[str]:
    """按空行切段落；连续表格行保持完整，避免表头与数据分离导致检索不到。"""
    paragraphs: list[str] = []
    buf: list[str] = []
    in_table = False

    def flush() -> None:
        if buf:
            text = "\n".join(buf).strip()
            if text:
                paragraphs.append(text)
            buf.clear()

    for line in lines:
        is_table_row = line.lstrip().startswith("|")
        if is_table_row:
            if not in_table:
                flush()
                in_table = True
            buf.append(line)
        else:
            if in_table:
                flush()
                in_table = False
            if line.strip():
                buf.append(line)
            else:
                flush()
    flush()
    return paragraphs


def _pack(paragraphs: list[str]) -> list[str]:
    """把段落贪心装箱到 TARGET_CHARS 左右。

    装箱而不是"每段一块"：攻略里的段落常常只有一两句话，
    逐段成块会产生大量低于 MIN_CHARS 的碎片，每个碎片都要占一个向量和一个词频行。
    """
    buckets: list[str] = []
    cur: list[str] = []
    cur_len = 0

    for paragraph in paragraphs:
        for piece in _split_oversized(paragraph):
            if cur and cur_len + len(piece) > TARGET_CHARS:
                buckets.append("\n\n".join(cur))
                cur, cur_len = [], 0
            cur.append(piece)
            cur_len += len(piece)
            # piece 可能是超长表格，塞进来就直接顶到上限了，就地收口
            if cur_len >= MAX_CHARS:
                buckets.append("\n\n".join(cur))
                cur, cur_len = [], 0

    if cur:
        buckets.append("\n\n".join(cur))
    return buckets


def _split_oversized(paragraph: str) -> list[str]:
    """把一个超长段落拆成若干段；表格原样返回。

    拆分优先级：句末标点 → 硬切。按句子拆是首选，因为块的价值就在于
    "语义完整"；只有整段连标点都没有（比如一长串 URL 或没有句读的口语）
    才退到按字数硬切。
    """
    if len(paragraph) <= MAX_CHARS:
        return [paragraph]
    if paragraph.lstrip().startswith("|"):
        return [paragraph]

    pieces: list[str] = []
    cur = ""
    for sentence in _SENTENCE_RE.split(paragraph):
        if not sentence:
            continue
        if cur and len(cur) + len(sentence) > MAX_CHARS:
            pieces.append(cur)
            # 接缝处补一段重叠，避免"那句话的答案正好被切在缝上"
            cur = cur[-OVERLAP_CHARS:] + sentence
        else:
            cur += sentence
    if cur:
        pieces.append(cur)

    # 还是超长（整段没标点）就只能按字数硬切了
    out: list[str] = []
    for piece in pieces:
        while len(piece) > MAX_CHARS:
            out.append(piece[:MAX_CHARS])
            piece = piece[MAX_CHARS - OVERLAP_CHARS :]
        if piece:
            out.append(piece)
    return out


def _merge_tiny(pieces: list[tuple[tuple[str, ...], str]]) -> list[tuple[tuple[str, ...], str]]:
    """把过短的块并入同章节的上一块。

    只在【同一章节内】合并：跨章节合并会把"交通"的尾巴和"住宿"的开头
    黏成一个块，那块的主题是分裂的，检索时两边都命中不好。
    """
    merged: list[tuple[tuple[str, ...], str]] = []
    for path, text in pieces:
        if (
            len(text) < MIN_CHARS
            and merged
            and merged[-1][0] == path
            and len(merged[-1][1]) + len(text) <= MAX_CHARS
        ):
            prev_path, prev_text = merged[-1]
            merged[-1] = (prev_path, f"{prev_text}\n\n{text}")
        else:
            merged.append((path, text))
    return merged


def _with_breadcrumb(title: str, path: tuple[str, ...], body: str) -> str:
    """给块正文拼一行面包屑，形如【成都三日游攻略 > 第三天 > 交通】。

    这行同时喂给嵌入模型和精排模型。"第三天"这几个字对
    "第三天怎么去都江堰"这类问题是有信息量的——块本身就讲这件事，
    但没有面包屑时，向量里体现不出它属于哪一天。
    """
    # 文档的 H1 标题常常和 front matter 的 title 重复，去一下重
    parts = [title, *(t for t in path if t != title)] if title else list(path)
    parts = [p for p in parts if p]
    if not parts:
        return body
    return f"【{' > '.join(parts)}】\n{body}"
