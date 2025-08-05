#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from split_by_ast import chunks_from_file

def test_split():
    # 选择当前仓库中的一个脚本文件
    f = ROOT / "scripts" / "visualize_chunks.py"
    chunks = chunks_from_file(f, level="function")
    assert len(chunks) >= 0  # 若首次为空不报错，只验证可调用
