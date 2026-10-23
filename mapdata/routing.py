"""공개된 꾸러미 하나로 하는 검색·경로 계산 (메모리 안)."""

import copy
import heapq
import math
import re
from collections import defaultdict

from mapdata.indicators import ELEVATOR_WAIT_S, basic_indicators
from mapdata.surfaces import surface_z

ATTACH_RADIUS_M = 25.0
ATTACH_DZ_M = 2.0
NODE_AT_M = 0.05  # 점이 노드에서 이만큼 안이면 그 노드
# 열린 면(광장·운동장 등) 가로지르기: 면 위 노드끼리 면 안에 머무는 직선으로 잇는다(꾸러미에는 없고 읽을 때 만든다).
AREA_DZ_M = 0.5  # 면 높이와 노드 높이 차 한도. 넘으면 다른 단이라 잇지 않는다(단 사이는 벽).
AREA_MAX_NEIGHBORS = 12  # 노드마다 가까운 이웃 몇 개까지만(면 노드가 많아도 간선 수가 n² 로 늘지 않게)
SURFACE_SAMPLE_M = 2.0  # 기단 면 가로지르기 선은 이만큼마다 면 높이를 찍는다(기울기·걷는 시간용)
OPEN_AREA_FACTOR = 1.02  # 같은 길이면 그려 둔 길(실측 선)을 고르게 하는 작은 덤
VEHICLE_ROAD_FACTOR = 1.5  # 보행 허용이 아닌 차도
VEHICLE_ROAD_WALK_FACTOR = 1.1  # 보행 허용 차도(보차 혼용): 비슷한 길이면 인도를 고르게 하는 작은 덤
AREA_LABEL = {"plaza": "광장", "field": "운동장", "parking_aisle": "주차장"}

# 경로 방식별 비용 규칙. 길 하나의 비용 = 기준값(base) × 곱(mult) × 노출 곱(exposure), exclude 에 걸리면 못 지나감.
#   base       lengthM(길이 m) 또는 walkS(기울기·계단·승강기를 셈한 걷는 시간 s, 가는 방향별)
#   exclude    stairs(계단), wheelchairNo(휠체어 판정 no: 계단·1/12 넘는 기울기·폭 0.9 m 미만·원천 금지)
#   mult       stairs, elevator, pedestrianUnknown(보행 통행 모름), vehicleRoadWalk(보행 허용 차도),
#              vehicleRoad(그 밖 차도), openArea(열린 면 가상 간선), wheelchairSteep(1/18~1/12),
#              wheelchairUnknown(기울기 모름)
#   exposure   rain:    × (1 + k × 비 맞는 비율). 지붕 모름은 맞는 것으로 셈
#              dark:    바깥 길 × (1 + k × 가로등 없는·모르는 비율). 실내는 0
#              vehicle: 차도(shared)·횡단보도 × (1 + k)
#   elevatorWaitS  승강기에 탈 때 한 번 더하는 기다림(초). base 가 walkS 일 때만 비용에 들어감
_COMMON = {"pedestrianUnknown": 1.2, "vehicleRoadWalk": 1.1, "vehicleRoad": 1.5, "openArea": 1.02}
MODE_SPEC = {
    "shortest": {"label": "최단 거리", "base": "lengthM", "exclude": [], "mult": dict(_COMMON)},
    "fastest": {
        "label": "가장 빠른 길",
        "base": "walkS",
        "exclude": [],
        "mult": dict(_COMMON),
        "elevatorWaitS": ELEVATOR_WAIT_S,
        "note": "오르막·계단·승강기 기다림을 셈한 어림 시간. 승강기 기다림 30 초는 어림값",
    },
    "avoidStairs": {"label": "계단 없이", "base": "lengthM", "exclude": ["stairs"], "mult": dict(_COMMON)},
    "accessible": {
        "label": "휠체어·유모차",
        "base": "lengthM",
        "exclude": ["stairs", "wheelchairNo"],
        "mult": {**_COMMON, "wheelchairSteep": 2.0, "wheelchairUnknown": 1.5},
        "note": "계단·가파른 길(1/12 넘음)만 기하로 뺐다. 문턱·문 종류·바닥 상태·승강기 운영은 확인 안 됨",
    },
    "preferElevator": {
        "label": "승강기 우선",
        "base": "lengthM",
        "exclude": [],
        "mult": {**_COMMON, "stairs": 4.0, "elevator": 0.5},
    },
    "rainProtected": {
        "label": "비 안 맞는 길",
        "base": "lengthM",
        "exclude": [],
        "mult": dict(_COMMON),
        "exposure": {"rain": 2.0},
        "note": "지붕은 실내·지하·구조물 차양 기준(구조물은 추정 포함). 지붕을 모르는 바깥 길은 비 맞는 것으로 셈",
    },
    "nightSafe": {
        "label": "밤에 밝은 길",
        "base": "lengthM",
        "exclude": [],
        "mult": dict(_COMMON),
        "exposure": {"dark": 1.0, "vehicle": 0.5},
        "note": "가로등은 사진으로 확인한 곳만 자료에 있음(나머지는 어두운 것으로 셈). 건물 야간 출입은 모름",
    },
}
MODES = tuple(MODE_SPEC)
_WC_ORDER = ["yes", "likely", "steep", "unknown", "no"]


