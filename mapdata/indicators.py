"""경로 지표: 꾸러미 내용(content)의 길·노드마다 경로 계산에 쓸 값을 붙인다.

가져올 때(transform.build 끝) 한 번 돈다. 자료로 끌어낼 수 있는 것만 채우고, 현장·학교 자료가 있어야 하는 것은
비워 둔다(없는 칸 = 모름). 값마다 어디서 왔는지 src 에 적는다:
  source          트래커 원천 칸(편집기에서 사람이 넣은 값)
  name            길 이름에 적힌 값("12칸")
  name_estimated  길 이름에 적혔지만 "추정·쯤·안팎·약"이 붙은 값
  structure       구조물 파일(계단 단·차양·기단·가로등)에서 잰 값
  geometry        길 선의 좌표(높이 포함)에서 계산한 값
  estimated       문헌 기본값으로 어림한 값
  unknown         모름
"""

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

WALK_MPS = 1.2  # 평지 보행 속도
SLOPE_WINDOW_M = 2.0  # 기울기는 수평 2 m 이상 묶음으로 잰다(꼭짓점 높이 잡음 줄이기)
SAMPLE_M = 1.0  # 지붕·조명·차도·바닥은 길을 1 m 마다 찍어 본다
STEP_RISE_EST_M = 0.16  # 단 수를 모를 때 한 단 높이(구조물 계단 단 높이 중앙값)
STEP_RISE_OK = (0.10, 0.31)  # 이름에서 읽은 단 수를 믿는 한 단 높이 범위
STEP_RUN_M = 0.3  # 디딤 깊이(계단 길이 중 단이 차지하는 부분)
STEP_UP_S, STEP_DOWN_S = 0.6, 0.5  # 한 단 오르기·내리기 시간(문헌 어림)
ELEVATOR_WAIT_S = 30.0  # 승강기 기다림(탈 때 한 번, 어림)
ELEVATOR_MPS = 1.0  # 승강기 오르내림 속도(어림)
ELEVATOR_STOP_S = 8.0  # 한 번 서고 문 여닫는 시간(어림)
HEADROOM_M = 1.8  # 머리 위 덮개로 보는 최소 높이
OVERHEAD_MAX_M = 15.0  # 이보다 높으면 덮개로 안 본다(비가 들이침)
LAMP_RADIUS_M = 15.0  # 가로등 하나가 밝히는 수평 반경(어림)
LAMP_DZ_M = 5.0
VEHICLE_NEAR_M = 3.0  # 차도에서 이만큼 안이면 차도 옆 길
VEHICLE_NEAR_RATIO = 0.3
LANDMARK_RADIUS_M = 25.0
LANDMARK_DZ_M = 3.0
WHEELCHAIR_MAX_SLOPE_PCT = 8.33  # 1/12: 경사로 기울기 한도(장애인등편의법)
WHEELCHAIR_COMFORT_SLOPE_PCT = 5.56  # 1/18: 보도 권장 기울기
WHEELCHAIR_MIN_WIDTH_M = 0.9  # 유효폭이 이보다 좁으면 못 지나감
SURFACE_DZ_M = 1.0  # 기단 윗면과 길 높이 차 한도(다른 단의 재질을 잡지 않게)
LANDMARK_CATEGORIES = {"landmark", "entrance", "facility", "bus_stop", "parking"}


def poly_len(coords) -> float:
    return sum(math.dist(a, b) for a, b in zip(coords, coords[1:]))


# ---------- 기하 지표 ----------


def _tobler_s(h: float, dz: float) -> float:
    """수평 h m, 높이 차 dz m 를 걷는 시간. Tobler 걷기 함수를 평지 WALK_MPS 에 맞춰 줄인 것."""
    if h <= 0:
        return 0.0
    g = dz / h
    v = WALK_MPS * math.exp(-3.5 * abs(g + 0.05)) / math.exp(-3.5 * 0.05)
    return h / v


