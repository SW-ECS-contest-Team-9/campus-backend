"""걷는 면: 트래커 기단 덩어리(구조물 platform, 평면 또는 한 방향 기울기)를 사람이 다니는 곳 외곽으로 자른 것.

가져올 때(transform.build) 만들어 꾸러미 walkSurfaces·walkSurfaceNodes 로 내보내고, 경로 계산은 읽을 때
같은 면 위 노드끼리 면 안에 머무는 직선으로 잇는다(routing.MapIndex). 앱도 같은 자료로 같은 일을 한다.

면 하나 = 윗면이 같은 기단(같은 topM 또는 같은 topProfile)을 합친 것
          ∩ 사람이 다니는 곳 외곽(사용자 그림, walkable_boundary)
          − 건물 발자국 − 면 위로 솟은 구조물(벽·화단·턱·계단 단·나무 줄기 등)
  · 화단(planting)·암석(rock) 기단은 걷는 면이 아니다.
  · 다른 높이 면 사이는 벽이라 면끼리 잇지 않는다(면마다 따로).
  · 노드는 면 높이와 AREA_DZ_M 안, 면에서 SNAP_M 안이면 그 면 위 노드. 면 밖에 조금 걸친 노드(문 앞 등)는
    노드 둘레 작은 원을 면에 붙여 직선이 노드에서 시작할 수 있게 한다.
"""

import json
from pathlib import Path

from shapely import STRtree, get_coordinates
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from mapdata.indicators import HEADROOM_M

AREA_DZ_M = 0.5  # 노드 높이와 면 높이 차 한도(넘으면 다른 단)
SNAP_M = 0.5  # 면 경계 밖 이만큼까지는 면 위 노드
STEP_OK_M = 0.2  # 면 위로 이보다 낮게 솟은 것(턱·경계석)은 넘어간다
MIN_PART_M2 = 1.0  # 이보다 작은 조각은 버린다
NOT_WALKABLE = {"planting", "rock"}
ZONE_NAME_SHARE = 0.3  # 면 넓이의 이만큼 이상이 빗금 묶음 안이면 그 이름을 붙인다


def surface_z(top: dict, x: float, y: float) -> float:
    """면 높이. top = {"z": 높이} 또는 {"profile": {origin, direction, points: [[거리, 높이], ...]}}.
    profile 은 origin 에서 direction 쪽으로 잰 거리마다의 높이(사이는 직선, 끝 밖은 끝 높이)."""
    if "z" in top:
        return float(top["z"])
    p = top["profile"]
    (ox, oy), (dx, dy) = p["origin"], p["direction"]
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    d = ((x - ox) * dx + (y - oy) * dy) / n
    pts = p["points"]
    if d <= pts[0][0]:
        return float(pts[0][1])
    for (d0, h0), (d1, h1) in zip(pts, pts[1:]):
        if d <= d1:
            return h0 + (h1 - h0) * (d - d0) / (d1 - d0) if d1 > d0 else float(h1)
    return float(pts[-1][1])


def load_walkable(path: str) -> dict:
    """사람이 다니는 곳 GeoJSON(5186) → {"outline": [고리, ...], "zones": [{name, polygon}]}."""
    d = json.loads(Path(path).read_text(encoding="utf8"))
    out = {"outline": [], "zones": [], "file": Path(path).name}
    for f in d["features"]:
        g, role = f["geometry"], f["properties"].get("role")
        if g["type"] != "Polygon":
            continue
        if role == "walkable_boundary":
            out["outline"].append(g["coordinates"][0])
        elif role == "hatched_zone":
            out["zones"].append({"name": f["properties"].get("name") or "", "polygon": g["coordinates"][0]})
    return out


def _poly(ring) -> Polygon:
    p = Polygon([(float(c[0]), float(c[1])) for c in ring])
    return p if p.is_valid else p.buffer(0)


def _top_of(p: dict) -> dict | None:
    if p.get("topM") is not None:
        return {"z": round(float(p["topM"]), 2)}
    prof = p.get("topProfile")
    if prof and prof.get("points"):
        return {
            "profile": {
                "origin": [float(v) for v in prof["origin"]],
                "direction": [float(v) for v in prof["direction"]],
                "points": [[float(a), float(b)] for a, b in prof["points"]],
            }
        }
    return None