def normalize(s: str) -> str:
    """클라이언트 search.ts 의 normalize 와 같게: 소문자, 공백·기호 제거."""
    return re.sub(r"[\s()\[\]{}·.,=\-–_/#'\"]+", "", s.lower())


def _strip_suffix(s: str) -> str:
    return s[:-1] if len(s) > 2 and s[-1] in "관동" else s


def _floor_match(t: str, code: str | None) -> bool:
    if not code:
        return False
    m = re.fullmatch(r"(지하)?(\d+)층?", t)
    if not m:
        return t == code.lower()
    return (f"b{m.group(2)}" if m.group(1) else f"{m.group(2)}f") == code.lower()


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


class _Ring:
    """면 외곽(닫히지 않은 꼭짓점 목록). 경계 위 점은 안으로 본다."""

    def __init__(self, polygon: list):
        pts = [(float(p[0]), float(p[1])) for p in polygon]
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        self.edges = list(zip(pts, pts[1:] + pts[:1]))

    def covers(self, p) -> bool:
        inside = False
        for a, b in self.edges:
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1.0)))
            if math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) < 1e-6:
                return True
            if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < a[0] + (p[1] - a[1]) / (b[1] - a[1]) * (b[0] - a[0]):
                inside = not inside
        return inside

    def on_edge(self, p) -> bool:
        for a, b in self.edges:
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1.0)))
            if math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy) < 1e-6:
                return True
        return False

    def cuts(self, p, q, length: float) -> list[float]:
        """선분 pq 가 이 고리의 변과 만나는 곳(pq 위 비율 t)."""
        cuts = []
        for a, b in self.edges:
            d1, d2 = _cross(p, q, a), _cross(p, q, b)
            on1, on2 = abs(d1) < 1e-7 * length, abs(d2) < 1e-7 * length
            if on1 and on2:  # 경계와 겹쳐 나란한 변
                for c in (a, b):
                    cuts.append(((c[0] - p[0]) * (q[0] - p[0]) + (c[1] - p[1]) * (q[1] - p[1])) / length**2)
            elif (d1 > 0) != (d2 > 0) or on1 or on2:
                den = _cross(a, b, p) - _cross(a, b, q)
                if den:
                    cuts.append(_cross(a, b, p) / den)
        return cuts

    def sees(self, p, q) -> bool:
        """선분 pq 가 통째로 면 안(경계 포함)에 있는가: 경계와 만나는 곳마다 자르고 조각 가운데를 본다."""
        length = math.dist(p, q)
        if length < 1e-9:
            return True
        cuts = [0.0, 1.0, *self.cuts(p, q, length)]
        ts = sorted(t for t in cuts if 0.0 <= t <= 1.0)
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 < 1e-9:
                continue
            m = (t0 + t1) / 2
            if not self.covers((p[0] + m * (q[0] - p[0]), p[1] + m * (q[1] - p[1]))):
                return False
        return True


class _Region:
    """구멍 있는 면 여러 조각([[바깥 고리, 구멍 고리...], ...]). 경계 위 점은 안으로 본다."""

    def __init__(self, polygons: list):
        self.parts = [(_Ring(p[0]), [_Ring(h) for h in p[1:]]) for p in polygons]
        self.rings = [r for outer, holes in self.parts for r in [outer, *holes]]

    def covers(self, p) -> bool:
        for outer, holes in self.parts:
            if outer.covers(p) and not any(h.covers(p) and not h.on_edge(p) for h in holes):
                return True
        return False

    def sees(self, p, q) -> bool:
        length = math.dist(p, q)
        if length < 1e-9:
            return self.covers(p)
        ts = sorted(t for r in self.rings for t in r.cuts(p, q, length) if 0.0 <= t <= 1.0)
        ts = [0.0, *ts, 1.0]
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 < 1e-9:
                continue
            m = (t0 + t1) / 2
            if not self.covers((p[0] + m * (q[0] - p[0]), p[1] + m * (q[1] - p[1]))):
                return False
        return True


