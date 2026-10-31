작성: Codex

# 광고 API 운영 반영 결과 — 2026-11-01

## 범위와 계약
- 기존 Curie 작업을 재사용했다. 신규 중복 구현과 client 수정 없음.
- 인증 없는 GET /v1/advertisements. 요청 본문·쿼리 없음.
- 응답: {"count": N, "items": [...]}. 항목은 id,title,imageUrl,targetUrl,startsAt,endsAt,isActive.
- 시작 포함·종료 제외, null은 해당 기간 경계 없음. 활성 광고만 id 오름차순.
- 빈 광고: HTTP 200 {"count":0,"items":[]}.
- 노출·클릭 기록 API/집계 없음. 클라이언트는 유효한 첫 광고를 보여주며 외부 targetUrl을 연다.
- 프론트 계약: origin/feat/advertisement-v2 docs/advertisements-api.md 직접 확인.

## 검증을 구분한 결과
- 로컬 최신 실행: pytest 136 passed, 1 warning (Starlette/httpx deprecation). 전용 시험 DB campus_ads_verify_20261101.
- Ruff check 및 format --check, git diff --check 통과.
- 운영 대상: di-lab@100.93.21.92 ~/Desktop/campus-backend, deploy/compose.server.yml, API 8010. 트래커 서버는 수정하지 않음.
- 배포 전 서버 Git: 415f787, 변경 없음. API·PostgreSQL healthy, Alembic 0006.
- 배포 후: API·PostgreSQL healthy, Alembic 0007_advertisements (head).
- 서버 localhost:8010/health 실제 응답: {"status":"ok","db":"ok"}.
- 서버 localhost:8010/v1/advertisements 실제 응답: {"count":0,"items":[]}.
- 광고 데이터 등록·샘플 생성 없음. 실기기 이미지 표시·클릭은 미검증.
- 광고 변경 파일만 tar로 전송 후 API만 build/up --wait. DB 볼륨 삭제·교체 없음. 커밋·푸시 없음. 서버에는 의도한 미커밋 변경이 남음.

## 복구 수단
- 서버 ~/backups/campus-backend/ads-20261101/: database.dump (pg_dump -Fc), source.tar (배포 전 git archive), change.tar, build.log.
- 기존 API 이미지: campus-backend-api:before-ads-20261101.
- 앱만 복구: API_TAG=before-ads-20261101 docker compose -f deploy/compose.server.yml up -d --no-build --wait api.
- 0007은 추가 테이블이므로 앱 이미지 롤백 시 DB는 0007로 유지한다. 기존 이미지의 시작 명령 alembic upgrade head가 미인식 revision으로 실패할 수 있으므로, 롤백 시 별도 compose override로 api.command를 ["uvicorn","main:app","--host","0.0.0.0","--port","8010"]로 지정하여 자동 migration을 건너뛰어야 한다.
- downgrade/DB 복원은 광고 데이터 손실 가능성이 있어 수행하지 않음. 필요하면 별도 승인과 복구 검토 후 진행.

## 남은 일
- 실제 광고 이미지·링크·게시 기간 등록 및 활성화, 실기기 표시/링크 검증.
- 소스 커밋·푸시와 서버 변경 정리는 별도 통합 작업.

## 프론트 실제 API 함수 연결 확인
- 2026-11-01 origin/feat/advertisement-v2의 src/api/advertisements.ts를 메모리에서 TypeScript 변환해 실행했다. 프론트 파일 수정 없음.
- 운영 http://100.93.21.92:8010 호출: HTTP 200, {"count":0,"items":[]}.
- 실제 fetchAdvertisements 결과 [], currentAdvertisement 결과 null. 원본 응답과 프론트 파싱 결과 일치: PASS.
- 현재 C:\campus-client는 local/integration-20261101이며 src/api/advertisements.ts가 없다. 따라서 광고 브랜치 API의 운영 연결은 확인했지만 현재 통합 앱 화면 연결 완료를 의미하지 않는다.
- 프론트 담당이 광고 브랜치의 API/배너/화면 변경을 통합해야 한다. 기존 미커밋 변경이 다수 있어 이 백엔드 작업에서는 client를 수정하지 않았다.
