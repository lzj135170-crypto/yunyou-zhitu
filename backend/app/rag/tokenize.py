"""BM25 切词：入库和查询必须使用同一个函数。

两边切法不一致会让词表对不上，BM25 静默失效。
用 lcut_for_search 同时保留长词和子词，解决「北京烤鸭」这类长专名匹配。
"""

import re
from collections.abc import Iterable

import jieba

# 只收最明显的一层。"成都""推荐""攻略"这类高频词不进来：
# BM25 的 IDF 本来就会把它们压下去，手工停用词表只会引入新的不一致。
_STOPWORDS = frozenset(
    {
        "的", "了", "是", "在", "有", "和", "与", "及", "或", "就", "都",
        "也", "很", "还", "又", "把", "被", "让", "给", "对", "从", "到",
        "去", "来", "这", "那", "哪", "什么", "怎么", "怎样", "如何",
        "为什么", "可以", "能否", "是否", "推荐", "请问", "一下", "一些",
        "一个", "这个", "那个", "哪些", "哪里", "多少", "几个", "吗",
        "呢", "吧", "啊", "呀", "哦", "嗯",
    }
)

_PUNCT_ONLY = re.compile(r"^[\s\W_]+$", re.UNICODE)


def postprocess(tokens: Iterable[str]) -> list[str]:
    """把分词结果收成 BM25 规范词表：保留英文和数字，丢弃单字、标点和停用词。"""
    out: list[str] = []
    for raw in tokens:
        token = raw.strip()
        if not token or _PUNCT_ONLY.match(token) or token in _STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        out.append(token)
    return out


def tokenize(text: str) -> list[str]:
    """切词。入库和查询都必须调这一个函数，见模块开头的对称性说明。"""
    return postprocess(jieba.lcut_for_search((text or "").lower()))
