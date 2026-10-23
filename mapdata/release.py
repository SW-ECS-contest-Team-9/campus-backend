"""릴리스 DB 쓰기·검증·꾸러미 만들기·공개."""

import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from psycopg.types.json import Jsonb

from mapdata.routing import MODE_SPEC, MapIndex

SCHEMA_VERSION = 1
CAMPUS = {"code": "skuniv", "name": "서경대학교", "crs": 5186}
EDGE_KINDS = {"walkway", "sidewalk", "crossing", "corridor", "stairs", "ramp", "elevator", "vehicle_road"}
VERTICAL = {"stairs", "elevator", "ramp"}


class PublishError(Exception):
    pass


def data_dir() -> Path:
    p = Path(os.environ.get("MAP_DATA_DIR", "data/releases"))
    if not p.is_absolute():
        p = Path(__file__).resolve().parent.parent / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_campus(conn) -> int:
    row = conn.execute("SELECT id FROM campus WHERE code = %s", (CAMPUS["code"],)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO campus (code, name, crs) VALUES (%s, %s, %s) RETURNING id",
        (CAMPUS["code"], CAMPUS["name"], CAMPUS["crs"]),
    ).fetchone()["id"]


def create_draft(conn, content: dict, report: dict, source: dict) -> int:
    """content(transform.build 결과)를 새 draft 릴리스로 넣는다. 한 트랜잭션."""
    with conn.transaction():
        campus_id = ensure_campus(conn)
        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM map_release WHERE campus_id = %s", (campus_id,)
        ).fetchone()["v"]
        rid = conn.execute(
            "INSERT INTO map_release (campus_id, version, schema_version, source) VALUES (%s, %s, %s, %s) RETURNING id",
            (campus_id, version, SCHEMA_VERSION, Jsonb({**source, "importReport": report})),
        ).fetchone()["id"]
        bid, fid, nid, aid = {}, {}, {}, {}
        for b in content["buildings"]:
            bid[b["key"]] = conn.execute(
                "INSERT INTO building (release_id, key, name, aliases, ground_floors, underground_floors, center_x, center_y, footprint)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    rid,
                    b["key"],
                    b["name"],
                    b["aliases"],
                    b.get("_ground"),
                    b.get("_under"),
                    b["center"][0] if b["center"] else None,
                    b["center"][1] if b["center"] else None,
                    Jsonb(b["footprint"]) if b["footprint"] else None,
                ),
            ).fetchone()["id"]
        for f in content["floors"]:
            fid[f["key"]] = conn.execute(
                "INSERT INTO floor (release_id, building_id, key, code, sort_order, floor_z_m, z_estimated)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (rid, bid[f["buildingKey"]], f["key"], f["code"], f["sortOrder"], f["floorZ"], f["zEstimated"]),
            ).fetchone()["id"]
        for n in content["nodes"]:
            nid[n["key"]] = conn.execute(
                "INSERT INTO node (release_id, key, location, building_id, floor_id, kind, x, y, z, estimated, indicators)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    rid,
                    n["key"],
                    n["location"],
                    bid.get(n["buildingKey"]),
                    fid.get(n["floorKey"]),
                    n["kind"],
                    n["x"],
                    n["y"],
                    n["z"],
                    n["estimated"],
                    _json(n.get("indicators")),
                ),
            ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO edge (release_id, key, from_node_id, to_node_id, name, kind, direction, pedestrian_access,"
                " wheelchair_access, length_m, step_count, width_m, geometry, estimated, indicators)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        rid,
                        e["key"],
                        nid[e["from"]],
                        nid[e["to"]],
                        e["name"],
                        e["kind"],
                        e["direction"],
                        e["pedestrianAccess"],
                        e["wheelchairAccess"],
                        e["lengthM"],
                        e["stepCount"],
                        e["widthM"],
                        Jsonb(e["geometry"]),
                        e["estimated"],
                        _json(e.get("indicators")),
                    )
                    for e in content["edges"]
                ],
            )
            cur.executemany(
                "INSERT INTO place (release_id, key, location, building_id, floor_id, node_id, name, aliases, category, x, y, z)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [
                    (
                        rid,
                        p["key"],
                        p["location"],
                        bid.get(p["buildingKey"]),
                        fid.get(p["floorKey"]),
                        nid.get(p["nodeKey"]),
                        p["name"],
                        p["aliases"],
                        p["category"],
                        p["x"],
                        p["y"],
                        p["z"],
                    )
                    for p in content["places"]
                ],
            )
        for a in content["walkAreas"]:
            aid[a["key"]] = conn.execute(
                "INSERT INTO walk_area (release_id, key, location, floor_id, name, kind, polygon, z_m, estimated)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    rid,
                    a["key"],
                    a["location"],
                    fid.get(a["floorKey"]),
                    a["name"],
                    a["kind"],
                    Jsonb(a["polygon"]),
                    a["z"],
                    a["estimated"],
                ),
            ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO walk_area_node (area_id, node_id) VALUES (%s, %s)",
                [(aid[w["areaKey"]], nid[w["nodeKey"]]) for w in content["walkAreaNodes"]],
            )
        sid = {}
        for s in content.get("walkSurfaces", []):
            sid[s["key"]] = conn.execute(
                "INSERT INTO walk_surface (release_id, key, name, material, top, polygons, platforms, estimated)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (
                    rid,
                    s["key"],
                    s["name"],
                    s["material"],
                    Jsonb(s["top"]),
                    Jsonb(s["polygons"]),
                    Jsonb(s["platforms"]),
                    s["estimated"],
                ),
            ).fetchone()["id"]
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO walk_surface_node (surface_id, node_id) VALUES (%s, %s)",
                [(sid[w["surfaceKey"]], nid[w["nodeKey"]]) for w in content.get("walkSurfaceNodes", [])],
            )
    return rid


