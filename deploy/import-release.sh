#!/bin/sh
# 지도 릴리스 가져오기 → 검증 오류가 없으면 공개. api 컨테이너 안에서 돈다.
# 원천: 트래커 읽기 API(deploy/.env 의 TRACKER_API) + mobility SQL 내보내기(인자, 기본 /tmp/mobility.sql).
# 사용(저장소 루트 어디서든): deploy/import-release.sh [/tmp/mobility.sql]
set -eu
cd "$(dirname "$0")/.."

EXPORT=${1:-/tmp/mobility.sql}
if [ ! -s "$EXPORT" ]; then
    echo "내보내기 파일이 없거나 비어 있음: $EXPORT" >&2
    exit 1
fi

DC="docker compose -f deploy/compose.server.yml"

# 파일은 표준 입력으로 넘긴다(Docker Desktop 의 파일 공유 설정과 무관).
set +e
$DC exec -T api sh -c '
    f=/tmp/mobility-import.sql
    cat > "$f" &&
    python scripts/import_release.py --api "$TRACKER_API" --export "$f" --publish
    rc=$?
    rm -f "$f"
    exit $rc
' < "$EXPORT"
rc=$?
set -e

echo
if [ "$rc" -eq 0 ]; then
    echo "공개됨. 지금 최신:"
else
    echo "검증 오류 또는 실패(종료 코드 $rc): 공개하지 않았음. 지금 최신(바뀌지 않음):"
fi
$DC exec -T api python -c "
import urllib.request as u
try:
    print(u.urlopen('http://127.0.0.1:8010/v1/map/releases/latest', timeout=5).read().decode())
except Exception as e:
    print(e)
" || true
exit "$rc"
