#!/usr/bin/env python3
import sys, pkgutil

def ver(mod):
    try:
        m = __import__(mod)
        return getattr(m, '__version__', 'unknown')
    except Exception:
        return 'not-installed'

mods = [
    'openai','aiohttp','requests','prometheus_client',
    'tiktoken','jieba','rapidfuzz','spacy','boto3'
]
for m in mods:
    print(f'{m}: {ver(m)}')

# OpenAI 主版本
import openai
assert str(openai.__version__).split('.')[0] >= '1', 'openai major must be >=1'
print('Dependency versions look OK')
