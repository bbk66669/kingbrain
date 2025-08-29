FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# 安装依赖
COPY orchestrator/requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# 拷贝应用代码（构建上下文为 orchestrator/）
COPY orchestrator/ /app/

EXPOSE 8000

# 直接启动 FastAPI 应用
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
