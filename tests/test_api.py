import hashlib

import pytest
from fastapi.testclient import TestClient

from fixture_source import source
from mapdata import release, transform


@pytest.fixture
def client(clean):
    import main

    return TestClient(main.app)


@pytest.fixture
def published(clean):
    c, rep = transform.build(source())
    rid = release.create_draft(clean, c, rep, {"label": "fixture"})
    clean.commit()
    out = release.publish(clean, rid)
    clean.commit()
    return out


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "db": "ok"}


def test_latest_without_release(client):
    r = client.get("/v1/map/releases/latest")
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_release"
    r = client.get("/v1/places?q=x")
    assert r.status_code == 503 and r.json()["error"]["code"] == "no_release"


def test_latest_package_checksum(client, published):
    info = client.get("/v1/map/releases/latest").json()
    assert set(info) == {"id", "version", "schemaVersion", "sizeBytes", "sha256", "url", "publishedAt"}
    assert info["version"] == 1 and info["schemaVersion"] == 1
    r = client.get(info["url"])
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == info["sha256"] and len(r.content) == info["sizeBytes"]
    assert r.headers["etag"] == f'"{info["sha256"]}"'
    r2 = client.get(info["url"], headers={"If-None-Match": r.headers["etag"]})
    assert r2.status_code == 304
    assert client.get("/v1/map/releases/999/package").json()["error"]["code"] == "release_not_found"


def test_newer_release_becomes_latest(client, published, clean):
    c, rep = transform.build(source())
    rid = release.create_draft(clean, c, rep, {"label": "fixture 2"})
    clean.commit()
    assert client.get("/v1/map/releases/latest").json()["version"] == 1  # draft 는 안 보임
    release.publish(clean, rid)
    clean.commit()
    assert client.get("/v1/map/releases/latest").json()["version"] == 2


def test_places_search(client, published):
    items = client.get("/v1/places", params={"q": "가 입구"}).json()["items"]
    assert items[0]["name"] == "가관 입구" and items[0]["floorCode"] == "1F"
    items = client.get("/v1/places", params={"q": "휴게실 2층"}).json()["items"]
    assert [i["key"] for i in items] == ["p:p_room"]
    items = client.get("/v1/places", params={"building": "가관", "floor": "2층"}).json()["items"]
    assert [i["key"] for i in items] == ["p:p_room"]
    items = client.get("/v1/places", params={"q": "  정 문 "}).json()["items"]
    assert items[0]["key"] == "p:p_gate"
    far = client.get("/v1/places", params={"q": "먼곳"}).json()["items"][0]
    assert far["routable"] is False


def test_route_shortest_uses_stairs(client, published):
    r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room"}).json()
    kinds = [s["kind"] for s in r["steps"]]
    assert "stairs" in kinds and "금지 지름길" not in [s["name"] for s in r["steps"]]
    st = next(s for s in r["steps"] if s["kind"] == "stairs")
    assert st["floorChange"] and st["from"]["floorCode"] == "1F" and st["to"]["floorCode"] == "2F"
    assert r["distanceM"] > 0 and len(r["geometry"]) >= 2


def test_route_avoid_stairs_and_prefer_elevator(client, published):
    for mode in ("avoidStairs", "preferElevator"):
        r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room", "mode": mode}).json()
        kinds = [s["kind"] for s in r["steps"]]
        assert "stairs" not in kinds and "elevator" in kinds, mode
        assert next(s for s in r["steps"] if s["kind"] == "elevator")["instruction"].startswith("승강기로")


def test_avoid_stairs_no_route(client, published, clean):
    # 승강기를 통행 금지로 바꾼 꾸러미를 따로 공개: 계단 피하기는 길이 없어야 함
    c, rep = transform.build(source())
    for e in c["edges"]:
        if e["kind"] == "elevator":
            e["pedestrianAccess"] = "prohibited"
    rid = release.create_draft(clean, c, rep, {"label": "no elevator"})
    clean.commit()
    release.publish(clean, rid)
    clean.commit()
    r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room", "mode": "avoidStairs"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_route" and r.json()["error"]["details"]["mode"] == "avoidStairs"
    assert client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room"}).status_code == 200


def test_route_no_route_island_and_one_way(client, published):
    r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_island"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_route"
    assert client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_o3"}).status_code == 200
    back = client.get("/v1/routes", params={"from": "p:p_o3", "to": "p:p_gate"})
    assert back.status_code == 404  # 한 방향 길을 거꾸로 가지 않음


def test_route_xyz_and_errors(client, published):
    r = client.get("/v1/routes", params={"from": "1,0,100", "to": "30,5,104"})
    assert r.status_code == 200 and r.json()["from"]["type"] == "point"
    assert (
        client.get("/v1/routes", params={"from": "1,0,130", "to": "p:p_room"}).json()["error"]["code"]
        == "no_nearby_node"
    )
    assert (
        client.get("/v1/routes", params={"from": "p:p_far", "to": "p:p_room"}).json()["error"]["code"]
        == "place_not_routable"
    )
    assert client.get("/v1/routes", params={"from": "zzz", "to": "p:p_room"}).json()["error"]["code"] == "bad_endpoint"
    assert (
        client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room", "mode": "x"}).json()["error"]["code"]
        == "bad_mode"
    )
    assert client.get("/v1/routes", params={"to": "p:p_room"}).json()["error"]["code"] == "invalid_request"


def test_cors(client, published):
    r = client.get("/v1/map/releases/latest", headers={"Origin": "http://localhost:8081"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:8081"


def test_route_all_modes_with_summary(client, published):
    for mode in ("fastest", "accessible", "rainProtected", "nightSafe"):
        r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room", "mode": mode})
        assert r.status_code == 200, mode
        body = r.json()
        assert body["mode"] == mode and {"climbM", "steps", "rainExposedM", "wheelchair"} <= set(body["summary"])
    r = client.get("/v1/routes", params={"from": "p:p_gate", "to": "p:p_room", "mode": "accessible"}).json()
    assert "stairs" not in [s["kind"] for s in r["steps"]] and r["note"]


def test_route_modes_spec(client):
    spec = client.get("/v1/route-modes").json()
    assert spec["modes"]["rainProtected"]["exposure"] == {"rain": 2.0}
    assert spec["modes"]["accessible"]["exclude"] == ["stairs", "wheelchairNo"]
