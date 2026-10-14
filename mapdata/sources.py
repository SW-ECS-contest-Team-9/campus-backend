"""원천 읽기: campus-tracker 시험 서버 읽기 API 와 pg_dump SQL 내보내기.

둘 다 같은 모양(SourceData dict)으로 바꾼다.
  roads:      [{id, name, road_class, structure, pedestrian_access, direction, wheelchair_access,
                width_m, building_id, level_id, from_node, to_node, coords: [[x,y,z],...]}]
  node_levels: {node_id: level_id}
  places:     [{id, name, category, building_id, level_id, coords: [x,y,z]}]
  buildings:  [{id, name, ground_floors, rings_lonlat: [[[lon,lat],...]], floor_names, floors_m}]
  open_areas: [{id, name, kind, elevation_m, polygon: [[x,y],...]}]
  meta:       {...}
"""
import json
import struct
import urllib.request
from datetime import datetime, timezone

NULL = "\\N"


# ---------- SQL 내보내기 ----------

def read_copy_tables(path: str) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = {}
    cur_name, cols = None, []
    with open(path, encoding="utf8") as f:
        for line in f:
            if line.startswith("COPY "):
                cur_name = line.split()[1]
                cols = line[line.index("(") + 1 : line.index(")")].split(", ")
                tables[cur_name] = []
                continue
            if cur_name is None:
                continue
            if line.startswith("\\."):
                cur_name = None
                continue
            vals = line.rstrip("\n").split("\t")
            tables[cur_name].append({c: (None if v == NULL else _unescape(v)) for c, v in zip(cols, vals)})
    return tables


def _unescape(v: str) -> str:
    if "\\" not in v:
        return v
    return v.replace("\\t", "\t").replace("\\n", "\n").replace("\\r", "\r").replace("\\\\", "\\")


def parse_ewkb(hexstr: str):
    """hex EWKB → (type, 좌표). Point/LineString/Polygon/Multi* 만."""
    b = bytes.fromhex(hexstr)
    geom, _ = _read_geom(b, 0)
    return geom


def _read_geom(b: bytes, o: int):
    endian = "<" if b[o] == 1 else ">"
    o += 1
    (t,) = struct.unpack_from(endian + "I", b, o)
    o += 4
    has_z = bool(t & 0x80000000)
    has_m = bool(t & 0x40000000)
    if t & 0x20000000:
        o += 4  # SRID
    base = t & 0x0FFFFFFF
    dim = 2 + has_z + has_m

    def pts(o):
        (n,) = struct.unpack_from(endian + "I", b, o)
        o += 4
        out = []
        for _ in range(n):
            out.append(list(struct.unpack_from(endian + "d" * dim, b, o))[: 2 + has_z])
            o += 8 * dim
        return out, o

    if base == 1:
        p = list(struct.unpack_from(endian + "d" * dim, b, o))[: 2 + has_z]
        return ("Point", p), o + 8 * dim
    if base == 2:
        p, o = pts(o)
        return ("LineString", p), o
    if base == 3:
        (n,) = struct.unpack_from(endian + "I", b, o)
        o += 4
        rings = []
        for _ in range(n):
            r, o = pts(o)
            rings.append(r)
        return ("Polygon", rings), o
    if base in (4, 5, 6):
        (n,) = struct.unpack_from(endian + "I", b, o)
        o += 4
        parts = []
        for _ in range(n):
            g, o = _read_geom(b, o)
            parts.append(g[1])
        return ("Multi" + {4: "Point", 5: "LineString", 6: "Polygon"}[base], parts), o
    raise ValueError(f"지원하지 않는 도형 {base}")


def _outer_ring(geom) -> list:
    t, c = geom
    if t == "Polygon":
        return c[0]
    if t == "MultiPolygon":
        return max((p[0] for p in c), key=len)
    raise ValueError(t)