def _windows(geometry) -> list[tuple[float, float]]:
    """(수평 길이, 높이 차) 묶음. 수평 SLOPE_WINDOW_M 이 찰 때까지 이어 붙이고, 끝의 짧은 묶음은 앞 묶음에 붙인다."""
    out, h, dz = [], 0.0, 0.0
    for a, b in zip(geometry, geometry[1:]):
        h += math.dist(a[:2], b[:2])
        dz += b[2] - a[2]
        if h >= SLOPE_WINDOW_M:
            out.append((h, dz))
            h, dz = 0.0, 0.0
    if h > 0 or dz:
        if out and h < SLOPE_WINDOW_M:
            ph, pdz = out.pop()
            out.append((ph + h, pdz + dz))
        else:
            out.append((h, dz))
    return out


def steps_estimate(dz: float) -> int:
    return max(1, round(abs(dz) / STEP_RISE_EST_M))


def geometry_indicators(geometry, kind: str, steps: int | None = None) -> dict:
    """선 좌표만으로 정해지는 지표. 계단은 단 수(모르면 높이로 어림)로 시간을 낸다."""
    hor = sum(math.dist(a[:2], b[:2]) for a, b in zip(geometry, geometry[1:]))
    climb = sum(max(0.0, b[2] - a[2]) for a, b in zip(geometry, geometry[1:]))
    desc = sum(max(0.0, a[2] - b[2]) for a, b in zip(geometry, geometry[1:]))
    net = geometry[-1][2] - geometry[0][2]
    if kind == "elevator" or hor < 0.5:
        max_slope = avg_slope = None
    else:
        max_slope = max((abs(dz) / h * 100 for h, dz in _windows(geometry) if h > 0), default=0.0)
        avg_slope = abs(net) / hor * 100
    if kind == "elevator":
        fwd = back = abs(net) / ELEVATOR_MPS + ELEVATOR_STOP_S
    elif kind == "stairs":
        n = steps or steps_estimate(net)
        flat = max(0.0, hor - n * STEP_RUN_M) / WALK_MPS
        up, down = n * STEP_UP_S + flat, n * STEP_DOWN_S + flat
        fwd, back = (up, down) if net >= 0 else (down, up)
    else:
        w = _windows(geometry)
        fwd = sum(_tobler_s(h, dz) for h, dz in w)
        back = sum(_tobler_s(h, -dz) for h, dz in w)
    return {
        "horizontalM": round(hor, 2),
        "climbM": round(climb, 2),
        "descentM": round(desc, 2),
        "maxSlopePct": None if max_slope is None else round(max_slope, 1),
        "avgSlopePct": None if avg_slope is None else round(avg_slope, 1),
        "walkS": round(fwd, 1),
        "walkSBack": round(back, 1),
    }


def wheelchair_derived(kind: str, max_slope, width, source_value: str) -> tuple[str, str]:
    """(값, 출처). 값: yes(원천 확인) / likely(기하로는 됨, 문턱·바닥 미확인) / steep / no / unknown."""
    if source_value == "prohibited":
        return "no", "surveyed"
    if source_value == "allowed":
        return "yes", "surveyed"
    if kind == "stairs":
        return "no", "geometry"
    if width is not None and width < WHEELCHAIR_MIN_WIDTH_M:
        return "no", "geometry"
    if kind == "elevator":
        return "likely", "geometry"
    if max_slope is None:
        return "unknown", "unknown"
    if max_slope > WHEELCHAIR_MAX_SLOPE_PCT:
        return "no", "geometry"
    if max_slope > WHEELCHAIR_COMFORT_SLOPE_PCT:
        return "steep", "geometry"
    return "likely", "geometry"


def basic_indicators(e: dict, indoor: bool) -> dict:
    """구조물 없이 길 하나로 낼 수 있는 지표(지표 없는 옛 꾸러미, 열린 면 가상 간선용)."""
    g = geometry_indicators(e["geometry"], e["kind"], e.get("stepCount"))
    wc, wsrc = wheelchair_derived(e["kind"], g["maxSlopePct"], e.get("widthM"), e.get("wheelchairAccess", "unknown"))
    return {
        **g,
        "indoor": indoor,
        "stepFree": e["kind"] != "stairs",
        "steps": (e.get("stepCount") or steps_estimate(g["climbM"] - g["descentM"])) if e["kind"] == "stairs" else None,
        "minWidthM": e.get("widthM"),
        "coveredRatio": 1.0 if indoor else None,
        "cover": "indoor" if indoor else None,
        "litRatio": None,
        "vehicle": {"vehicle_road": "shared", "crossing": "crossing"}.get(e["kind"], "none" if indoor else None),
        "wheelchair": wc,
        "src": {"wheelchair": wsrc},
    }


