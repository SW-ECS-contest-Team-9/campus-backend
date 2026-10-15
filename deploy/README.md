# 서버 배포 (트래커 운영 서버)

트래커 운영 서버(`di-lab@100.93.21.92`, Tailscale)에 이 API 와 전용 Postgres 를 Docker 로 띄운다. 트래커(`~/Desktop/CampusTracker-Server`, 3000·5173, DB `campus-collector-db` 5433)와는 따로 돈다.

| 무엇 | 값 |
|---|---|
| API | `http://100.93.21.92:8010` (컨테이너 `api`, 켜질 때 `alembic upgrade head` 뒤 uvicorn) |
| DB | 전용 Postgres 16, 서버 안에서만 `127.0.0.1:5435`, 볼륨 `campus-backend-pgdata` |
| 꾸러미 파일 | 볼륨 `campus-backend-releases` |
| 설정 | `deploy/.env` (`.env.server.example` 복사본) |

이 문서와 compose 파일은 저장소에 있다. 비밀값(DB 비밀번호)은 서버의 `deploy/.env` 에만 둔다(커밋 안 됨).
명령은 모두 저장소 루트(`~/Desktop/campus-backend`)에서 돌린다. 아래 `DC` 는 `docker compose -f deploy/compose.server.yml` 의 줄임이다.

## 1. 처음 한 번

```sh
cd ~/Desktop
git clone https://github.com/SW-ECS-contest-Team-9/campus-backend.git   # 비공개 저장소: 트래커를 받는 것과 같은 GitHub 로그인
cd campus-backend
cp deploy/.env.server.example deploy/.env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" deploy/.env
docker compose -f deploy/compose.server.yml up -d --build --wait
curl http://127.0.0.1:8010/health        # {"status":"ok","db":"ok"}
```

- 배포 가지는 `main`(병합 전 시험이면 그 가지로 `git switch`).
- `POSTGRES_PASSWORD` 는 볼륨을 처음 만들 때만 적용된다.

## 2. 지도 가져오기·공개

```sh
docker exec campus-collector-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --schema=mobility' > /tmp/mobility.sql
deploy/import-release.sh /tmp/mobility.sql
```

- 원천: 트래커 읽기 API(`TRACKER_API`, 기본 `http://host.docker.internal:3000`) + 위 SQL(보행 통행·방향·휠체어, 노드 층, 열린 면). 트래커 DB 는 읽기만 한다.
- pg_dump 의 `circular foreign-key constraints` 경고는 무시해도 된다.
- 검증 오류가 없으면 새 버전으로 공개하고 보고서(JSON)와 지금 최신을 찍는다. 오류가 있으면 공개하지 않고 종료 코드 1.
- 컨테이너에서 트래커 API 에 닿지 않으면 `deploy/.env` 의 `TRACKER_API=http://100.93.21.92:3000` 로 바꾸고 `DC up -d --wait`.

## 3. 업데이트

```sh
cd ~/Desktop/campus-backend
docker tag campus-backend-api:latest campus-backend-api:prev   # 되돌리기용
git pull
docker compose -f deploy/compose.server.yml up -d --build --wait
```

트래커 도로망이 바뀌었으면 이어서 2절을 돌린다.

## 4. 확인

```sh
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/v1/map/releases/latest
docker compose -f deploy/compose.server.yml ps
```

- `API_BIND` 를 Tailscale IP 로 했다면 127.0.0.1 대신 그 IP.
- 다른 기기(Tailscale): `curl http://100.93.21.92:8010/health`.

## 5. 되돌리기

| 무엇 | 방법 |
|---|---|
| 코드(이미지) | `API_TAG=prev docker compose -f deploy/compose.server.yml up -d --no-build --wait api`. 새것으로 돌아갈 때는 `API_TAG` 없이 `up -d --wait api` |
| 마이그레이션이 더해진 업데이트 | 옛 이미지가 새 리비전을 몰라 켜지지 않는다. 먼저 새 이미지에서 `DC exec api alembic downgrade <옛 리비전>` 뒤 위 줄 |
| 지도 릴리스 | 아래처럼 문제 릴리스를 `withdrawn` 으로 바꾸면 그 앞 공개본이 최신이 된다 |

```sh
echo "SELECT id, version, status, published_at FROM map_release ORDER BY id DESC LIMIT 5;" \
  | docker compose -f deploy/compose.server.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
echo "UPDATE map_release SET status = 'withdrawn' WHERE id = <새 id>;" \
  | docker compose -f deploy/compose.server.yml exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

- 이미 새 버전을 받은 앱은 그대로 쓴다(앱은 받은 버전이 최신 이상이면 받지 않음). 고친 자료로 새 릴리스를 내는 편이 낫다.

## 6. 로그·멈춤

```sh
docker compose -f deploy/compose.server.yml logs -f --tail=100 api
docker compose -f deploy/compose.server.yml down      # 멈춤(자료 유지)
```

`down -v` 는 DB 와 꾸러미 볼륨을 지운다. 쓰지 않는다.
Docker Desktop 이 내려가면 API 도 멈춘다. 다시 켜지면 `restart: unless-stopped` 로 함께 뜬다.

## 7. 망·방화벽

- API 는 기본 `0.0.0.0:8010`(LAN·Tailscale 모두). Tailscale 로만 열려면 `deploy/.env` 에 `API_BIND=100.93.21.92`(Tailscale 이 먼저 떠 있어야 함).
- 서버 방화벽(ufw 등)을 쓰면 8010/tcp 를 Tailscale 에만 연다: `sudo ufw allow in on tailscale0 to any port 8010 proto tcp`.
- DB 5435 는 127.0.0.1 에만 열린다.
- S21 등 휴대폰은 Tailscale 앱으로 같은 tailnet 에 로그인해야 `100.93.21.92` 에 닿는다. 개발 중 PC 의 로컬 서버를 쓸 때는 USB `adb reverse tcp:8010 tcp:8010`.

## 8. 앱의 서버 주소

- 빌드 때 `EXPO_PUBLIC_DEV_HOST=100.93.21.92` 를 주고 `npx expo prebuild --platform android` → APK(campus-client `docs/개발-가이드.md`). 앱 기본 주소가 `http://100.93.21.92:8010` 이 되고, Android 평문 HTTP 가 그 주소에만 허용된다.
- 앱 설정 화면에서 서버 주소를 바꿀 수 있지만, 평문 HTTP 는 빌드 때 넣은 주소에만 된다(다른 주소면 다시 빌드).
- USB 개발용 빌드는 `EXPO_PUBLIC_USB_REVERSE=1`(기본 주소 `127.0.0.1`).