class MapIndex:
    def __init__(self, pkg: dict):
        self.pkg = pkg
        self.nodes = {n["key"]: n for n in pkg["nodes"]}
        self.edges = {e["key"]: e for e in pkg["edges"]}
        self.places = {p["key"]: p for p in pkg["places"]}
        self.buildings = {b["key"]: b for b in pkg["buildings"]}
        self.floors = {f["key"]: f for f in pkg["floors"]}
        self.out = defaultdict(list)  # node → [(edge, 다음 노드)]
        for e in pkg["edges"]:
            if e["pedestrianAccess"] == "prohibited":
                continue
            if e["direction"] in ("both", "forward"):
                self.out[e["from"]].append((e, e["to"]))
            if e["direction"] in ("both", "backward"):
                self.out[e["to"]].append((e, e["from"]))
        self.walk_areas = {a["key"]: a for a in pkg.get("walkAreas", [])}
        for e in self._area_edges(pkg.get("walkAreaNodes", [])):
            self.edges[e["key"]] = e
            self.out[e["from"]].append((e, e["to"]))
            self.out[e["to"]].append((e, e["from"]))
        self.walk_surfaces = {s["key"]: s for s in pkg.get("walkSurfaces", [])}
        joined = {frozenset((e["from"], e["to"])) for e in self.edges.values() if e["kind"] == "open_area"}
        for e in self._surface_edges(pkg.get("walkSurfaceNodes", []), joined):
            self.edges[e["key"]] = e
            self.out[e["from"]].append((e, e["to"]))
            self.out[e["to"]].append((e, e["from"]))
        # 지표: 꾸러미에 있으면 그대로, 없으면(옛 꾸러미·열린 면 가상 간선) 길 선으로 계산
        self.ind = {k: self._indicators(e, self.nodes) for k, e in self.edges.items()}
        self._mesh = None

    @property
    def mesh(self):
        """보행 그물 길찾기(꾸러미에 walkMeshes 가 있을 때). 처음 쓸 때 만든다(관문 표 계산)."""
        if self._mesh is None and self.pkg.get("walkMeshes"):
            from mapdata.walkmesh import MeshRouter

            self._mesh = MeshRouter(self, self.pkg["walkMeshes"])
        return self._mesh

    def _indicators(self, e: dict, nodes: dict) -> dict:
        i = e.get("indicators")
        if i is not None:
            return i
        indoor = all(nodes[x]["location"] == "indoor" for x in (e["from"], e["to"]) if x in nodes)
        i = basic_indicators(e, indoor)
        if e["kind"] == "open_area":
            i["vehicle"] = "shared" if e.get("areaKind") == "parking_aisle" else "none"
            if e.get("surfaceKey"):  # 기단 면: 아스팔트이거나 빗금 차도·주차면이면 차와 같이 쓰는 곳
                sf = self.walk_surfaces[e["surfaceKey"]]
                if sf.get("material") == "asphalt" or any(w in (sf["name"] or "") for w in ("차도", "주차")):
                    i["vehicle"] = "shared"
        return i

    def _area_edges(self, area_nodes: list[dict]) -> list[dict]:
        """면마다 같은 높이 노드끼리, 면 안에 머무는 직선이면 가상 간선(kind open_area)."""
        members = defaultdict(list)
        for w in area_nodes:
            a, n = self.walk_areas.get(w["areaKey"]), self.nodes.get(w["nodeKey"])
            if a and n and a["z"] is not None and abs(n["z"] - a["z"]) <= AREA_DZ_M:
                members[a["key"]].append(n)
        self.area_members = {ak: sorted(ns, key=lambda n: n["key"]) for ak, ns in members.items()}
        self.area_rings = {ak: _Ring(a["polygon"]) for ak, a in self.walk_areas.items()}
        out = []
        for ak, ns in sorted(self.area_members.items()):
            for i, j in _visible_pairs(ns, self.area_rings[ak]):
                out.append(self._area_edge(ak, ns[i], ns[j]))
        return out

    def _area_edge(self, ak: str, p: dict, q: dict) -> dict:
        a = self.walk_areas[ak]
        return {
            "key": f"x:{ak}:{p['key']}:{q['key']}",
            "from": p["key"],
            "to": q["key"],
            "name": a["name"] or None,
            "kind": "open_area",
            "areaKind": a["kind"],
            "direction": "both",
            "pedestrianAccess": "allowed",
            "wheelchairAccess": "unknown",
            "lengthM": round(math.dist((p["x"], p["y"], p["z"]), (q["x"], q["y"], q["z"])), 2),
            "stepCount": None,
            "widthM": None,
            "geometry": [[p["x"], p["y"], p["z"]], [q["x"], q["y"], q["z"]]],
            "estimated": True,
        }

    def _surface_edges(self, surface_nodes: list[dict], joined: set) -> list[dict]:
        """기단 면마다 면 높이(±AREA_DZ_M) 노드끼리, 면(구멍 포함) 안에 머무는 직선이면 가상 간선(kind open_area).
        선은 SURFACE_SAMPLE_M 마다 면 높이를 따라가고, 길이는 그 3D 길이. 열린 면이 이미 이은 쌍은 건너뛴다."""
        members = defaultdict(list)
        for w in surface_nodes:
            s, n = self.walk_surfaces.get(w["surfaceKey"]), self.nodes.get(w["nodeKey"])
            if (
                s
                and n
                and n["location"] == "outdoor"
                and abs(n["z"] - surface_z(s["top"], n["x"], n["y"])) <= AREA_DZ_M
            ):
                members[s["key"]].append(n)
        self.surface_members = {sk: sorted(ns, key=lambda n: n["key"]) for sk, ns in members.items()}
        self.surface_regions = {sk: _Region(s["polygons"]) for sk, s in self.walk_surfaces.items()}
        out = []
        for sk, ns in sorted(self.surface_members.items()):
            for i, j in _visible_pairs(ns, self.surface_regions[sk]):
                p, q = ns[i], ns[j]
                if frozenset((p["key"], q["key"])) not in joined:
                    out.append(self._surface_edge(sk, p, q))
        return out

    def _surface_edge(self, sk: str, p: dict, q: dict) -> dict:
        s = self.walk_surfaces[sk]
        g = _surface_line(s["top"], p, q)
        return {
            "key": f"y:{sk}:{p['key']}:{q['key']}",
            "from": p["key"],
            "to": q["key"],
            "name": s["name"] or None,
            "kind": "open_area",
            "areaKind": "surface",
            "surfaceKey": sk,
            "direction": "both",
            "pedestrianAccess": "allowed",
            "wheelchairAccess": "unknown",
            "lengthM": round(sum(math.dist(a, b) for a, b in zip(g, g[1:])), 2),
            "stepCount": None,
            "widthM": None,
            "geometry": g,
            "estimated": True,
        }

    # ---------- 면 위 아무 점 ----------
    def _point_on(self, x: float, y: float, z: float) -> list[tuple]:
        """점 (x, y, z) 를 덮고 높이(±AREA_DZ_M)가 맞는 열린 면·기단 면: [(높이 차, 면 높이, "area"|"surface", key)].
        열린 면 먼저(key 순), 그다음 기단 면(key 순)."""
        hits = []
        for ak in sorted(self.walk_areas):
            a = self.walk_areas[ak]
            if a["z"] is not None and abs(z - a["z"]) <= AREA_DZ_M and self.area_rings[ak].covers((x, y)):
                hits.append((abs(z - a["z"]), float(a["z"]), "area", ak))
        for sk in sorted(self.walk_surfaces):
            zs = surface_z(self.walk_surfaces[sk]["top"], x, y)
            if abs(z - zs) <= AREA_DZ_M and self.surface_regions[sk].covers((x, y)):
                hits.append((abs(z - zs), zs, "surface", sk))
        return hits

    def _point_edges(self, pt: dict, hits: list[tuple], others: list[dict]) -> list[dict]:
        """면 위 임시 노드 pt 를 그 면의 노드(와 같은 면 위 앞서 만든 임시 노드 others)에 잇는 가상 간선.
        같은 면 규칙: 면(구멍 포함) 안에 머무는 직선, 기단 면 선은 면 높이를 따라감. 열린 면이 이은 노드는
        기단 면이 다시 잇지 않는다. 이웃 수 제한은 없다(점 하나라 간선 수가 면 노드 수만큼)."""
        out, joined = [], set()
        for _, _, kind, k in hits:
            shape = self.area_rings[k] if kind == "area" else self.surface_regions[k]
            mem = self.area_members.get(k, []) if kind == "area" else self.surface_members.get(k, [])
            for n in [*mem, *[o for o in others if k in o["_on"]]]:
                if n["key"] in joined or not shape.sees((pt["x"], pt["y"]), (n["x"], n["y"])):
                    continue
                joined.add(n["key"])
                out.append(self._area_edge(k, pt, n) if kind == "area" else self._surface_edge(k, pt, n))
        return out

    def route_points(self, start, goal, mode: str) -> tuple[dict | None, list[dict | None]]:
        """출발·도착이 노드 key 또는 점 (x, y, z). 열린 면·기단 면 위 점은 임시 노드(q:from, q:to)로 넣어
        그 면의 노드와 같은 면 규칙으로 잇는다(면 안에 노드가 없는 넓은 면도 쓸 수 있게). 면 위가 아니거나
        이을 노드가 없으면 25 m·높이 차 2 m 안 가장 가까운 노드. 공유하는 색인은 바꾸지 않는다.
        반환: (경로 또는 None, [출발, 도착] 붙인 결과 {nodeKey, attached: node|surface|nearest_node, areas} 또는 None).
        꾸러미에 보행 그물(walkMeshes)이 있으면 그물 길찾기(walkmesh.MeshRouter)로 한다."""
        if self.mesh is not None:
            return self.mesh.route_points(start, goal, mode)
        tmp_nodes, tmp_edges, ends = {}, [], []
        for key, spec in (("q:from", start), ("q:to", goal)):
            if isinstance(spec, str):
                ends.append({"nodeKey": spec, "attached": "node"} if spec in self.nodes else None)
                continue
            x, y, z = (float(v) for v in spec)
            at = self.node_at(x, y, z)
            if at:  # 노드 자리(앱이 노드 좌표로 보낸 출발 등)는 그 노드
                ends.append({"nodeKey": at["key"], "attached": "node"})
                continue
            hits = self._point_on(x, y, z)
            if hits:
                zs = min(hits, key=lambda h: h[0])[1]
                pt = {
                    "key": key,
                    "location": "outdoor",
                    "buildingKey": None,
                    "floorKey": None,
                    "kind": "point",
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "z": round(zs, 2),
                    "estimated": True,
                    "_on": {h[3] for h in hits},
                }
                es = self._point_edges(pt, hits, list(tmp_nodes.values()))
                if es:
                    tmp_nodes[key] = pt
                    tmp_edges += es
                    ends.append({"nodeKey": key, "attached": "surface", "areas": [h[3] for h in hits]})
                    continue
            n = self.nearest_node(x, y, z)
            ends.append({"nodeKey": n["key"], "attached": "nearest_node"} if n else None)
        if None in ends:
            return None, ends
        view = self._with(tmp_nodes, tmp_edges) if tmp_edges else self
        return view.route(ends[0]["nodeKey"], ends[1]["nodeKey"], mode), ends

    def _with(self, tmp_nodes: dict, tmp_edges: list[dict]) -> "MapIndex":
        """임시 노드·간선을 더한 얕은 복사본(원래 색인의 dict·목록은 바꾸지 않음)."""
        view = copy.copy(self)
        view.nodes = {**self.nodes, **{k: {f: v for f, v in n.items() if f != "_on"} for k, n in tmp_nodes.items()}}
        view.edges = {**self.edges, **{e["key"]: e for e in tmp_edges}}
        view.out = defaultdict(list, self.out)
        for e in tmp_edges:
            view.out[e["from"]] = [*view.out[e["from"]], (e, e["to"])]
            view.out[e["to"]] = [*view.out[e["to"]], (e, e["from"])]
        view.ind = {**self.ind, **{e["key"]: self._indicators(e, view.nodes) for e in tmp_edges}}
        return view

    # ---------- 검색 ----------
    def _building_filter(self, q: str | None):
        if not q:
            return None
        if q in self.buildings:
            return {q}
        t = normalize(q)
        return {
            k
            for k, b in self.buildings.items()
            if t in {normalize(b["name"]), _strip_suffix(normalize(b["name"]))} | {normalize(a) for a in b["aliases"]}
        }

    def search(self, q: str = "", building: str | None = None, floor: str | None = None, limit: int = 30) -> list[dict]:
        tokens = [normalize(t) for t in (q or "").split()]
        tokens = [t for t in tokens if t]
        bset = self._building_filter(building)
        hits = []
        for p in self.places.values():
            b = self.buildings.get(p["buildingKey"])
            f = self.floors.get(p["floorKey"])
            if bset is not None and p["buildingKey"] not in bset:
                continue
            if floor and not (f and (floor == f["key"] or _floor_match(normalize(floor), f["code"]))):
                continue
            name = normalize(p["name"])
            fields = [name, *map(normalize, p["aliases"])]
            if b:
                fields += [normalize(b["name"]), *map(normalize, b["aliases"])]
            if f:
                fields.append(normalize(f["code"]))
            hay = "|".join(fields)
            score, ok = 0.0, True
            for t in tokens:
                if name.startswith(t):
                    score += 10
                elif t in name:
                    score += 6
                elif t in hay:
                    score += 4
                elif _strip_suffix(t) in hay or _floor_match(t, f["code"] if f else None):
                    score += 2
                else:
                    ok = False
                    break
            if ok:
                hits.append((score - len(name) / 100, p, b, f))
        hits.sort(key=lambda h: (-h[0], h[1]["name"]))
        return [self.place_out(p, score=round(s, 2)) for s, p, b, f in hits[:limit]]

    def place_out(self, p: dict, **extra) -> dict:
        b = self.buildings.get(p["buildingKey"])
        f = self.floors.get(p["floorKey"])
        return {
            "key": p["key"],
            "name": p["name"],
            "category": p["category"],
            "location": p["location"],
            "buildingKey": p["buildingKey"],
            "buildingName": b["name"] if b else None,
            "floorKey": p["floorKey"],
            "floorCode": f["code"] if f else None,
            "nodeKey": p["nodeKey"],
            "routable": p["nodeKey"] is not None,
            "x": p["x"],
            "y": p["y"],
            "z": p["z"],
            **extra,
        }

    # ---------- 경로 ----------
    def nearest_node(self, x: float, y: float, z: float) -> dict | None:
        best, bd = None, math.inf
        for n in self.nodes.values():
            if abs(n["z"] - z) > ATTACH_DZ_M or math.hypot(n["x"] - x, n["y"] - y) > ATTACH_RADIUS_M:
                continue
            d = math.dist((n["x"], n["y"], n["z"]), (x, y, z))
            if d < bd:
                best, bd = n, d
        return best

    def node_at(self, x: float, y: float, z: float) -> dict | None:
        """(x, y, z) 에서 NODE_AT_M 안(3D)의 첫 노드(꾸러미 순서)."""
        for n in self.nodes.values():
            if math.dist((n["x"], n["y"], n["z"]), (x, y, z)) <= NODE_AT_M:
                return n
        return None

    def _cost(self, e: dict, mode: str, reverse: bool, boarding: bool) -> float | None:
        return edge_cost(e, self.ind[e["key"]], mode, reverse, boarding)

    def route(self, start: str, goal: str, mode: str) -> dict | None:
        """다익스트라. 상태 = (노드, 승강기 안인지): 승강기 기다림을 탈 때 한 번만 센다."""
        s0 = (start, False)
        dist = {s0: 0.0}
        prev: dict[tuple, tuple[tuple, dict]] = {}
        pq = [(0.0, start, False)]
        end = None
        while pq:
            d, u, riding = heapq.heappop(pq)
            if u == goal:
                end = (u, riding)
                break
            if d > dist.get((u, riding), math.inf):
                continue
            for e, v in self.out[u]:
                c = self._cost(e, mode, reverse=v == e["from"], boarding=not riding)
                if c is None:
                    continue
                sv = (v, e["kind"] == "elevator")
                nd = d + c
                if nd < dist.get(sv, math.inf):
                    dist[sv] = nd
                    prev[sv] = ((u, riding), e)
                    heapq.heappush(pq, (nd, v, sv[1]))
        if end is None:
            return None
        path_nodes, path_edges = [goal], []
        cur = end
        while cur != s0:
            p, e = prev[cur]
            path_edges.append(e)
            path_nodes.append(p[0])
            cur = p
        path_nodes.reverse()
        path_edges.reverse()
        return self._describe(path_nodes, path_edges, mode)

    def floor_label(self, node_key: str) -> dict:
        n = self.nodes[node_key]
        f = self.floors.get(n["floorKey"])
        b = self.buildings.get(n["buildingKey"])
        return {
            "location": n["location"],
            "buildingKey": n["buildingKey"],
            "buildingName": b["name"] if b else None,
            "floorKey": n["floorKey"],
            "floorCode": f["code"] if f else None,
        }

    def _describe(self, nodes: list[str], edges: list[dict], mode: str) -> dict:
        geometry, steps = [], []
        total = 0.0
        stairs = 0
        for i, e in enumerate(edges):
            a, b = nodes[i], nodes[i + 1]
            g = e["geometry"] if e["from"] == a else list(reversed(e["geometry"]))
            geometry.extend(g if not geometry else g[1:])
            total += e["lengthM"]
            stairs += e["kind"] == "stairs"
            fa, fb = self.floor_label(a), self.floor_label(b)
            last = steps[-1] if steps else None
            if (
                last
                and last["kind"] == e["kind"]
                and last["name"] == e["name"]
                and e["kind"] not in ("stairs", "elevator")
            ):
                last["distanceM"] = round(last["distanceM"] + e["lengthM"], 2)
                last["edgeKeys"].append(e["key"])
                last["to"] = fb
                last["toNode"] = b
            else:
                steps.append(
                    {
                        "kind": e["kind"],
                        **({"areaKind": e["areaKind"]} if e["kind"] == "open_area" else {}),
                        "name": e["name"],
                        "distanceM": e["lengthM"],
                        "edgeKeys": [e["key"]],
                        "fromNode": a,
                        "toNode": b,
                        "from": fa,
                        "to": fb,
                    }
                )
        for s in steps:
            s["floorChange"] = (
                s["from"]["floorKey"] != s["to"]["floorKey"] or s["from"]["location"] != s["to"]["location"]
            )
            s["dz"] = round(self.nodes[s["toNode"]]["z"] - self.nodes[s["fromNode"]]["z"], 2)
            s["instruction"] = _instruction(s)
            lm = (self.nodes[s["toNode"]].get("indicators") or {}).get("landmark")
            if lm:
                s["landmark"] = lm["name"]
        summary = self._summary(nodes, edges)
        return {
            "mode": mode,
            "distanceM": round(total, 2),
            "durationS": round(summary.pop("_durationS")),
            "summary": summary,
            "note": MODE_SPEC[mode].get("note"),
            "stairsEdges": stairs,
            "nodeKeys": nodes,
            "edgeKeys": [e["key"] for e in edges],
            "geometry": geometry,
            "steps": steps,
        }

    def _summary(self, nodes: list[str], edges: list[dict]) -> dict:
        """경로 전체의 지표 합. 비·조명은 모르는 곳을 나쁜 쪽(비 맞음·어두움)으로 센다."""
        t = defaultdict(float)
        rides, riding, steps, wc, max_slope = 0, False, 0, "yes", None
        for k, e in enumerate(edges):
            i, rev = self.ind[e["key"]], nodes[k] == e["to"]
            length = e["lengthM"]
            t["climbM"] += i["descentM"] if rev else i["climbM"]
            t["descentM"] += i["climbM"] if rev else i["descentM"]
            t["_durationS"] += i["walkSBack"] if rev else i["walkS"]
            if e["kind"] == "elevator":
                if not riding:
                    rides += 1
                    t["_durationS"] += ELEVATOR_WAIT_S
                riding = True
            else:
                riding = False
            if e["kind"] == "stairs":
                steps += i.get("steps") or 0
            if e["kind"] != "stairs" and i.get("maxSlopePct") is not None:
                max_slope = max(max_slope or 0.0, i["maxSlopePct"])
            t["indoorM" if i.get("indoor") else "outdoorM"] += length
            t["rainExposedM"] += length * (1 - (i.get("coveredRatio") or 0.0))
            if i.get("coveredRatio") is None:
                t["coverUnknownM"] += length
            if not i.get("indoor"):
                t["litOutdoorM"] += length * (i.get("litRatio") or 0.0)
            if i.get("vehicle") == "shared":
                t["vehicleSharedM"] += length
            t["crossings"] += i.get("vehicle") == "crossing"
            w = i.get("wheelchair") or "unknown"
            if _WC_ORDER.index(w) > _WC_ORDER.index(wc):
                wc = w
        keys = ["climbM", "descentM", "indoorM", "outdoorM", "rainExposedM", "coverUnknownM", "litOutdoorM"]
        out = {k: round(t[k], 1) for k in keys + ["vehicleSharedM"]}
        out.update(
            steps=steps,
            maxSlopePct=max_slope,
            crossings=int(t["crossings"]),
            elevatorRides=rides,
            wheelchair=wc,
            _durationS=t["_durationS"],
        )
        return out