def _parts(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [x for x in getattr(g, "geoms", []) if isinstance(x, Polygon)]


def _rings_out(g) -> list:
    """shapely 면 → [[바깥 고리, 구멍 고리...], ...] (고리는 닫지 않은 [x, y] 목록, 소수 둘째 자리)."""
    out = []
    for p in _parts(g):
        if p.area < MIN_PART_M2:
            continue
        rings = [p.exterior] + list(p.interiors)
        out.append([[[round(x, 2), round(y, 2)] for x, y in list(r.coords)[:-1]] for r in rings])
    return out


def build(platforms, solids, outline, building_rings, zones, nodes) -> tuple[list, list, dict]:
    """반환: (walkSurfaces, walkSurfaceNodes, 보고). nodes 는 꾸러미 노드(바깥 노드만 씀)."""
    rep = {"platforms": len(platforms), "skippedMaterial": [], "skippedNoTop": [], "obstaclesCut": 0}
    walk = unary_union([_poly(r) for r in outline]) if outline else None
    blds = unary_union([_poly(r) for r in building_rings]) if building_rings else None
    groups: dict[str, dict] = {}
    for p in platforms:
        if (p.get("material") or "") in NOT_WALKABLE:
            rep["skippedMaterial"].append(p["id"])
            continue
        top = _top_of(p)
        if top is None:
            rep["skippedNoTop"].append(p["id"])
            continue
        sig = json.dumps(top, sort_keys=True)
        g = groups.setdefault(sig, {"top": top, "ids": [], "polys": [], "material": p.get("material")})
        g["ids"].append(p["id"])
        g["polys"].append(_poly(p["ring"]))
    solid_geoms = [_poly(s["ring"]) for s in solids]
    tree = STRtree(solid_geoms) if solid_geoms else None
    zone_polys = [(z["name"], _poly(z["polygon"])) for z in zones]
    outdoor = [n for n in nodes if n["location"] == "outdoor"]
    surfaces, links = [], []
    for g in sorted(groups.values(), key=lambda g: sorted(g["ids"])[0]):
        geom = unary_union(g["polys"])
        if walk is not None:
            geom = geom.intersection(walk)
        if blds is not None and not geom.is_empty:
            geom = geom.difference(blds)
        if geom.is_empty or geom.area < MIN_PART_M2:
            continue
        top = g["top"]
        cut = []
        if tree is not None:
            for i in tree.query(geom):
                s, sg = solids[i], solid_geoms[i]
                if s.get("fromM") is None or s.get("toM") is None or not sg.intersects(geom):
                    continue
                hs = [surface_z(top, x, y) for x, y in get_coordinates(sg)]
                # 윗면이 그 밑 면의 가장 높은 곳보다 STEP_OK_M 넘게 솟고, 밑면이 머리 높이 아래면 막힘.
                # (기운 면 위 평평한 덧판·포장 조각은 윗면이 오르막 쪽 면 높이 정도라 막지 않는다)
                if float(s["toM"]) > max(hs) + STEP_OK_M and float(s["fromM"]) < max(hs) + HEADROOM_M:
                    cut.append(sg)
        if cut:
            geom = geom.difference(unary_union(cut))
            rep["obstaclesCut"] += len(cut)
        # 면 위 노드
        mine = []
        for n in outdoor:
            pt = Point(n["x"], n["y"])
            d = geom.distance(pt)
            if d > SNAP_M or abs(n["z"] - surface_z(top, n["x"], n["y"])) > AREA_DZ_M:
                continue
            if d > 1e-6:  # 면 밖에 조금 걸친 노드(문 앞 등): 노드 둘레 원(반지름 = 면까지 거리 + 0.1 m)을 면에 붙인다
                geom = geom.union(pt.buffer(d + 0.1))
            mine.append(n["key"])
        rings = _rings_out(geom)
        if not rings:
            continue
        key = f"s:{sorted(g['ids'])[0]}"
        name = ""
        for zn, zp in zone_polys:
            if geom.intersection(zp).area >= ZONE_NAME_SHARE * geom.area:
                name = zn
                break
        surfaces.append(
            {
                "key": key,
                "name": name,
                "material": g["material"],
                "top": top,
                "polygons": rings,
                "platforms": sorted(g["ids"]),
                "estimated": True,
            }
        )
        links += [{"surfaceKey": key, "nodeKey": k} for k in sorted(mine)]
    rep["surfaces"] = len(surfaces)
    rep["surfaceNodes"] = len(links)
    rep["sloped"] = sum("profile" in s["top"] for s in surfaces)
    return surfaces, links, rep
