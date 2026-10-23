# 캠퍼스 백엔드

서경대학교 캠퍼스 길찾기 서비스의 백엔드입니다.

## 개발 목적

건물 밖에서 강의실 앞까지, 층과 층 사이를 잇는 캠퍼스 길 안내를 제공합니다.
사용자 앱이 인터넷이 없어도 길을 찾을 수 있도록 검증된 캠퍼스 지도(건물·층·길·장소)를 묶어 배포하고, 연결되어 있을 때는 장소 검색과 경로 계산을 돕습니다.

## 사용 도구

### 기술 스택

| 구분 | 기술 |
|---|---|
| 언어 | Python 3.13 이상 |
| API | FastAPI |
| 서버 | Uvicorn |
| 데이터베이스 | PostgreSQL 16 |
| DB 드라이버 | psycopg 3 |
| 마이그레이션 | Alembic |
| 캐시 | Redis 7 |

### 개발 도구

| 도구 | 용도 |
|---|---|
| uv | 패키지·가상환경 관리 |
| Docker Compose | 컨테이너 실행 환경 관리 |
| pytest | 시험 |
| ruff · pre-commit | 코드 검사·정렬, 커밋 전 검사 |
| Git · GitHub | 버전 관리·협업 |
| GitHub Actions | CI |
| CodeRabbit | PR 코드 리뷰 |

## 브랜치와 PR

![브랜치 전략](docs/images/branch-strategy.svg)

- `main`: 배포용. 일반 변경은 PR로 병합.
- `develop`: 개발 통합용.
- `feature/*`, `docs/*`, `refactoring/*`, `fix/*`, `chore/*`: `develop`에서 분기 → 작업·검증 → PR → `develop` 병합.
- 배포 준비 완료 후 `develop` → `main` PR.
- `hotfix/*`: `main`에서 분기 → 검증 → PR 없이 `main` 병합·push. 같은 수정을 `develop`에도 반영.

PR은 목적·변경·확인·배포/후속을 각각 한 줄로 적습니다. 미검증은 이유를 적고, 배포 영향이나 후속 작업이 없으면 `없음`을 적습니다.

실행 방법과 개발 명령은 [개발 가이드](docs/개발-가이드.md)에 있습니다.
