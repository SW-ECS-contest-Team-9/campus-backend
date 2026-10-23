#!/bin/sh
# 지도 릴리스 가져오기 → 검증 오류가 없으면 공개. api 컨테이너 안에서 돈다.
# 원천: 트래커 읽기 API(deploy/.env 의 TRACKER_API) + mobility SQL 내보내기(인자, 기본 /tmp/mobility.sql)
#       + 걷는 면 원천(compose 가 읽기 전용으로 붙임): /srv/structures(STRUCTURES_DIR), /srv/walkable-areas-5186.geojson(WALKABLE_GEOJSON).
#       + 실내 빈 곳 /srv/interiors.geojson(INTERIORS_GEOJSON). 없거나 비었으면 실내 구조 없이 가져온다(그물은 복도 띠로만).
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
    S=/srv/structures W=/srv/walkable-areas-5186.geojson I=/srv/interiors.geojson
    if ! ls "$S"/*.geojson >/dev/null 2>&1 || [ ! -s "$W" ]; then
        echo "걷는 면 원천이 없음($S 에 geojson, $W): deploy/.env 의 STRUCTURES_DIR·WALKABLE_GEOJSON 확인 뒤 up -d" >&2
        exit 1
    fi
    if [ -f "$I" ] && [ -s "$I" ]; then
        set -- --interiors "$I"
    else
        echo "실내 구조 없이 그물은 복도 띠로만($I 없음: deploy/.env 의 INTERIORS_GEOJSON 확인 뒤 up -d)" >&2
        set --
    fi
    cat > "$f" &&
    python scripts/import_release.py --api "$TRACKER_API" --export "$f" --structures "$S" --walkable "$W" "$@" --publish
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