def _json(v):
    return None if v is None else Jsonb(v)


# ---------- DB → 꾸러미 ----------


def _f(v):
    return None if v is None else float(v)


def build_package(conn, rid: int) -> dict:
    rel = conn.execute("SELECT * FROM map_release WHERE id = %s", (rid,)).fetchone()
    q = lambda sql: conn.execute(sql, (rid,)).fetchall()  # noqa: E731
    bk = {r["id"]: r["key"] for r in q("SELECT id, key FROM building WHERE release_id = %s")}
    fk = {r["id"]: r["key"] for r in q("SELECT id, key FROM floor WHERE release_id = %s")}
    nk = {r["id"]: r["key"] for r in q("SELECT id, key FROM node WHERE release_id = %s")}
    ak = {r["id"]: r["key"] for r in q("SELECT id, key FROM walk_area WHERE release_id = %s")}
    sk = {r["id"]: r["key"] for r in q("SELECT id, key FROM walk_surface WHERE release_id = %s")}
    src = rel["source"] or {}
    pkg = {
        "schemaVersion": rel["schema_version"],
        "release": {
            "version": rel["version"],
            "createdAt": rel["created_at"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": src.get("label", "campus-tracker"),
        },
        "campus": dict(CAMPUS),
        "buildings": [
            {
                "key": r["key"],
                "name": r["name"],
                "aliases": list(r["aliases"]),
                "center": [_f(r["center_x"]), _f(r["center_y"])] if r["center_x"] is not None else None,
                "footprint": r["footprint"],
            }
            for r in q("SELECT * FROM building WHERE release_id = %s ORDER BY key")
        ],
        "floors": [
            {
                "key": r["key"],
                "buildingKey": bk[r["building_id"]],
                "code": r["code"],
                "sortOrder": r["sort_order"],
                "floorZ": _f(r["floor_z_m"]),
                "zEstimated": r["z_estimated"],
            }
            for r in q("SELECT * FROM floor WHERE release_id = %s ORDER BY key")
        ],
        "nodes": [
            {
                "key": r["key"],
                "location": r["location"],
                "buildingKey": bk.get(r["building_id"]),
                "floorKey": fk.get(r["floor_id"]),
                "kind": r["kind"],
                "x": _f(r["x"]),
                "y": _f(r["y"]),
                "z": _f(r["z"]),
                "estimated": r["estimated"],
                **({"indicators": r["indicators"]} if r["indicators"] is not None else {}),
            }
            for r in q("SELECT * FROM node WHERE release_id = %s ORDER BY key")
        ],
        "edges": [
            {
                "key": r["key"],
                "from": nk[r["from_node_id"]],
                "to": nk[r["to_node_id"]],
                "name": r["name"],
                "kind": r["kind"],
                "direction": r["direction"],
                "pedestrianAccess": r["pedestrian_access"],
                "wheelchairAccess": r["wheelchair_access"],
                "lengthM": _f(r["length_m"]),
                "stepCount": r["step_count"],
                "widthM": _f(r["width_m"]),
                "geometry": r["geometry"],
                "estimated": r["estimated"],
                **({"indicators": r["indicators"]} if r["indicators"] is not None else {}),
            }
            for r in q("SELECT * FROM edge WHERE release_id = %s ORDER BY key")
        ],
        "places": [
            {
                "key": r["key"],
                "location": r["location"],
                "buildingKey": bk.get(r["building_id"]),
                "floorKey": fk.get(r["floor_id"]),
                "nodeKey": nk.get(r["node_id"]),
                "name": r["name"],
                "aliases": list(r["aliases"]),
                "category": r["category"],
                "x": _f(r["x"]),
                "y": _f(r["y"]),
                "z": _f(r["z"]),
            }
            for r in q("SELECT * FROM place WHERE release_id = %s ORDER BY key")
        ],
        "walkAreas": [
            {
                "key": r["key"],
                "location": r["location"],
                "floorKey": fk.get(r["floor_id"]),
                "name": r["name"],
                "kind": r["kind"],
                "polygon": r["polygon"],
                "z": _f(r["z_m"]),
                "estimated": r["estimated"],
            }
            for r in q("SELECT * FROM walk_area WHERE release_id = %s ORDER BY key")
        ],
        "walkAreaNodes": sorted(
            (
                {"areaKey": ak[r["area_id"]], "nodeKey": nk[r["node_id"]]}
                for r in q(
                    "SELECT wan.* FROM walk_area_node wan JOIN walk_area a ON a.id = wan.area_id WHERE a.release_id = %s"
                )
            ),
            key=lambda w: (w["areaKey"], w["nodeKey"]),
        ),
        # 기단 면(기울어진 면 포함). 같은 면 위 노드끼리 면 안에 머무는 직선으로 잇는다. 선택 칸.
        "walkSurfaces": [
            {
                "key": r["key"],
                "name": r["name"],
                "material": r["material"],
                "top": r["top"],
                "polygons": r["polygons"],
                "platforms": r["platforms"],
                "estimated": r["estimated"],
            }
            for r in q("SELECT * FROM walk_surface WHERE release_id = %s ORDER BY key")
        ],
        "walkSurfaceNodes": sorted(
            (
                {"surfaceKey": sk[r["surface_id"]], "nodeKey": nk[r["node_id"]]}
                for r in q(
                    "SELECT wsn.* FROM walk_surface_node wsn JOIN walk_surface s ON s.id = wsn.surface_id"
                    " WHERE s.release_id = %s"
                )
            ),
            key=lambda w: (w["surfaceKey"], w["nodeKey"]),
        ),
        # 경로 방식별 비용 규칙(앱이 기기 안에서 같은 규칙으로 길을 찾도록). 선택 칸.
        "routeModes": MODE_SPEC,
    }
    return pkg


def validate(pkg: dict) -> dict:
    """그래프 검증. errors 가 있으면 공개 못 함. warnings 는 보고만."""
    errors, warnings = [], []
    nodes = {n["key"]: n for n in pkg["nodes"]}
    floors = {f["key"]: f for f in pkg["floors"]}
    builds = {b["key"] for b in pkg["buildings"]}
    for name in ("buildings", "floors", "nodes", "edges", "places", "walkAreas", "walkSurfaces"):
        keys = [x["key"] for x in pkg.get(name, [])]
        if len(keys) != len(set(keys)):
            errors.append(f"{name}: key 중복")
    for n in pkg["nodes"]:
        if n["location"] == "outdoor" and (n["buildingKey"] or n["floorKey"]):
            errors.append(f"바깥 노드에 건물·층: {n['key']}")
        if n["location"] == "indoor" and n["buildingKey"] not in builds:
            errors.append(f"실내 노드 건물 없음: {n['key']}")
        if n["floorKey"] and floors[n["floorKey"]]["buildingKey"] != n["buildingKey"]:
            errors.append(f"노드 층이 다른 건물: {n['key']}")
    indoor_no_floor, cross = [], []
    adj = defaultdict(set)
    for e in pkg["edges"]:
        a, b = nodes.get(e["from"]), nodes.get(e["to"])
        if not a or not b:
            errors.append(f"없는 노드를 잇는 길: {e['key']}")
            continue
        if e["from"] == e["to"]:
            errors.append(f"자기 자신을 잇는 길: {e['key']}")
        if not e["lengthM"] or e["lengthM"] <= 0:
            errors.append(f"길이 0: {e['key']}")
        if len(e["geometry"]) < 2:
            errors.append(f"선 점 2개 미만: {e['key']}")
        if e["kind"] not in EDGE_KINDS:
            errors.append(f"길 종류 값: {e['key']} {e['kind']}")
        if (
            (a["location"] == "indoor" or b["location"] == "indoor")
            and e["kind"] not in VERTICAL
            and ((a["location"] == "indoor" and not a["floorKey"]) or (b["location"] == "indoor" and not b["floorKey"]))
        ):
            indoor_no_floor.append(e["key"])
        if (
            a["floorKey"]
            and b["floorKey"]
            and a["floorKey"] != b["floorKey"]
            and a["buildingKey"] == b["buildingKey"]
            and e["kind"] not in VERTICAL
        ):
            cross.append(f"{e['key']} {floors[a['floorKey']]['code']}→{floors[b['floorKey']]['code']} ({e['kind']})")
        adj[e["from"]].add(e["to"])
        adj[e["to"]].add(e["from"])
    skeys = {s["key"] for s in pkg.get("walkSurfaces", [])}
    for w in pkg.get("walkSurfaceNodes", []):
        if w["surfaceKey"] not in skeys or w["nodeKey"] not in nodes:
            errors.append(f"기단 면 노드 짝이 없는 것을 가리킴: {w}")
        elif nodes[w["nodeKey"]]["location"] != "outdoor":
            errors.append(f"기단 면에 실내 노드: {w}")
    for p in pkg["places"]:
        if p["nodeKey"] and p["nodeKey"] not in nodes:
            errors.append(f"장소가 없는 노드에 붙음: {p['key']}")
    # 이어진 덩어리: 열린 면·걷는 면 가로지르기(읽을 때 만드는 가상 간선)도 이음으로 본다
    if not errors:
        for e in MapIndex(pkg).edges.values():
            if e["kind"] == "open_area":
                adj[e["from"]].add(e["to"])
                adj[e["to"]].add(e["from"])
    seen, comps = set(), []
    for k in nodes:
        if k in seen:
            continue
        stack, comp = [k], 0
        seen.add(k)
        while stack:
            cur = stack.pop()
            comp += 1
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(comp)
    comps.sort(reverse=True)
    if len(comps) > 1:
        warnings.append(f"끊긴 덩어리 {len(comps) - 1}개 (가장 큰 것 외 크기: {comps[1:]})")
    if indoor_no_floor:
        warnings.append(f"층 모르는 실내 노드에 닿는 길 {len(indoor_no_floor)}개")
    if cross:
        errors.append(f"계단·승강기·경사로가 아닌데 층이 바뀌는 길 {len(cross)}개: {cross[:10]}")
    unattached = [p["name"] for p in pkg["places"] if not p["nodeKey"]]
    if unattached:
        warnings.append(f"길에 안 붙은 장소 {len(unattached)}개: {unattached}")
    return {"errors": errors, "warnings": warnings, "components": comps}


def encode(pkg: dict) -> bytes:
    return json.dumps(pkg, ensure_ascii=False, separators=(",", ":")).encode("utf8")


def publish(conn, rid: int) -> dict:
    rel = conn.execute("SELECT * FROM map_release WHERE id = %s FOR UPDATE", (rid,)).fetchone()
    if rel is None:
        raise PublishError("릴리스 없음")
    if rel["status"] != "draft":
        raise PublishError(f"draft 만 공개할 수 있음 (지금 {rel['status']})")
    pkg = build_package(conn, rid)
    v = validate(pkg)
    if v["errors"]:
        raise PublishError("검증 오류: " + "; ".join(v["errors"][:5]))
    body = encode(pkg)
    sha = hashlib.sha256(body).hexdigest()
    path = data_dir() / f"{CAMPUS['code']}-v{rel['version']}-{sha[:12]}.json"
    path.write_bytes(body)
    now = datetime.now(timezone.utc)
    with conn.transaction():
        conn.execute(
            "UPDATE map_release SET status = 'published', file_path = %s, size_bytes = %s, sha256 = %s,"
            " published_at = %s, source = source || %s WHERE id = %s",
            (str(path), len(body), sha, now, Jsonb({"validation": v}), rid),
        )
    return {
        "id": rid,
        "version": rel["version"],
        "sha256": sha,
        "sizeBytes": len(body),
        "path": str(path),
        "validation": v,
    }
