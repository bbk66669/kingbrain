# -*- coding: utf-8 -*-
"""
kingbrain/utils.py
共享工具函数

CHANGELOG
- 新增 normalize_token，将 load_balance 等归一为 loadbalance。
- 支持中文停用词扩展；英文/中文统一抽取。
"""

import re
import jieba
from typing import List, Dict

STOP_WORDS = {
    "def", "class", "return", "if", "for", "while", "and", "or", "import",
    "from", "as",
    "函数", "类", "返回", "如果", "循环", "导入"
}
_kw_re = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]+")

DOMAIN_WEIGHTS = {
    "loadbalance": 2, "retry": 1.5, "network": 1.5,
    "trailing_mgr": 2, "update_logic": 1.5, "config": 1.5,
    "error_handling": 1.5, "api": 1.5
}

def normalize_token(w: str) -> str:
    w = w.strip()
    w = w.replace("load_balance", "loadbalance")
    w = w.replace("load-bal", "loadbalance")
    if w == "lb":
        return "loadbalance"
    return w

def extract_keywords(text: str, limit: int = 15, corpus: List[str] = None) -> List[str]:
    scored: Dict[str, float] = {}
    words: List[str] = []
    if any("\u4e00" <= c <= "\u9fa5" for c in text):
        words.extend([w for w in jieba.cut(text.lower(), cut_all=False)])
    else:
        words.extend(_kw_re.findall(text.lower()))

    if corpus:
        # 可选 TF-IDF（若安装了 sklearn）
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            vectorizer = TfidfVectorizer(stop_words=list(STOP_WORDS),
                                         token_pattern=r"[A-Za-z]{3,}|[\u4e00-\u9fa5]+")
            tfidf_matrix = vectorizer.fit_transform([text] + corpus)
            feature_names = vectorizer.get_feature_names_out()
            scores = tfidf_matrix[0].toarray()[0]
            for w, s in zip(feature_names, scores):
                w2 = normalize_token(w)
                if w2 in STOP_WORDS: continue
                if s > 0:
                    scored[w2] = scored.get(w2, 0.0) + float(s)
        except Exception:
            pass

    if not scored:
        for w in words:
            w2 = normalize_token(w)
            if w2 in STOP_WORDS:
                continue
            scored[w2] = scored.get(w2, 0.0) + 1.0

    scored = {w: s * DOMAIN_WEIGHTS.get(w, 1.0) for w, s in scored.items()}
    special = [w for w in scored if "_" in w or (w and w[0].isupper())]
    ordered = special + sorted(
        (w for w in scored if w not in special),
        key=lambda x: scored[x],
        reverse=True
    )
    return ordered[:limit]
