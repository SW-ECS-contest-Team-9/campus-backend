"""원천(SourceData) → 릴리스 내용(꾸러미 v1 과 같은 key·모양) + 보고서.

자료 보정은 모두 여기서 하고, 고친 것·못 고친 것을 report 에 남긴다.
"""

import math
import re
from collections import Counter, defaultdict

FLOOR_TOL_M = 1.5  # 높이로 층을 정할 때 허용 차
PLACE_DZ_M = 2.0  # 장소-노드 높이 차 한도
PLACE_RADIUS_M = 25.0  # 장소-노드 평면 거리 한도
COORD_TOL_M = 0.05
AREA_DZ_M = 0.5  # 열린 면 높이와 노드 높이 차 한도: 넘으면 다른 단(사이는 벽)
AREA_EDGE_TOL_M = 0.5  # 면 경계 밖으로 이만큼까지는 면 위 노드로 본다

KIND_BY_STRUCTURE = {
    "indoor_corridor": "corridor",
    "stairs": "stairs",
    "elevator": "elevator",
    "ramp": "ramp",
    "sidewalk": "sidewalk",
    "crossing": "crossing",
}
VERTICAL_KINDS = {"stairs", "elevator", "ramp"}
CATEGORY = {
    "building_entrance": "entrance",
    "entrance": "entrance",
    "facility": "facility",
    "landmark": "landmark",
    "parking": "parking",
    "bus_stop": "bus_stop",
    "room": "room",
}
AREA_KIND = {"plaza": "plaza", "parking": "parking_aisle", "parking_aisle": "parking_aisle", "field": "field"}


def floor_code(name: str) -> str | None:
    n = name.strip().upper()
    m = re.fullmatch(r"B(\d+)", n) or re.fullmatch(r"지하\s*(\d+)\s*층", name.strip())
    if m:
        return f"B{int(m.group(1))}"
    m = re.fullmatch(r"(\d+)\s*(층|F)", n)
    if m:
        return f"{int(m.group(1))}F"
    return None


def sort_order(code: str) -> int:
    return -int(code[1:]) if code.startswith("B") else int(code[:-1])


def parse_level(level_id: str | None) -> tuple[str | None, float | None]:
    """'북악관_5F_추정' → ('5F', None), '북악관_Z151.79_추정' → (None, 151.79)."""
    if not level_id:
        return None, None
    m = re.search(r"_Z(-?\d+(?:\.\d+)?)", level_id)
    if m:
        return None, float(m.group(1))
    m = re.search(r"_(B\d+|\d+F)(?:_|$)", level_id)
    if m:
        return m.group(1), None
    return None, None


def canon_building(bid: str | None, scene_names: dict[str, str]) -> str | None:
    if not bid:
        return None
    if bid in scene_names:
        return scene_names[bid]
    return bid.split("#")[0].removeprefix("추가-")


def poly_len(coords) -> float:
    return sum(math.dist(a, b) for a, b in zip(coords, coords[1:]))


def ring_area(ring) -> float:
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2


def point_in_poly(x, y, poly) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def dist_to_ring(x, y, poly) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def _to_5186():
    from pyproj import Transformer

    return Transformer.from_crs(4326, 5186, always_xy=True)


