FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# 依赖
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 把整个 orchestrator/ 目录内容直接拷到 /app
COPY . .

EXPOSE 8000

# 启动 ASGI 应用（文件就在 /app/app.py）
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