def edge_cost(e: dict, i: dict, mode: str, reverse: bool, boarding: bool) -> float | None:
    """길 하나의 방식별 비용(i = 길 지표). None = 이 방식에서 못 지나감."""
    spec = MODE_SPEC[mode]
    if "stairs" in spec["exclude"] and e["kind"] == "stairs":
        return None
    if "wheelchairNo" in spec["exclude"] and i.get("wheelchair") == "no":
        return None
    m = spec["mult"]
    if spec["base"] == "walkS":
        c = i["walkSBack"] if reverse else i["walkS"]
        if boarding and e["kind"] == "elevator":
            c += spec.get("elevatorWaitS", 0.0)
    else:
        c = e["lengthM"]
    if e["kind"] in ("stairs", "elevator"):
        c *= m.get(e["kind"], 1.0)
    if e["pedestrianAccess"] == "unknown":
        c *= m["pedestrianUnknown"]
    if e["kind"] == "vehicle_road":
        c *= m["vehicleRoadWalk"] if e["pedestrianAccess"] == "allowed" else m["vehicleRoad"]
    if e["kind"] == "open_area":
        c *= m["openArea"]
    if i.get("wheelchair") == "steep":
        c *= m.get("wheelchairSteep", 1.0)
    if i.get("wheelchair") == "unknown":
        c *= m.get("wheelchairUnknown", 1.0)
    x = spec.get("exposure", {})
    if "rain" in x:
        c *= 1 + x["rain"] * (1 - (i.get("coveredRatio") or 0.0))
    if "dark" in x and not i.get("indoor"):
        c *= 1 + x["dark"] * (1 - (i.get("litRatio") or 0.0))
    if "vehicle" in x and i.get("vehicle") in ("shared", "crossing"):
        c *= 1 + x["vehicle"]
    return c


