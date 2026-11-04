# ==========================================
# OpsCopilot Agent (Backend) Dockerfile
# ==========================================
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 复制依赖文件并安装 Python 包（包含已预编译好 wheel 的 greenlet、cryptography 等）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端工程源码
COPY . .

# 暴露 FastAPI HTTP 与 WebSocket 端口
EXPOSE 8000

# 容器健康检查：直接使用 Python 原生标准库，无需外部 curl 依赖，杜绝 apt 报错与网络故障
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# 启动服务
CMD ["python", "-m", "uvicorn", "adapter.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
