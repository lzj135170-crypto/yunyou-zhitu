"""查询文本的最小清洗：只做表示层标准化，不改变词项集合。

NFKC 规范化全角/兼容字符，去掉零宽和控制字符，合并空白。
不去标点、不删内容词、不做改写、纠错或繁简转换；分词交给 tokenize。
入库语料无需清洗，查询侧清洗后再走同一个 tokenize；filters 不受影响。
"""

import re
import unicodedata

# 这些 Unicode 类别是"不可见/控制/格式"字符。保留空白类，其余丢掉。
#   Cc 控制字符、Cf 格式字符（零宽、BOM、软连字符）、Cs 代理项、
#   Co 私用区、Cn 未分配。
_CONTROL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

_WHITESPACE = re.compile(r"\s+")

# 显式保留的空白。NFKC 会把 U+3000 等映射成普通空格，这里再兜一层。
_KEEP_AS_SPACE = frozenset(" \t\r\n\v\f")


def minimal_clean(text: str) -> str:
    """返回最小清洗后的查询文本；只做表示层变换，不改变词项集合。"""
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)

    kept: list[str] = []
    for ch in text:
        if ch in _KEEP_AS_SPACE:
            kept.append(" ")
            continue
        if unicodedata.category(ch) in _CONTROL_CATEGORIES:
            continue
        kept.append(ch)

    return _WHITESPACE.sub(" ", "".join(kept)).strip()
