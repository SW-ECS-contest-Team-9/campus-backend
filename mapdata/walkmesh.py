"""보행 그물(아3 시제품): 걷는 면을 정사각형 칸으로 덮고, 칸마다 가운데 꼭짓점으로 4등분한 세모로 길을 찾는다.

면(plane) 하나 = 높이 함수(top)가 같은 걷는 면·열린 면을 합친 것. 면마다 제 축에 맞춘 격자를 깐다.
  · 축: 기운 면(profile)은 기울기 방향, 평평한 면은 최소 외접 사각형의 긴 변 방향.
  · 칸(한 변 cell m) 하나 = 세모 넷: S(아래 변)·E(오른 변)·N(위 변)·W(왼 변), 모두 칸 가운데 꼭짓점을 낀다.
  · 세모가 면(구멍 포함) 안에 통째로 들면 쓸 수 있는 세모. 칸마다 4 비트(S=1, E=2, N=4, W=8).
  · 꼭짓점 높이는 면 높이 함수. 면 사이는 이어지지 않는다(같은 높이로 맞닿은 곳만 이음 간선, 나머지는 계단·승강기 등 길 간선).
  · 그래프 = 쓸 수 있는 세모의 변(축 방향 cell, 가운데로 가는 대각 cell/√2). 경로는 이 그래프에서 찾고
    같은 면 안 구간은 면 안에 머무는 직선으로 당겨 편다(줄 당기기).
꾸러미에는 세모를 나열하지 않고 면마다 원점·축·칸 크기·칸 수·비트 묶음만 넣는다(앱이 같은 그물을 다시 만든다).
"""

import base64
import heapq
import json
import math
import time
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from mapdata.routing import AREA_DZ_M, MODE_SPEC, SURFACE_SAMPLE_M, MapIndex
from mapdata.surfaces import STEP_OK_M, surface_z

CELL_M = 2.0  # 바깥 넓은 면
CELL_NARROW_M = 1.0  # 좁은 면(2 m 칸으로 덮이는 비율이 MIN_COVER 밑)·실내
MIN_COVER = 0.85
COVER_TOL_M = 0.01  # 세모가 면 경계에서 이만큼 밖으로 나가도 안으로 본다(반올림 오차)
ATTACH_CELLS = 1.5  # 노드·점을 이 반경(칸 크기 배수) 안 꼭짓점에 잇는다
ATTACH_MAX = 8
LINK_CELLS = 0.75  # 같은 높이로 맞닿은 두 면의 경계 꼭짓점을 이 거리(칸 크기 배수) 안이면 잇는다
S_BIT, E_BIT, N_BIT, W_BIT = 1, 2, 4, 8
_ASTAR_MODES = {k for k, v in MODE_SPEC.items() if v["base"] == "lengthM" and min(v["mult"].values()) >= 1.0}


# ---------- 비트 묶음 ----------


