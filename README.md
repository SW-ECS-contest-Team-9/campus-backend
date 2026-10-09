# Campus Backend

## 기술 스택

| 구분 | 기술 |
|---|---|
| 언어 | Python 3.13 이상 |
| API | FastAPI |
| 서버 | Uvicorn |
| 데이터베이스 | PostgreSQL 16 |
| DB 드라이버 | psycopg 3 |
| 캐시 | Redis 7 |

## 개발 도구

| 도구 | 용도 |
|---|---|
| uv | 패키지·가상환경 관리 |
| Docker Compose | 컨테이너 실행 환경 관리 |
| Git · GitHub | 버전 관리·협업 |
| GitHub Actions | CI |
| CodeRabbit | PR 코드 리뷰 |

SQLAlchemy와 Alembic은 의존성에 등록되어 있으나, 현재 DB 연결은 psycopg로 직접 SQL을 실행합니다.

## 브랜치와 PR

- `main`: 배포용. 일반 변경은 PR로 병합.
- `develop`: 개발 통합용.
- `feature/*`, `docs/*`, `refactoring/*`, `fix/*`, `chore/*`: `develop`에서 분기 → 작업·검증 → PR → `develop` 병합.
- 배포 준비 완료 후 `develop` → `main` PR.
- `hotfix/*`: `main`에서 분기 → 검증 → PR 없이 `main` 병합·push. 같은 수정을 `develop`에도 반영.

PR은 목적·변경·확인·배포/후속을 각각 한 줄로 적습니다. 미검증은 이유를 적고, 배포 영향이나 후속 작업이 없으면 `없음`을 적습니다.
