#!/usr/bin/env sh
# 개발 명령 모음 (macOS·Linux·Git Bash). 사용: ./scripts/dev.sh <명령> [인자...]
set -e
cd "$(dirname "$0")/.."
task="${1:-help}"
[ $# -gt 0 ] && shift

case "$task" in
  setup)
    uv sync
    [ -f .env ] || { cp .env.example .env; echo ".env 를 만들었습니다. 값을 확인하세요."; }
    uv run pre-commit install
    ;;
  db-up) docker compose up -d --wait postgres redis ;;
  migrate) uv run alembic upgrade head ;;
  import-release) uv run python scripts/import_release.py "$@" ;;
  run) uv run uvicorn main:app --reload --port "${PORT:-8000}" ;;
  test) uv run pytest "$@" ;;
  lint) uv run ruff check . && uv run ruff format --check . ;;
  format) uv run ruff check --fix . && uv run ruff format . ;;
  *) echo "명령: setup | db-up | migrate | import-release [인자] | run | test [인자] | lint | format" ;;
esac