def encode_bits(codes: list[int]) -> str:
    """칸마다 4 비트 → 한 바이트에 두 칸(앞 칸이 낮은 4 비트) → base64."""
    b = bytearray((len(codes) + 1) // 2)
    for k, c in enumerate(codes):
        b[k // 2] |= (c & 15) << (4 * (k % 2))
    return base64.b64encode(bytes(b)).decode("ascii")


def decode_bits(s: str, n: int) -> list[int]:
    b = base64.b64decode(s)
    return [(b[k // 2] >> (4 * (k % 2))) & 15 for k in range(n)]


# ---------- 면 만들기(가져올 때) ----------


def _geom(polygons: list) -> MultiPolygon:
    parts = []
    for p in polygons:
        g = Polygon(p[0], p[1:])
        parts.append(g if g.is_valid else g.buffer(0))
    return unary_union(parts)


def _frame(geom, top: dict) -> tuple[list[float], list[float]]:
    """(원점, 축 단위 벡터). 원점은 축 좌표계에서 면의 최소 모서리."""
    if "profile" in top:
        dx, dy = top["profile"]["direction"]
    else:
        r = geom.minimum_rotated_rectangle
        c = list(r.exterior.coords) if r.geom_type == "Polygon" else [(0, 0), (1, 0)]
        a, b, d = c[0], c[1], c[2] if len(c) > 2 else c[1]
        e1, e2 = (b[0] - a[0], b[1] - a[1]), (d[0] - b[0], d[1] - b[1])
        dx, dy = e1 if math.hypot(*e1) >= math.hypot(*e2) else e2
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    if ux < 0 or (ux == 0 and uy < 0):  # 같은 축은 같은 방향으로(결정적)
        ux, uy = -ux, -uy
    ux, uy = round(ux, 6), round(uy, 6)
    xy = shapely.get_coordinates(geom.boundary)
    a = xy[:, 0] * ux + xy[:, 1] * uy
    b = -xy[:, 0] * uy + xy[:, 1] * ux
    a0, b0 = math.floor(a.min() * 100) / 100, math.floor(b.min() * 100) / 100
    origin = [round(a0 * ux - b0 * uy, 2), round(a0 * uy + b0 * ux, 2)]
    return origin, [ux, uy]


def _extent(geom, origin, axis, cell) -> tuple[int, int]:
    ux, uy = axis
    xy = shapely.get_coordinates(geom.boundary) - np.array(origin)
    a = xy[:, 0] * ux + xy[:, 1] * uy
    b = -xy[:, 0] * uy + xy[:, 1] * ux
    return max(1, math.ceil(a.max() / cell - 1e-9)), max(1, math.ceil(b.max() / cell - 1e-9))


def _grid_xy(origin, axis, cell, i, j):
    """격자 좌표 (i, j)(칸 단위, 소수 가능) → 세계 좌표. 서버·앱이 같은 식으로 계산한다."""
    ux, uy = axis
    return origin[0] + cell * (i * ux - j * uy), origin[1] + cell * (i * uy + j * ux)


def _tri_codes(geom, origin, axis, cell, cols, rows) -> list[int]:
    """칸마다 4 비트: 면 안에 통째로 드는 세모."""
    ii, jj = np.meshgrid(np.arange(cols), np.arange(rows))
    ii, jj = ii.ravel().astype(float), jj.ravel().astype(float)
    corners = {
        "00": _grid_xy(origin, axis, cell, ii, jj),
        "10": _grid_xy(origin, axis, cell, ii + 1, jj),
        "11": _grid_xy(origin, axis, cell, ii + 1, jj + 1),
        "01": _grid_xy(origin, axis, cell, ii, jj + 1),
        "m": _grid_xy(origin, axis, cell, ii + 0.5, jj + 0.5),
    }
    region = geom.buffer(COVER_TOL_M)
    shapely.prepare(region)
    codes = np.zeros(len(ii), dtype=int)
    for bit, (p, q) in ((S_BIT, ("00", "10")), (E_BIT, ("10", "11")), (N_BIT, ("11", "01")), (W_BIT, ("01", "00"))):
        ring = np.stack(
            [
                np.stack(corners[p], axis=1),
                np.stack(corners[q], axis=1),
                np.stack(corners["m"], axis=1),
                np.stack(corners[p], axis=1),
            ],
            axis=1,
        )
        tris = shapely.polygons(ring)
        codes |= np.where(shapely.covers(region, tris), bit, 0)
    return codes.tolist()


def build_plane(key: str, polygons: list, top: dict, cell: float | None = None, sources: list | None = None) -> dict:
    """꾸러미 walkMeshes 항목 하나. cell 을 안 주면 CELL_M 으로 덮어 보고 덮인 비율이 MIN_COVER 밑이면 CELL_NARROW_M."""
    geom = _geom(polygons)
    origin, axis = _frame(geom, top)
    tried = [cell] if cell else [CELL_M, CELL_NARROW_M]
    for c in tried:
        cols, rows = _extent(geom, origin, axis, c)
        codes = _tri_codes(geom, origin, axis, c, cols, rows)
        cover = sum(bin(x).count("1") for x in codes) * c * c / 4 / geom.area if geom.area else 0.0
        if cover >= MIN_COVER:
            break
    return {
        "key": key,
        "sources": sources or [],
        "top": top,
        "origin": origin,
        "axis": axis,
        "cell": c,
        "cols": cols,
        "rows": rows,
        "tris": encode_bits(codes),
        "cover": round(cover, 3),
    }


def planes_from_package(pkg: dict, keys: list[str] | None = None) -> list[dict]:
    """꾸러미의 걷는 면(s:...)·열린 면(a:...)을 높이 함수가 같은 것끼리 합쳐 면 목록으로.
    keys 를 주면 그 면만(시제품 구역). 지하 주차장 통로 면 같은 열린 면도 평평한 면으로 다룬다."""
    groups: dict[str, dict] = {}
    for s in pkg.get("walkSurfaces", []):
        if keys is None or s["key"] in keys:
            g = groups.setdefault(repr(sorted(s["top"].items())), {"top": s["top"], "polys": [], "src": []})
            g["polys"] += s["polygons"]
            g["src"].append(s["key"])
    for a in pkg.get("walkAreas", []):
        if a["z"] is None or (keys is not None and a["key"] not in keys):
            continue
        top = {"z": round(float(a["z"]), 2)}
        g = groups.setdefault(repr(sorted(top.items())), {"top": top, "polys": [], "src": []})
        g["polys"].append([a["polygon"]])
        g["src"].append(a["key"])
    out = []
    for g in sorted(groups.values(), key=lambda g: g["src"][0]):
        out.append({"key": f"m:{g['src'][0]}", "top": g["top"], "polygons": g["polys"], "sources": g["src"]})
    return out


# ---------- 그물 다시 만들기(서버 읽을 때·앱) ----------


class Plane:
    """walkMeshes 항목 + 면 외곽(줄 당기기용) → 꼭짓점·간선."""

    def __init__(self, entry: dict, polygons: list):
        self.key, self.top, self.cell = entry["key"], entry["top"], float(entry["cell"])
        self.origin, self.axis = entry["origin"], entry["axis"]
        self.cols, self.rows = entry["cols"], entry["rows"]
        self.codes = decode_bits(entry["tris"], self.cols * self.rows)
        self.region = _geom(polygons).buffer(COVER_TOL_M)
        shapely.prepare(self.region)
        self.sources = entry.get("sources", [])
        nc = (self.cols + 1) * (self.rows + 1)
        self._nc = nc
        edges = set()
        for k, c in enumerate(self.codes):
            if not c:
                continue
            i, j = k % self.cols, k // self.cols
            c00, c10 = j * (self.cols + 1) + i, j * (self.cols + 1) + i + 1
            c01, c11 = c00 + self.cols + 1, c10 + self.cols + 1
            m = nc + k
            for bit, p, q in ((S_BIT, c00, c10), (E_BIT, c10, c11), (N_BIT, c11, c01), (W_BIT, c01, c00)):
                if c & bit:
                    edges.update({(min(p, q), max(p, q)), (min(p, m), max(p, m)), (min(q, m), max(q, m))})
        self.xyz: dict[int, tuple] = {}
        self.adj: dict[int, list] = defaultdict(list)
        for p, q in sorted(edges):
            a, b = self.vertex(p), self.vertex(q)
            d = math.dist(a, b)
            self.adj[p].append((q, d))
            self.adj[q].append((p, d))

    def z(self, x: float, y: float) -> float:
        return surface_z(self.top, x, y)

    def vertex(self, v: int) -> tuple:
        if v not in self.xyz:
            if v < self._nc:
                i, j = v % (self.cols + 1), v // (self.cols + 1)
            else:
                k = v - self._nc
                i, j = k % self.cols + 0.5, k // self.cols + 0.5
            x, y = _grid_xy(self.origin, self.axis, self.cell, i, j)
            self.xyz[v] = (x, y, self.z(x, y))
        return self.xyz[v]

    def local(self, x: float, y: float) -> tuple[float, float]:
        ux, uy = self.axis
        dx, dy = x - self.origin[0], y - self.origin[1]
        return (dx * ux + dy * uy) / self.cell, (-dx * uy + dy * ux) / self.cell

    def near_vertices(self, x: float, y: float, r: float) -> list[int]:
        a, b = self.local(x, y)
        k = math.ceil(r / self.cell) + 1
        out = []
        for j in range(math.floor(b) - k, math.floor(b) + k + 2):
            for i in range(math.floor(a) - k, math.floor(a) + k + 2):
                for v in (
                    (j * (self.cols + 1) + i) if 0 <= i <= self.cols and 0 <= j <= self.rows else None,
                    (self._nc + j * self.cols + i) if 0 <= i < self.cols and 0 <= j < self.rows else None,
                ):
                    if v is not None and v in self.adj and math.dist(self.vertex(v)[:2], (x, y)) <= r:
                        out.append(v)
        return out

    def covers(self, x: float, y: float) -> bool:
        return self.region.covers(Point(x, y))

    def sees(self, p, q) -> bool:
        if math.dist(p[:2], q[:2]) < 1e-9:
            return self.covers(p[0], p[1])
        return self.region.covers(LineString([p[:2], q[:2]]))

    def line(self, p, q) -> list:
        """p → q 직선을 SURFACE_SAMPLE_M 마다 면 높이로 찍은 3D 선(두 끝은 주어진 높이)."""
        h = math.dist(p[:2], q[:2])
        k = max(1, math.ceil(h / SURFACE_SAMPLE_M))
        pts = [list(p)]
        for s in range(1, k):
            t = s / k
            x, y = p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
            pts.append([x, y, self.z(x, y)])
        pts.append(list(q))
        return pts


def _links(planes: list[Plane]) -> list[tuple]:
    """같은 높이(차 STEP_OK_M 안)로 맞닿거나 겹친 두 면: 한 면의 경계 꼭짓점을 다른 면의 가까운 꼭짓점에 잇는다."""
    out = []
    for ai, A in enumerate(planes):
        edge_band = None
        for bi, B in enumerate(planes):
            gap = max(A.cell, B.cell)
            if bi == ai or not _bbox_near(A.region.bounds, B.region.bounds, gap):
                continue
            if not A.region.buffer(gap).intersects(B.region):
                continue
            if edge_band is None:
                edge_band = A.region.boundary.buffer(A.cell)
                shapely.prepare(edge_band)
            r = LINK_CELLS * max(A.cell, B.cell)
            for v in A.adj:
                p = A.vertex(v)
                if not edge_band.covers(Point(p[:2])):
                    continue
                best = None
                for w in B.near_vertices(p[0], p[1], r):
                    q = B.vertex(w)
                    if abs(p[2] - q[2]) <= STEP_OK_M and (best is None or math.dist(p, q) < best[1]):
                        best = (w, math.dist(p, q))
                if best:
                    out.append(((ai, v), (bi, best[0]), best[1]))
    return out


def _bbox_near(a, b, gap: float) -> bool:
    return a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]


class MeshRouter:
    """기존 길 그래프(MapIndex) + 보행 그물. 그물이 덮은 면의 면 가상 간선(y:·x:)은 쓰지 않는다."""

    def __init__(self, idx: MapIndex, entries: list[dict]):
        self.idx = idx
        polys = {s["key"]: s["polygons"] for s in idx.pkg.get("walkSurfaces", [])}
        polys.update({a["key"]: [[a["polygon"]]] for a in idx.pkg.get("walkAreas", [])})
        self.planes = [Plane(e, [p for k in e["sources"] for p in polys[k]]) for e in entries]
        self.covered = {k for e in entries for k in e["sources"]}
        self.links = defaultdict(list)
        for a, b, d in _links(self.planes):
            self.links[a].append((b, d))
            self.links[b].append((a, d))
        self.attach = defaultdict(list)  # 노드 key → [((면, 꼭짓점), 길이)] (양방향)
        for n in idx.nodes.values():
            if n["location"] != "outdoor":
                continue
            for pi, (v, d) in self._attach_point((n["x"], n["y"], n["z"])):
                self.attach[n["key"]].append(((pi, v), d))
                self.attach[(pi, v)].append((n["key"], d))
        self._factor_cache: dict = {}

    def _attach_point(self, p) -> list[tuple]:
        """점 p 가 올라선 면(높이 차 AREA_DZ_M 안, 면 안)의 꼭짓점 가운데 직선으로 보이는 가까운 것들."""
        out = []
        for pi, P in enumerate(self.planes):
            if not _bbox_near(P.region.bounds, (p[0], p[1], p[0], p[1]), 0.5):
                continue
            if abs(p[2] - P.z(p[0], p[1])) > AREA_DZ_M or P.region.distance(Point(p[:2])) > 0.5:
                continue
            cands = sorted(
                (math.dist(P.vertex(v)[:2], p[:2]), v) for v in P.near_vertices(p[0], p[1], ATTACH_CELLS * P.cell)
            )
            k = 0
            for d2, v in cands:
                if k >= ATTACH_MAX:
                    break
                q = P.vertex(v)
                if P.sees(p, q):
                    out.append((pi, (v, math.dist(p, q))))
                    k += 1
        return out

    def factor(self, pi: int, mode: str) -> float | None:
        """면의 길이 1 m 비용(방식별). 열린 면 가상 간선과 같은 규칙을 1 m 짜리 가상 간선으로 잰다(오르내림은 평균)."""
        ck = (pi, mode)
        if ck not in self._factor_cache:
            P = self.planes[pi]
            v = next(iter(sorted(P.adj)))
            x, y, z = P.vertex(v)
            ux, uy = P.axis
            a = {
                "key": "q:a",
                "location": "outdoor",
                "buildingKey": None,
                "floorKey": None,
                "kind": "point",
                "x": x,
                "y": y,
                "z": z,
                "estimated": True,
            }
            b = {**a, "key": "q:b", "x": x + ux, "y": y + uy, "z": P.z(x + ux, y + uy)}
            src = P.sources[0]
            if src.startswith("s:"):
                e = self.idx._surface_edge(src, a, b)
            else:
                e = self.idx._area_edge(src, a, b)
            view = self.idx._with({"q:a": a, "q:b": b}, [e])
            cs = [view._cost(e, mode, False, True), view._cost(e, mode, True, True)]
            # 이 방식에서 못 지나는 면(휠체어: 너무 가파름 등)은 None
            self._factor_cache[ck] = None if None in cs else sum(cs) / 2 / max(e["lengthM"], 1e-6)
        return self._factor_cache[ck]

    def _xyz(self, s, tmp):
        if isinstance(s, tuple):
            return self.planes[s[0]].vertex(s[1])
        n = tmp.get(s) or self.idx.nodes[s]
        return (n["x"], n["y"], n["z"])

    def route_points(self, start, goal, mode: str = "shortest") -> dict | None:
        """출발·도착 = 노드 key 또는 점 (x, y, z). 점은 노드 자리면 그 노드, 아니면 올라선 면의 꼭짓점에 잇는다."""
        t0 = time.perf_counter()
        tmp, extra = {}, defaultdict(list)
        ends = []
        for key, spec in (("q:from", start), ("q:to", goal)):
            if isinstance(spec, str):
                ends.append(spec)
                continue
            x, y, z = (float(v) for v in spec)
            at = self.idx.node_at(x, y, z)
            if at:
                ends.append(at["key"])
                continue
            hits = self._attach_point((x, y, z))
            if not hits:
                n = self.idx.nearest_node(x, y, z)
                if not n:
                    return None
                ends.append(n["key"])
                continue
            pi0 = hits[0][0]
            tmp[key] = {"key": key, "x": x, "y": y, "z": self.planes[pi0].z(x, y), "location": "outdoor"}
            for pi, (v, d) in hits:
                extra[key].append(((pi, v), d))
                extra[(pi, v)].append((key, d))
            ends.append(key)
        start_k, goal_k = ends
        res = self._search(start_k, goal_k, mode, tmp, extra)
        if res is None:
            return None
        states, steps = res
        self.mode = mode
        out = self._describe(states, steps, tmp)
        out["mode"] = mode
        out["timeMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def _neighbors(self, u, mode, tmp, extra):
        """(다음 상태, 비용, 단계 정보). 단계 정보 = ("edge", 길) | ("mesh", 면) | ("link", None) | ("attach", 면)."""
        if isinstance(u, tuple):
            pi, v = u
            f = self.factor(pi, mode)
            if f is None:
                return
            for w, d in self.planes[pi].adj[v]:
                yield (pi, w), d * f, ("mesh", pi)
            for b, d in self.links.get(u, []):
                if self.factor(b[0], mode) is not None:
                    yield b, d * f, ("link", pi)
        else:
            if u in self.idx.out and u not in tmp:
                for e, v in self.idx.out[u]:
                    if e["kind"] == "open_area" and (
                        e.get("surfaceKey") in self.covered or self._area_of(e) in self.covered
                    ):
                        continue
                    yield v, e, ("edge", e)
        for b, d in [*self.attach.get(u, []), *extra.get(u, [])]:
            pi = b[0] if isinstance(b, tuple) else u[0]
            f = self.factor(pi, mode)
            if f is not None:
                yield b, d * f, ("attach", pi)

    @staticmethod
    def _area_of(e: dict) -> str | None:
        """열린 면 가상 간선 key x:<면 key>:<노드>:<노드> 의 면 key(면 key 는 a:<번호>)."""
        return ":".join(e["key"].split(":")[1:3]) if e["key"].startswith("x:") else None

    def _search(self, start, goal, mode, tmp, extra):
        gx = self._xyz(goal, tmp)
        use_h = mode in _ASTAR_MODES

        def h(s):
            return math.dist(self._xyz(s, tmp), gx) if use_h else 0.0

        s0 = (start, False)
        dist = {s0: 0.0}
        prev = {}
        pq = [(h(start), 0, 0.0, start, False)]
        tie = 0
        end = None
        self.visited = 0
        while pq:
            _, _, d, u, riding = heapq.heappop(pq)
            if d > dist.get((u, riding), math.inf):
                continue
            self.visited += 1
            if u == goal:
                end = (u, riding)
                break
            for v, c, info in self._neighbors(u, mode, tmp, extra):
                if info[0] == "edge":
                    e = c
                    c = self.idx._cost(e, mode, reverse=v == e["from"], boarding=not riding)
                    if c is None:
                        continue
                    nr = e["kind"] == "elevator"
                else:
                    nr = False
                sv = (v, nr)
                nd = d + c
                if nd < dist.get(sv, math.inf):
                    dist[sv] = nd
                    prev[sv] = ((u, riding), info)
                    tie += 1
                    heapq.heappush(pq, (nd + h(v), tie, nd, v, nr))
        if end is None:
            return None
        states, steps = [end[0]], []
        cur = end
        while cur != s0:
            p, info = prev[cur]
            steps.append(info)
            states.append(p[0])
            cur = p
        states.reverse()
        steps.reverse()
        self.cost = dist[end]
        return states, steps

    def _describe(self, states, steps, tmp) -> dict:
        """경로를 3D 선으로. 같은 면 위 연속 구간(그물 간선·잇는 간선)은 면 안 직선으로 당겨 편다."""
        geometry, edge_keys, raw = [], [], 0
        i = 0
        while i < len(steps):
            kind, val = steps[i]
            if kind == "edge":
                e = val
                g = e["geometry"] if e["from"] == states[i] else list(reversed(e["geometry"]))
                geometry.extend(g if not geometry else g[1:])
                edge_keys.append(e["key"])
                i += 1
                continue
            # 그물 구간: 이음 간선으로 넘어가는 같은 높이 면까지, 비용(1 m 당)이 같은 면이면 한 구간으로 당겨 편다
            f0 = self.factor(val, self.mode)
            j = i
            while j < len(steps) and steps[j][0] != "edge" and self.factor(steps[j][1], self.mode) == f0:
                j += 1
            run = _Run([self.planes[k] for k in sorted({steps[m][1] for m in range(i, j)})])
            pts = [self._xyz(s, tmp) for s in states[i : j + 1]]
            raw += len(pts)
            pulled = _pull(run, pts)
            for a, b in zip(pulled, pulled[1:]):
                seg = run.line(a, b)
                geometry.extend(seg if not geometry else seg[1:])
            i = max(j, i + 1)
        geometry = [[round(c, 2) for c in p] for p in geometry]
        return {
            "distanceM": round(sum(math.dist(a, b) for a, b in zip(geometry, geometry[1:])), 2),
            "cost": round(self.cost, 2),
            "geometry": geometry,
            "edgeKeys": edge_keys,
            "meshVertices": raw,
            "visited": self.visited,
        }


class _Run:
    """한 구간에 이어 쓰는 면들(같은 높이로 이어진 면, 하나일 때가 많음). 줄 당기기용 보기·높이."""

    SAMPLE_M = 0.5  # 직선이 면을 넘어갈 때 높이 끊김을 이 간격으로 본다

    def __init__(self, planes: list[Plane]):
        self.planes = planes
        self.region = planes[0].region if len(planes) == 1 else unary_union([P.region for P in planes])
        shapely.prepare(self.region)

    def _z(self, x, y, prev):
        """(x, y) 를 덮는 면 가운데 높이가 prev 에 가장 가까운 면의 높이(덮는 면이 없으면 None)."""
        best = None
        for P in self.planes:
            if len(self.planes) == 1 or P.region.covers(Point(x, y)):
                z = P.z(x, y)
                if best is None or abs(z - prev) < abs(best - prev):
                    best = z
        return best

    def sees(self, p, q) -> bool:
        if math.dist(p[:2], q[:2]) > 1e-9 and not self.region.covers(LineString([p[:2], q[:2]])):
            return False
        if len(self.planes) == 1:
            return True
        k = max(1, math.ceil(math.dist(p[:2], q[:2]) / self.SAMPLE_M))
        prev = p[2]
        for s in range(1, k + 1):
            t = s / k
            z = self._z(p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]), prev)
            if z is None or abs(z - prev) > STEP_OK_M + 0.5 * self.SAMPLE_M:  # 턱 0.2 m + 50 % 기울기까지
                return False
            prev = z
        return True

    def line(self, p, q) -> list:
        """p → q 직선을 SURFACE_SAMPLE_M 마다 면 높이로 찍은 3D 선(두 끝은 주어진 높이)."""
        h = math.dist(p[:2], q[:2])
        k = max(1, math.ceil(h / SURFACE_SAMPLE_M))
        pts, prev = [list(p)], p[2]
        for s in range(1, k):
            t = s / k
            x, y = p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
            z = self._z(x, y, prev)
            prev = prev if z is None else z
            pts.append([x, y, prev])
        pts.append(list(q))
        return pts


def _pull(run: _Run, pts: list) -> list:
    """줄 당기기: 앞 점에서 면 안 직선으로 보이는 가장 먼 점으로 건너뛴다."""
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and not run.sees(pts[i], pts[j]):
            j -= 1
        out.append(pts[j])
        i = j
    return out


def package_size(entries: list[dict]) -> int:
    return len(json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