def build(src: dict) -> tuple[dict, dict]:
    """반환: (content, report). content 는 꾸러미의 buildings..walkAreaNodes 목록."""
    rep = {"fixed": defaultdict(list), "unfixed": defaultdict(list), "counts": {}}
    fix = lambda k, v: rep["fixed"][k].append(v)  # noqa: E731
    unf = lambda k, v: rep["unfixed"][k].append(v)  # noqa: E731

    # ---- 건물·층 ----
    scene_names = {}
    names = {b.get("name") or b["id"] for b in src["buildings"]}
    for b in src["buildings"]:
        bid = b["id"]
        if "#" in bid:
            base = bid.split("#")[0]
        elif bid.startswith(
            "추가-"
        ):  # 덧붙인 부분: 이름 첫 낱말이 다른 건물 이름이면 그 건물 (상승관 동쪽 끝 → 상승관)
            name = b.get("name") or bid.removeprefix("추가-")
            first = name.split()[0]
            base = first if first != name and first in names else name
        else:
            base = bid
        scene_names[bid] = base
    groups = defaultdict(list)
    for b in src["buildings"]:
        groups[scene_names[b["id"]]].append(b)
    tr = _to_5186() if groups else None
    buildings, floors = [], []
    floor_by_bld: dict[str, list[dict]] = defaultdict(list)
    for name, parts in sorted(groups.items()):
        main = next((p for p in parts if scene_names[p["id"]] == p["id"] or p.get("name") == name), parts[0])
        rings = []
        for p in parts:
            for ring in p["rings_lonlat"]:
                xs, ys = tr.transform([c[0] for c in ring], [c[1] for c in ring])
                pts = [[round(x, 2), round(y, 2)] for x, y in zip(xs, ys)]
                if len(pts) > 1 and pts[0] == pts[-1]:
                    pts = pts[:-1]
                rings.append(pts)
        foot = max(rings, key=ring_area) if rings else None
        center = (
            [round(sum(p[0] for p in foot) / len(foot), 2), round(sum(p[1] for p in foot) / len(foot), 2)]
            if foot
            else None
        )
        key = f"b:{name}"
        aliases = sorted({p["name"] for p in parts if p.get("name") and p["name"] != name})
        withnames = next((p for p in [main] + parts if p.get("floor_names") and p.get("floors_m")), None)
        codes = []
        if withnames:
            for fname, z in zip(withnames["floor_names"], withnames["floors_m"]):
                code = floor_code(fname)
                if not code or code in codes:
                    unf("floor_name_unparsed", f"{name}: {fname}")
                    continue
                codes.append(code)
                f = {
                    "key": f"f:{name}:{code}",
                    "buildingKey": key,
                    "code": code,
                    "sortOrder": sort_order(code),
                    "floorZ": round(float(z), 2) if z is not None else None,
                    "zEstimated": True,
                }
                floors.append(f)
                floor_by_bld[key].append(f)
        elif any(p.get("floors_m") for p in parts):
            unf("floors_without_names", f"{name}: 층 높이만 있고 층 이름 없음 → 층 행 안 만듦")
        else:
            unf("building_without_floors", name)
        under = sum(c.startswith("B") for c in codes)
        buildings.append(
            {
                "key": key,
                "name": name,
                "aliases": aliases,
                "center": center,
                "footprint": foot,
                "_ground": main.get("ground_floors") or (len(codes) - under if codes else None),
                "_under": under if codes else None,
            }
        )
    bld_keys = {b["key"] for b in buildings}

    def bkey(raw):
        n = canon_building(raw, scene_names)
        return f"b:{n}" if n else None

    def nearest_floor(bk, z, tol=FLOOR_TOL_M):
        cands = [f for f in floor_by_bld.get(bk, []) if f["floorZ"] is not None]
        if z is None or not cands:
            return None
        f = min(cands, key=lambda f: abs(f["floorZ"] - z))
        return f if abs(f["floorZ"] - z) <= tol else None

    # ---- 길 → 노드·간선 ----
    node_xyz: dict[str, list[float]] = {}
    incident: dict[str, list[dict]] = defaultdict(list)
    edges = []
    pair_kind = set()
    for r in sorted(src["roads"], key=lambda r: r["id"]):
        coords = [[round(float(c[0]), 2), round(float(c[1]), 2), round(float(c[2]), 2)] for c in r["coords"]]
        if r["from_node"] == r["to_node"]:
            fix("self_loop_dropped", r["id"])
            continue
        length = poly_len(coords)
        if length < 0.01 or len(coords) < 2:
            fix("zero_length_dropped", r["id"])
            continue
        if r["road_class"] in ("vehicle", "shared") and r["structure"] in ("ordinary", "ramp", None):
            kind = "vehicle_road"
        else:
            kind = KIND_BY_STRUCTURE.get(r["structure"], "walkway")
        key_pair = (min(r["from_node"], r["to_node"]), max(r["from_node"], r["to_node"]), kind)
        if key_pair in pair_kind:
            fix("duplicate_pair_kind_dropped", r["id"])
            continue
        pair_kind.add(key_pair)
        for nid, c in ((r["from_node"], coords[0]), (r["to_node"], coords[-1])):
            if nid in node_xyz:
                if math.dist(node_xyz[nid], c) > COORD_TOL_M:
                    unf("node_coord_mismatch", f"{nid}: {math.dist(node_xyz[nid], c):.2f} m")
            else:
                node_xyz[nid] = c
        acc = r.get("pedestrian_access")
        if acc is None:
            acc = "unknown" if r["road_class"] == "vehicle" else "allowed"
        if acc == "restricted":
            fix("access_restricted_to_unknown", r["id"])
            acc = "unknown"
        direction = r.get("direction") or "both"
        if direction == "unknown":
            fix("direction_unknown_to_both", r["id"])
            direction = "both"
        wc = r.get("wheelchair_access") or "unknown"
        if wc == "restricted":
            wc = "unknown"
        steps = None
        m = re.search(r"(\d+)\s*칸", r["name"] or "")
        if kind == "stairs" and m:
            steps = int(m.group(1))
        e = {
            "key": f"e:{r['id']}",
            "from": f"n:{r['from_node']}",
            "to": f"n:{r['to_node']}",
            "name": r["name"],
            "kind": kind,
            "direction": direction,
            "pedestrianAccess": acc,
            "wheelchairAccess": wc,
            "lengthM": round(length, 2),
            "stepCount": steps,
            "widthM": float(r["width_m"]) if r.get("width_m") else None,
            "geometry": coords,
            "estimated": "추정" in (r["name"] or "") or "추정" in (r.get("level_id") or ""),
            "_building": bkey(r["building_id"]),
            "_level": r.get("level_id"),
        }
        edges.append(e)
        incident[r["from_node"]].append(e)
        incident[r["to_node"]].append(e)

    nodes = []
    node_by_key = {}
    for nid in sorted(node_xyz):
        x, y, z = node_xyz[nid]
        inc = incident[nid]
        blds = [e["_building"] for e in inc]
        indoor = all(blds)
        bk = Counter(blds).most_common(1)[0][0] if indoor else None
        if bk and bk not in bld_keys:
            unf("node_building_missing_in_scene", f"{nid}: {bk}")
            buildings.append(
                {
                    "key": bk,
                    "name": bk[2:],
                    "aliases": [],
                    "center": None,
                    "footprint": None,
                    "_ground": None,
                    "_under": None,
                }
            )
            bld_keys.add(bk)
        kinds = {e["kind"] for e in inc}
        if "elevator" in kinds:
            nkind = "elevator_stop"
        elif "stairs" in kinds:
            nkind = "stair_end"
        elif any(blds) and not all(blds):
            nkind = "door"
        elif len(inc) == 1:
            nkind = "end"
        else:
            nkind = "junction"
        fl = None
        if indoor:
            hint = src["node_levels"].get(nid)
            if not hint:
                hints = {e["_level"] for e in inc if e["_level"] and e["kind"] not in VERTICAL_KINDS}
                hint = hints.pop() if len(hints) == 1 else None
            code, hz = parse_level(hint)
            if code:
                fl = next((f for f in floor_by_bld.get(bk, []) if f["code"] == code), None)
                if fl and fl["floorZ"] is not None and abs(fl["floorZ"] - z) > FLOOR_TOL_M:
                    fix("level_code_height_mismatch", f"{nid}: {hint} 인데 z {z} → 높이로 다시 정함")
                    fl = nearest_floor(bk, z)
            elif hz is not None:
                # 높이형 층 표시(계단참 높이 등)는 노드 자체 높이보다 약하다: 노드 z 로 먼저, 안 되면 표시 높이로.
                fl = nearest_floor(bk, z) or nearest_floor(bk, hz)
                by_hint = nearest_floor(bk, hz)
                note = (
                    ""
                    if (by_hint and fl and by_hint["key"] == fl["key"])
                    else f" (표시 높이로는 {by_hint['code'] if by_hint else '층 없음'})"
                )
                fix("level_height_id", f"{nid}: {hint}, z {z} → {fl['code'] if fl else '층 모름'}{note}")
            else:
                fl = nearest_floor(bk, z)
            if fl is None:
                unf("indoor_node_floor_unknown", f"{nid} ({bk[2:]}, z {z})")
        n = {
            "key": f"n:{nid}",
            "location": "indoor" if indoor else "outdoor",
            "buildingKey": bk,
            "floorKey": fl["key"] if fl else None,
            "kind": nkind,
            "x": x,
            "y": y,
            "z": z,
            "estimated": any(e["estimated"] for e in inc),
        }
        nodes.append(n)
        node_by_key[n["key"]] = n

    # ---- 장소 ----
    places = []
    for p in sorted(src["places"], key=lambda p: p["id"]):
        x, y, z = (round(float(v), 2) for v in p["coords"][:3])
        bk = bkey(p["building_id"])
        if bk and bk not in bld_keys:
            unf("place_building_missing", f"{p['name']}: {bk}")
            bk = None
        code, hz = parse_level(p.get("level_id"))
        fl = None
        if bk:
            fl = next((f for f in floor_by_bld.get(bk, []) if f["code"] == code), None) if code else None
            fl = fl or nearest_floor(bk, hz if hz is not None else z)
        cat = CATEGORY.get(p["category"], "other")
        place = {
            "key": f"p:{p['id']}",
            "location": "indoor" if bk else "outdoor",
            "buildingKey": bk,
            "floorKey": fl["key"] if fl else None,
            "nodeKey": None,
            "name": p["name"],
            "aliases": [],
            "category": cat,
            "x": x,
            "y": y,
            "z": z,
        }

        def eligible(n):
            if abs(n["z"] - z) > PLACE_DZ_M or math.hypot(n["x"] - x, n["y"] - y) > PLACE_RADIUS_M:
                return False
            if n["location"] == "outdoor":
                return True
            if place["location"] == "outdoor" or n["buildingKey"] != bk:
                return False
            return n["floorKey"] is None or place["floorKey"] is None or n["floorKey"] == place["floorKey"]

        cands = [n for n in nodes if eligible(n)]
        if cands:
            best = min(cands, key=lambda n: math.dist((n["x"], n["y"], n["z"]), (x, y, z)))
            place["nodeKey"] = best["key"]
        else:
            unf("place_unattached", f"{p['name']}")
        naive = min(nodes, key=lambda n: math.hypot(n["x"] - x, n["y"] - y), default=None)
        if naive and naive["key"] != place["nodeKey"] and abs(naive["z"] - z) > PLACE_DZ_M:
            fix(
                "place_attach_height_corrected",
                f"{p['name']}: 평면 최근접 {naive['floorKey'] or naive['location']} (dz {naive['z'] - z:+.1f} m) 대신 "
                f"{node_by_key[place['nodeKey']]['floorKey'] or node_by_key[place['nodeKey']]['location'] if place['nodeKey'] else '안 붙임'}",
            )
        places.append(place)

    # ---- 열린 면 ----
    areas, area_nodes = [], []
    for a in sorted(src["open_areas"], key=lambda a: a["id"]):
        poly = [[round(p[0], 2), round(p[1], 2)] for p in a["polygon"]]
        if len(poly) > 1 and poly[0] == poly[-1]:
            poly = poly[:-1]
        kind = AREA_KIND.get(a["kind"]) or ("field" if "운동장" in (a["name"] or "") else "other")
        ak = f"a:{a['id']}"
        areas.append(
            {
                "key": ak,
                "location": "outdoor",
                "floorKey": None,
                "name": a["name"] or "",
                "kind": kind,
                "polygon": poly,
                "z": a["elevation_m"],
                "estimated": "추정" in (a["name"] or ""),
            }
        )
        for n in nodes:
            if (
                n["location"] == "outdoor"
                and (point_in_poly(n["x"], n["y"], poly) or dist_to_ring(n["x"], n["y"], poly) <= AREA_EDGE_TOL_M)
                and (a["elevation_m"] is None or abs(n["z"] - a["elevation_m"]) <= AREA_DZ_M)
            ):
                area_nodes.append({"areaKey": ak, "nodeKey": n["key"]})

    content = {
        "buildings": buildings,
        "floors": floors,
        "nodes": nodes,
        "edges": edges,
        "places": places,
        "walkAreas": areas,
        "walkAreaNodes": area_nodes,
    }
    rep["counts"] = {k: len(v) for k, v in content.items()}
    rep["fixed"] = dict(rep["fixed"])
    rep["unfixed"] = dict(rep["unfixed"])
    return content, rep
