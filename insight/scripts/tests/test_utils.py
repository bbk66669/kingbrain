#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from kingbrain.utils import extract_keywords

def test_extract_keywords():
    text = "load_balance function with retry and config"
    keywords = extract_keywords(text, limit=10)
    assert "loadbalance" in keywords
    assert "retry" in keywords
    assert "config" in keywords

def test_extract_keywords_chinese():
    text = "负载均衡函数包含重试和配置"
    keywords = extract_keywords(text, limit=10)
    # 允许分词差异，匹配关键字存在即可
    assert any(k in keywords for k in ["负载均衡", "负载", "均衡"])
    assert "重试" in keywords or any("重试" in k for k in keywords)
    assert "配置" in keywords or any("配置" in k for k in keywords)
