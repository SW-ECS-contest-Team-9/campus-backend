작성: Codex

# 광고 조회 API

- 계약 근거: campus-client 원격 feat/advertisement-v2, 3148272a2db56ff6b6b65aae5e69d0455b6e56ef의 docs/advertisements-api.md 및 src/api/advertisements.ts.
- 인증 없는 GET /v1/advertisements → 200 {"count": N, "items": [...]}.
- 항목: id, title, imageUrl, targetUrl, startsAt, endsAt, isActive. DB 컬럼은 snake_case.
- startsAt/endsAt은 타임존 포함 ISO 8601 또는 null(해당 경계 없음).
- is_active=true, starts_at <= now(), now() < ends_at만 반환. 시작 포함·종료 제외.
- 우선순위 컬럼은 계약에 없어 id 오름차순으로 고정. 클라이언트는 첫 유효 광고를 표시.
- 광고 없으면 count=0, items=[]. 지도 릴리스와 독립.

## 통합 및 배포

- 0006_walk_mesh_classes 다음에 0007_advertisements 적용: uv run alembic upgrade head.
- API 프로세스가 새 라우터를 사용하기 전에 마이그레이션을 적용한다. 미적용 상태는 정상 빈 목록이 아닌 DB 오류다.
- 신규 행의 is_active 기본값은 false. 운영자가 유효한 데이터를 넣고 명시적으로 활성화해야 한다. 샘플 광고는 생성하지 않는다.
- title은 비어 있지 않아야 하고, 기간 양끝이 있으면 starts_at < ends_at이어야 한다.
- image_url은 기기에서 접근 가능한 HTTP(S) 또는 서버 기준 상대 경로, target_url은 절대 HTTP(S)로 등록한다.
- 이미지 호스팅·업로드·광고 관리 API·집계는 이번 범위에 없다. URL 검증은 기존 클라이언트에서도 수행한다.
- downgrade는 광고 테이블과 데이터를 삭제한다. 되돌리기 전 백업 필요.
- 커밋·푸시·실서버 배포는 수행하지 않음. 부모 작업에서 통합·배포.

## 검증

- tests/test_advertisements.py: 실제 PostgreSQL의 활성/기간/정렬, API 빈 결과·camelCase·시간 오프셋, 잘못된 기간 거부, 기본 비활성.
- tests/test_migration.py: advertisements 포함 upgrade → downgrade → upgrade.
- 전용 로컬 시험 DB POSTGRES_TEST_DB=campus_ads_test_20261101 사용. 테스트 종료 시 삭제.

- 2026-11-01 실행 결과: 전체 pytest 136 passed, Ruff check/format 통과, git diff --check 통과. 기존 Starlette/httpx 사용 중단 예정 경고 1건. 실서버·실기기는 미검증.
