# Campus Backend

Campus Tracker 서비스의 백엔드를 구축하는 Python/FastAPI 프로젝트입니다. 현재는 초기 구현 단계이며 API와 데이터 구조를 개발하고 있습니다. 기존 TypeScript/Express 기반 `campus-tracker` 서버와의 연계 및 역할 분담은 설계 중입니다.

## 현재 상태

- FastAPI 앱과 `/`, `/data` 라우트 작성.
- PostgreSQL 연결 및 `SELECT 1` 실행 확인.
- PostgreSQL·Redis Docker Compose 설정 작성.
- GitHub Actions: 의존성 설치 및 Python 문법 검사. API·DB 통합 테스트와 CD는 아직 구성하지 않았습니다.
- ERD와 첫 서비스 기능은 설계 중입니다.

## 설치

Python 3.13 이상, uv, Docker Compose가 필요합니다.

```powershell
uv sync --locked
```

프로젝트 루트에 `.env`를 만들고 DB 접속 정보를 작성합니다.

```dotenv
POSTGRES_DB=campus_tracking
POSTGRES_USER=개발용_사용자명
POSTGRES_PASSWORD=개발용_비밀번호
POSTGRES_PORT=5444
```

실제 `.env`는 Git에 포함하지 않습니다. Compose와 Python은 각각 환경 변수를 읽습니다. 이미 초기화된 DB 볼륨의 계정은 `.env` 변경만으로 바뀌지 않습니다.

## PostgreSQL 연결 확인

```powershell
docker compose up -d postgres
docker compose ps
uv run python database.py
```

`(1,)` 출력은 `SELECT 1` 성공을 의미합니다. 호스트에서 접속하는 DB 주소는 `127.0.0.1:5444`입니다.

## API 실행

```powershell
uv run uvicorn main:app --reload
```

- API: http://127.0.0.1:8000
- 문서: http://127.0.0.1:8000/docs

현재 `main.py`의 `from data import router`는 파일 위치와 맞지 않습니다. 실행 전 `from routers.data import router`로 수정해야 합니다. 이 문서 변경에는 코드 수정을 포함하지 않았습니다.

## 파일 구성

```text
main.py                   앱 생성·라우터 등록
routers/data.py           /data 요청 핸들러
routers/health.py         헬스 라우터 작업 파일
database.py               PostgreSQL 연결 확인
compose.yaml              PostgreSQL·Redis 설정
.github/workflows/ci.yml   CI
```

Redis 설정의 `everysec` 앞에 `--appendfsync`가 누락되어 있어 수정·실행 확인이 필요합니다. PostgreSQL 연결 확인은 `postgres` 서비스만 실행하여 진행할 수 있습니다.

## 브랜치와 PR

- `main`: 배포용. 일반 변경은 PR로 병합.
- `develop`: 개발 통합용.
- `feature/*`, `docs/*`, `refactoring/*`, `fix/*`, `chore/*`: `develop`에서 분기 → 작업·검증 → PR → `develop` 병합.
- 배포 준비 완료 후 `develop` → `main` PR.
- `hotfix/*`: `main`에서 분기 → 검증 → PR 없이 `main` 병합·push. 같은 수정을 `develop`에도 반영.

PR은 목적·변경·확인·배포/후속을 각각 한 줄로 적습니다. 미검증은 이유를 적고, 배포 영향이나 후속 작업이 없으면 `없음`을 적습니다.