def _visible_pairs(ns: list[dict], shape) -> list[tuple[int, int]]:
    """노드마다 가까운 이웃부터 AREA_MAX_NEIGHBORS 개까지, 면 안에 머무는 직선으로 보이는 쌍(i < j)."""
    keep = set()
    for i, n in enumerate(ns):
        near = sorted((math.hypot(m["x"] - n["x"], m["y"] - n["y"]), j) for j, m in enumerate(ns) if j != i)
        picked = 0
        for _, j in near:
            if picked >= AREA_MAX_NEIGHBORS:
                break
            pair = (min(i, j), max(i, j))
            if pair in keep or shape.sees((n["x"], n["y"]), (ns[j]["x"], ns[j]["y"])):
                keep.add(pair)
                picked += 1
    return sorted(keep)


def _surface_line(top: dict, p: dict, q: dict) -> list:
    """p → q 직선을 SURFACE_SAMPLE_M 마다 면 높이로 찍은 3D 선. 두 끝은 노드 높이."""
    h = math.hypot(q["x"] - p["x"], q["y"] - p["y"])
    k = max(1, math.ceil(h / SURFACE_SAMPLE_M))
    pts = [[p["x"], p["y"], p["z"]]]
    for i in range(1, k):
        t = i / k
        x, y = p["x"] + t * (q["x"] - p["x"]), p["y"] + t * (q["y"] - p["y"])
        pts.append([round(x, 2), round(y, 2), round(surface_z(top, x, y), 2)])
    pts.append([q["x"], q["y"], q["z"]])
    return pts


def _where(lbl: dict) -> str:
    if lbl["location"] == "outdoor":
        return "바깥"
    return f"{lbl['buildingName'] or ''} {lbl['floorCode'] or '층 모름'}".strip()


def _instruction(s: dict) -> str:
    name = f" ({s['name']})" if s["name"] else ""
    if s["kind"] == "elevator":
        return f"승강기로 {_where(s['from'])} → {_where(s['to'])}"
    if s["kind"] == "stairs":
        return f"계단 {'올라가기' if s['dz'] > 0 else '내려가기'} {_where(s['from'])} → {_where(s['to'])}{name}"
    move = f" {_where(s['from'])} → {_where(s['to'])}" if s["floorChange"] else ""
    if s["kind"] == "open_area":
        return f"{AREA_LABEL.get(s['areaKind'], '열린 공간')}을 가로질러 {round(s['distanceM'])} m{move}{name}"
    verb = {"ramp": "경사로", "crossing": "횡단보도 건너기", "corridor": "복도"}.get(s["kind"], "걷기")
    return f"{verb} {round(s['distanceM'])} m{move}{name}"