# ---------- 구조물 ----------


class _Grid:
    """외곽 bbox 로 찾는 칸 색인."""

    def __init__(self, cell: float = 10.0):
        self.cell, self.cells, self.items = cell, defaultdict(list), []

    def add(self, bbox, item):
        i = len(self.items)
        self.items.append((bbox, item))
        c = self.cell
        for gx in range(int(bbox[0] // c), int(bbox[2] // c) + 1):
            for gy in range(int(bbox[1] // c), int(bbox[3] // c) + 1):
                self.cells[(gx, gy)].append(i)

    def near(self, x, y, r=0.0):
        c, seen = self.cell, set()
        for gx in range(int((x - r) // c), int((x + r) // c) + 1):
            for gy in range(int((y - r) // c), int((y + r) // c) + 1):
                for i in self.cells.get((gx, gy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    b, item = self.items[i]
                    if b[0] - r <= x <= b[2] + r and b[1] - r <= y <= b[3] + r:
                        yield item


def _bbox(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return (min(xs), min(ys), max(xs), max(ys))


def _inside(x, y, ring) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _dist_ring(x, y, ring) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        best = min(best, _seg_dist(x, y, (x1, y1), (x2, y2)))
    return best


def _seg_dist(x, y, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / d2)) if d2 else 0.0
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def _rings(geom) -> list[list]:
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return []
    out = []
    for p in polys:
        r = [[float(q[0]), float(q[1])] for q in p[0]]
        if len(r) > 1 and r[0] == r[-1]:
            r.pop()
        if len(r) >= 3:
            out.append(r)
    return out


def _centroid(ring):
    return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)


LAMP_PART = re.compile(r"-(HEAD|LIGHT)\d*$")


def parse_structures(features: list[dict]) -> dict:
    """구조물 GeoJSON 피처(트래커 frontend/public/structures 형식) → 지표 계산용 묶음."""
    s = {
        "overhead": [],  # 차양·데크: 밑면이 길 위 HEADROOM_M~OVERHEAD_MAX_M 이면 덮개
        "floors": [],  # 데크·참: 윗면이 길 높이면 바닥 재질
        "voids": [],
        "platforms": [],
        "stairs": defaultdict(list),
        "lamps": [],
        "solids": [],  # 높이가 있는 구조물 전부(벽·화단·턱·계단 단·나무 등): 걷는 면에서 솟은 것은 뺀다
        "count": len(features),
    }
    for f in features:
        p = f.get("properties") or {}
        t, kind = p.get("type"), p.get("kind")
        for ring in _rings(f.get("geometry") or {"type": None}):
            item = {"id": p.get("id"), "ring": ring, "bbox": _bbox(ring), "fromM": p.get("fromM"), "toM": p.get("toM")}
            if kind == "extrude":
                s["solids"].append({**item, "type": t})
            if kind == "platform":
                item.update(topM=p.get("topM"), topProfile=p.get("topProfile"), material=p.get("material"))
                s["platforms"].append(item)
            elif kind == "void":
                s["voids"].append(item)
            elif t in ("canopy", "deck"):
                s["overhead"].append(item)
                if t == "deck":
                    s["floors"].append({**item, "material": p.get("material") or "timber"})
            elif t == "landing":
                s["floors"].append({**item, "material": p.get("material") or "concrete"})
            elif t == "stair_step":
                cx, cy = _centroid(ring)
                s["stairs"][p.get("stair") or p.get("id")].append(
                    {"x": cx, "y": cy, "z": p.get("toM"), "riseM": p.get("riseM"), "widthM": p.get("widthM")}
                )
            elif "LAMP" in (p.get("id") or "") and not LAMP_PART.search(p["id"]):
                cx, cy = _centroid(ring)
                s["lamps"].append({"id": p["id"], "x": cx, "y": cy, "z": p.get("fromM")})
    s["stairs"] = dict(s["stairs"])
    return s


def load_structures(directory: str) -> dict:
    feats, files = [], []
    for path in sorted(Path(directory).glob("*.geojson")):
        feats += json.loads(path.read_text(encoding="utf8")).get("features", [])
        files.append(path.name)
    s = parse_structures(feats)
    s["files"] = files
    return s


def platform_top(p: dict, x: float, y: float) -> float | None:
    if p.get("topM") is not None:
        return float(p["topM"])
    prof = p.get("topProfile")
    if not prof:
        return None
    (ox, oy), (dx, dy) = prof["origin"], prof["direction"]
    n = math.hypot(dx, dy) or 1.0
    d = ((x - ox) * dx + (y - oy) * dy) / n
    pts = prof["points"]
    if d <= pts[0][0]:
        return float(pts[0][1])
    for (d0, h0), (d1, h1) in zip(pts, pts[1:]):
        if d <= d1:
            return h0 + (h1 - h0) * (d - d0) / (d1 - d0) if d1 > d0 else h1
    return float(pts[-1][1])


# ---------- 붙이기 ----------


def _samples(geometry, step=SAMPLE_M):
    """선을 step m 조각으로 나눈 조각 가운데 점들."""
    total = poly_len(geometry)
    n = max(1, math.ceil(total / step))
    out, seg, acc = [], 0, 0.0
    lens = [math.dist(a, b) for a, b in zip(geometry, geometry[1:])]
    for i in range(n):
        t = (i + 0.5) * total / n
        while seg < len(lens) - 1 and acc + lens[seg] < t:
            acc += lens[seg]
            seg += 1
        a, b = geometry[seg], geometry[seg + 1]
        r = (t - acc) / lens[seg] if lens[seg] else 0.0
        out.append(tuple(a[k] + r * (b[k] - a[k]) for k in range(3)))
    return out


NAME_STEPS_EQ = re.compile(r"=\s*(\d+)\s*(?:칸|단)")
NAME_STEPS = re.compile(r"(\d+)\s*(?:칸|단)")
NAME_UNSURE = re.compile(r"추정|쯤|안팎|약|~|대략")


def steps_from_name(name: str | None) -> tuple[int | None, str | None]:
    if not name:
        return None, None
    m = NAME_STEPS_EQ.search(name) or NAME_STEPS.search(name)
    if not m:
        return None, None
    tail = name[m.start() : m.end() + 6]
    head = name[max(0, m.start() - 3) : m.start()]
    return int(m.group(1)), ("name_estimated" if NAME_UNSURE.search(head + tail) else "name")


def _assign_stairs(stair_edges: list[dict], groups: dict) -> dict[str, list[str]]:
    """계단 단 묶음마다 가장 많이 겹치는 계단 길 하나에 준다(단 절반 이상이 길 2.5 m 안, 높이 범위 안)."""
    out = defaultdict(list)
    for gname, steps in sorted(groups.items()):
        best, best_n = None, 0
        for e in stair_edges:
            g = e["geometry"]
            zs = [p[2] for p in g]
            lo, hi = min(zs) - 0.5, max(zs) + 0.5
            n = sum(
                1
                for s in steps
                if s["z"] is not None
                and lo <= s["z"] <= hi
                and min(_seg_dist(s["x"], s["y"], a, b) for a, b in zip(g, g[1:])) < 2.5
            )
            if n > best_n:
                best, best_n = e, n
        if best is not None and best_n >= max(2, len(steps) * 0.5):
            out[best["key"]].append(gname)
    return out


COVERAGE_FIELDS = [
    # (이름, 노드/길, 해당 여부, 값 있는지)
    ("horizontalM", "edge", lambda e: True, lambda i: i["horizontalM"] is not None),
    ("climbM·descentM", "edge", lambda e: True, lambda i: i["climbM"] is not None),
    ("maxSlopePct", "edge", lambda e: e["kind"] != "elevator", lambda i: i["maxSlopePct"] is not None),
    ("walkS", "edge", lambda e: True, lambda i: i["walkS"] is not None),
    ("steps", "edge", lambda e: e["kind"] == "stairs", lambda i: i["src"].get("steps") != "estimated"),
    ("flights", "edge", lambda e: e["kind"] == "stairs", lambda i: i.get("flights") is not None),
    ("minWidthM", "edge", lambda e: e["kind"] != "elevator", lambda i: i["minWidthM"] is not None),
    ("cover", "edge", lambda e: True, lambda i: i["cover"] is not None),
    ("surface", "edge", lambda e: e["kind"] != "elevator", lambda i: i["surface"] is not None),
    ("litRatio", "edge", lambda e: not e.get("_building"), lambda i: i["litRatio"] is not None),
    ("vehicle", "edge", lambda e: True, lambda i: i["vehicle"] is not None),
    ("wheelchair(기하)", "edge", lambda e: True, lambda i: i["wheelchair"] != "unknown"),
    ("wheelchair(확인)", "edge", lambda e: True, lambda i: i["src"]["wheelchair"] == "surveyed"),
    ("elevator.floors", "edge", lambda e: e["kind"] == "elevator", lambda i: bool(i.get("elevator", {}).get("floors"))),
    # 현장·학교 자료가 있어야 하는 것(지금 원천에 칸이 없음)
    ("accessHours", "edge", lambda e: True, lambda i: i.get("accessHours") is not None),
    ("handrail", "edge", lambda e: e["kind"] == "stairs", lambda i: i.get("handrail") is not None),
    ("crossingSignal", "edge", lambda e: e["kind"] == "crossing", lambda i: i.get("crossingSignal") is not None),
    (
        "elevator.hours",
        "edge",
        lambda e: e["kind"] == "elevator",
        lambda i: i.get("elevator", {}).get("hours") is not None,
    ),
    ("landmark", "node", lambda n: True, lambda i: i.get("landmark") is not None),
    ("lit(바깥 노드)", "node", lambda n: n["location"] == "outdoor", lambda i: i.get("lit") is not None),
    ("thresholdCm", "node", lambda n: n["kind"] == "door", lambda i: i.get("thresholdCm") is not None),
    ("doorType", "node", lambda n: n["kind"] == "door", lambda i: i.get("doorType") is not None),
]


def annotate(content: dict, structs: dict | None) -> dict:
    """content 의 edges[].indicators, nodes[].indicators 를 채우고 덮개율 보고를 돌려준다."""
    nodes = {n["key"]: n for n in content["nodes"]}
    floors = {f["key"]: f for f in content["floors"]}
    S = structs
    grids = {}
    if S is not None:
        for name in ("overhead", "floors", "voids", "platforms"):
            g = _Grid()
            for it in S[name]:
                g.add(it["bbox"], it)
            grids[name] = g
        lamp_grid = _Grid()
        for lp in S["lamps"]:
            lamp_grid.add((lp["x"], lp["y"], lp["x"], lp["y"]), lp)
        bld_grid = _Grid()
        for b in content["buildings"]:
            if b.get("footprint"):
                bld_grid.add(_bbox(b["footprint"]), b["footprint"])
    roads = _Grid(5.0)
    for e in content["edges"]:
        if e["kind"] == "vehicle_road" and not e.get("_building"):
            for a, b in zip(e["geometry"], e["geometry"][1:]):
                roads.add((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), (a, b))

    stair_groups = (
        _assign_stairs([e for e in content["edges"] if e["kind"] == "stairs"], S["stairs"]) if S is not None else {}
    )

    # 승강기 통로: 승강기 길로 이어진 덩어리
    elev = [e for e in content["edges"] if e["kind"] == "elevator"]
    parent = {}

    def root(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for e in elev:
        parent[root(e["from"])] = root(e["to"])
    shafts = defaultdict(list)
    for e in elev:
        shafts[root(e["from"])].append(e)
    shaft_info = {}
    for r, es in shafts.items():
        stops = {k for e in es for k in (e["from"], e["to"])}
        codes = {
            floors[nodes[k]["floorKey"]]["code"]: floors[nodes[k]["floorKey"]]["sortOrder"]
            for k in stops
            if nodes[k]["floorKey"]
        }
        info = {
            "shaft": min(e["key"] for e in es),
            "floors": sorted(codes, key=codes.get),
            "stops": len(stops),
            "waitS": ELEVATOR_WAIT_S,
        }
        for k in stops:
            shaft_info[k] = info
        for e in es:
            shaft_info[e["key"]] = info

    for e in content["edges"]:
        indoor = bool(e.get("_building"))
        kind, geo = e["kind"], e["geometry"]
        src = {}
        # 계단
        steps = flights = rise_m = None
        width, src["minWidthM"] = (e["widthM"], "source") if e.get("widthM") else (None, "unknown")
        if kind == "stairs":
            net = geo[-1][2] - geo[0][2]
            n, how = steps_from_name(e["name"])
            if n is not None and not STEP_RISE_OK[0] <= abs(net) / n <= STEP_RISE_OK[1]:
                n = how = None  # 이름의 단 수가 높이 차와 안 맞음(한 줄 단 수만 적은 경우 등)
            groups = [S["stairs"][g] for g in stair_groups.get(e["key"], [])] if S is not None else []
            if groups:
                flights = len(groups)
                widths = [s["widthM"] for g in groups for s in g if s.get("widthM")]
                if width is None and widths:
                    width, src["minWidthM"] = min(widths), "structure"
            if n is not None and how == "name":
                steps, src["steps"] = n, how
            elif groups:
                steps, src["steps"] = sum(len(g) for g in groups), "structure"
            elif n is not None:
                steps, src["steps"] = n, how
            else:
                steps, src["steps"] = steps_estimate(net), "estimated"
            rise_m = round(abs(net) / steps, 3) if steps else None
        g = geometry_indicators(geo, kind, steps)
        i = {**g, "indoor": indoor, "stepFree": kind != "stairs"}
        if kind == "stairs":
            i.update(steps=steps, flights=flights, stepRiseM=rise_m)
        i["minWidthM"] = width

        pts = _samples(geo) if not indoor else []
        # 지붕
        if indoor or kind == "elevator":
            i["coveredRatio"], i["cover"], src["cover"] = 1.0, "indoor", "source"
        elif S is None:
            i["coveredRatio"], i["cover"], src["cover"] = None, None, "unknown"
        else:
            kinds = []
            for x, y, z in pts:
                k = None
                if any(
                    v["fromM"] is not None
                    and v["fromM"] - 0.5 <= z <= v["toM"] - HEADROOM_M
                    and _inside(x, y, v["ring"])
                    for v in grids["voids"].near(x, y)
                ):
                    k = "underground"
                elif any(
                    o["fromM"] is not None
                    and z + HEADROOM_M <= o["fromM"] <= z + OVERHEAD_MAX_M
                    and _inside(x, y, o["ring"])
                    for o in grids["overhead"].near(x, y)
                ):
                    k = "canopy"
                elif any(_inside(x, y, r) and _dist_ring(x, y, r) > 1.0 for r in bld_grid.near(x, y)):
                    k = "building"
                kinds.append(k)
            cov = [k for k in kinds if k]
            ratio = len(cov) / len(kinds)
            i["coveredRatio"] = round(ratio, 2)
            if not cov:
                i["cover"] = "none"
            elif ratio >= 0.95:
                i["cover"] = Counter(cov).most_common(1)[0][0]
            else:
                i["cover"] = "mixed"
            src["cover"] = "structure"
        i["rainExposedM"] = None if i["coveredRatio"] is None else round(e["lengthM"] * (1 - i["coveredRatio"]), 1)
        # 바닥
        if indoor:
            i["surface"], src["surface"] = "indoor_floor", "source"
        elif kind == "elevator" or S is None:
            i["surface"], src["surface"] = None, "unknown"
        else:
            mats = []
            for x, y, z in pts:
                best = None
                for fl in grids["floors"].near(x, y):
                    if fl["toM"] is not None and abs(fl["toM"] - z) <= 0.5 and _inside(x, y, fl["ring"]):
                        best = (abs(fl["toM"] - z), fl["material"])
                if best is None:
                    for p in grids["platforms"].near(x, y):
                        top = platform_top(p, x, y)
                        if (
                            top is not None
                            and abs(top - z) <= SURFACE_DZ_M
                            and p.get("material")
                            and _inside(x, y, p["ring"])
                        ):
                            if best is None or abs(top - z) < best[0]:
                                best = (abs(top - z), p["material"])
                if best:
                    mats.append(best[1])
            if mats:
                i["surface"] = Counter(mats).most_common(1)[0][0]
                i["surfaceRatio"] = round(len(mats) / len(pts), 2)
                src["surface"] = "structure"
            else:
                i["surface"], src["surface"] = None, "unknown"
        # 조명
        if indoor or kind == "elevator" or S is None:
            i["litRatio"], i["lampCount"], src["lit"] = None, None, "indoor" if indoor else "unknown"
        else:
            lit, seen = 0, set()
            for x, y, z in pts:
                hit = [
                    lp
                    for lp in lamp_grid.near(x, y, LAMP_RADIUS_M)
                    if math.hypot(lp["x"] - x, lp["y"] - y) <= LAMP_RADIUS_M
                    and (lp["z"] is None or abs(lp["z"] - z) <= LAMP_DZ_M)
                ]
                lit += bool(hit)
                seen.update(lp["id"] for lp in hit)
            # 가로등 자료는 사진으로 확인한 곳에만 있다: 가로등이 안 잡히면 어둡다가 아니라 모름.
            i["litRatio"] = round(lit / len(pts), 2) if lit else None
            i["lampCount"] = len(seen)
            src["lit"] = "structure" if lit else "unknown"
        # 차량
        if kind == "vehicle_road":
            i["vehicle"] = "shared"
        elif kind == "crossing":
            i["vehicle"], i["crossing"] = "crossing", "marked"
        elif indoor or kind == "elevator":
            i["vehicle"] = "none"
        else:
            near = sum(
                1
                for x, y, z in pts
                if any(
                    abs((a[2] + b[2]) / 2 - z) <= 1.5 and _seg_dist(x, y, a, b) <= VEHICLE_NEAR_M
                    for a, b in roads.near(x, y, VEHICLE_NEAR_M)
                )
            )
            i["vehicle"] = "adjacent" if near / len(pts) >= VEHICLE_NEAR_RATIO else "none"
        # 휠체어
        i["wheelchair"], src["wheelchair"] = wheelchair_derived(kind, i["maxSlopePct"], width, e["wheelchairAccess"])
        if e["key"] in shaft_info:
            i["elevator"] = shaft_info[e["key"]]
        src["geometry"] = "geometry_estimated" if e.get("estimated") else "geometry"
        i["src"] = src
        e["indicators"] = i

    # 노드
    places = [p for p in content["places"] if p["category"] in LANDMARK_CATEGORIES]
    for n in content["nodes"]:
        ni = {}
        best = None
        for p in places:
            d = math.hypot(p["x"] - n["x"], p["y"] - n["y"])
            if d <= LANDMARK_RADIUS_M and abs(p["z"] - n["z"]) <= LANDMARK_DZ_M and (best is None or d < best[0]):
                best = (d, p)
        if best:
            ni["landmark"] = {"key": best[1]["key"], "name": best[1]["name"], "distM": round(best[0], 1)}
        if S is not None and n["location"] == "outdoor":
            if any(
                math.hypot(lp["x"] - n["x"], lp["y"] - n["y"]) <= LAMP_RADIUS_M
                and (lp["z"] is None or abs(lp["z"] - n["z"]) <= LAMP_DZ_M)
                for lp in lamp_grid.near(n["x"], n["y"], LAMP_RADIUS_M)
            ):
                ni["lit"] = True
        if n["key"] in shaft_info:
            ni["elevator"] = shaft_info[n["key"]]
        n["indicators"] = ni

    return coverage(content)


def coverage(content: dict) -> dict:
    fields = {}
    for name, per, applies, known in COVERAGE_FIELDS:
        rows = content["edges"] if per == "edge" else content["nodes"]
        app = [r for r in rows if applies(r)]
        k = sum(1 for r in app if known(r.get("indicators") or {}))
        fields[name] = {"per": per, "known": k, "total": len(app), "pct": round(100 * k / len(app), 1) if app else None}
    return {"edges": len(content["edges"]), "nodes": len(content["nodes"]), "fields": fields}
