# campus-backend API 이미지. 켜질 때 마이그레이션(alembic upgrade head) 뒤 uvicorn 0.0.0.0:8010.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MAP_DATA_DIR=/data/releases

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .

# 꾸러미 파일 폴더는 볼륨으로 붙인다. 처음 붙일 때 이 소유권이 따라간다.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/releases \
    && chown app /data/releases
USER app

EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import json, sys, urllib.request as u; sys.exit(json.load(u.urlopen('http://127.0.0.1:8010/health', timeout=4))['status'] != 'ok')"]

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8010"]