def load_export(path: str) -> dict:
    """SQL 내보내기에서 길·노드 층·장소·면. 건물은 없음(빈 목록)."""
    t = read_copy_tables(path)
    roads = []
    for r in t.get("mobility.road_segments", []):
        if (r.get("status") or "").upper() in ("REPLACED", "RETIRED"):
            continue
        _, coords = parse_ewkb(r["geom"])
        roads.append({
            "id": r["id"], "name": r["name"], "road_class": r["road_class"], "structure": r["structure"],
            "pedestrian_access": r["pedestrian_access"], "direction": r["pedestrian_direction"],
            "wheelchair_access": r["wheelchair_access"],
            "width_m": float(r["width_m"]) if r["width_m"] else None,
            "building_id": r["building_id"], "level_id": r["level_id"],
            "from_node": r["from_node_id"], "to_node": r["to_node_id"], "coords": coords,
        })
    places = []
    for p in t.get("mobility.places", []):
        if (p.get("status") or "").upper() in ("REPLACED", "RETIRED"):
            continue
        _, c = parse_ewkb(p["geom"])
        places.append({"id": p["id"], "name": p["name"], "category": p["category"],
                       "building_id": p["building_id"], "level_id": p["level_id"], "coords": c})
    areas = []
    for a in t.get("mobility.open_areas", []):
        ring = _outer_ring(parse_ewkb(a["geom"]))
        areas.append({"id": a["id"], "name": a["name"], "kind": a["kind"],
                      "elevation_m": float(a["elevation_m"]) if a["elevation_m"] else None,
                      "polygon": [[p[0], p[1]] for p in ring]})
    node_levels = {n["id"]: n["level_id"] for n in t.get("mobility.network_nodes", []) if n["level_id"]}
    return {"roads": roads, "places": places, "buildings": [], "open_areas": areas,
            "node_levels": node_levels, "meta": {"export": path}}


# ---------- 시험 서버 읽기 API ----------

def _get(url: str):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read().decode("utf8"))


def load_api(base: str, export_path: str | None = None) -> dict:
    """시험 서버 API 에서 길·승강기·장소·건물. 내보내기가 있으면 API 에 없는 칸
    (보행 통행·방향·휠체어, 노드 층 표시, 열린 면)을 같은 id 로 채운다."""
    base = base.rstrip("/")
    roads_raw = _get(f"{base}/api/v1/mobility/roads")["roads"] + _get(f"{base}/api/v1/mobility/elevators")["roads"]
    places_raw = _get(f"{base}/api/v1/mobility/places")["places"]
    scene = _get(f"{base}/api/v1/scene")

    ex = load_export(export_path) if export_path else None
    ex_roads = {r["id"]: r for r in ex["roads"]} if ex else {}
    enriched = 0
    roads = []
    seen = set()
    for r in roads_raw:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        e = ex_roads.get(r["id"])
        enriched += e is not None
        roads.append({
            "id": r["id"], "name": r["name"], "road_class": r["roadClass"], "structure": r["structure"],
            "pedestrian_access": e["pedestrian_access"] if e else None,
            "direction": e["direction"] if e else None,
            "wheelchair_access": e["wheelchair_access"] if e else None,
            "width_m": r["widthM"], "building_id": r["buildingId"], "level_id": r["levelId"],
            "from_node": r["fromNodeId"], "to_node": r["toNodeId"], "coords": r["geometry"]["coordinates"],
        })
    places = [{"id": p["id"], "name": p["name"], "category": p["category"], "building_id": p["buildingId"],
               "level_id": p["levelId"], "coords": p["geometry"]["coordinates"]} for p in places_raw]
    buildings = []
    for b in scene["buildings"]:
        g = b["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        win = (b.get("appearance") or {}).get("windows") or {}
        buildings.append({"id": b["buildingId"], "name": b.get("name"), "ground_floors": b.get("groundFloors"),
                          "rings_lonlat": [p[0] for p in polys],
                          "floor_names": win.get("floorNames"), "floors_m": win.get("floorsM")})
    return {
        "roads": roads, "places": places, "buildings": buildings,
        "open_areas": ex["open_areas"] if ex else [],
        "node_levels": ex["node_levels"] if ex else {},
        "meta": {"api": base, "scene": scene.get("id"), "mapVersionId": scene.get("mapVersionId"),
                 "export": export_path, "exportMatchedRoads": enriched,
                 "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    }
