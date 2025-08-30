FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# 先装依赖（build context 是 orchestrator/，这里的 requirements.txt 指的就是 orchestrator/requirements.txt）
COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# 把 orchestrator/ 目录里的源码拷到容器内 /app/orchestrator/（作为 Python 包）
COPY . /app

EXPOSE 8000

# 明确跑包里的 ASGI 应用，避免命中根目录的 app.py
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000"]
