FROM python:3.11-slim

WORKDIR /app

# 安静、无缓存安装
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# 依赖（无 requirements.txt 时就按你列的包装）
RUN pip install --upgrade pip && \
    pip install --no-cache-dir langgraph openai anthropic pyyaml flask

# 拷贝代码（注意：你的 Pipeline 已把 build context 设为 orchestrator/）
COPY . /app/

# 端口
EXPOSE 8000

# 启动（按你原来的）
CMD ["python", "-m", "orchestrator"]
