"""보행 그물(아3): 걷는 면·길 띠를 정사각형 칸으로 덮고, 칸마다 가운데 꼭짓점으로 4등분한 세모로 길을 찾는다.

그물(mesh) 하나 = 격자 하나.
  · 면 그물(kind surface): 높이 함수(top)가 같은 걷는 면·열린 면을 합친 것. 축은 기운 면이면 기울기 방향,
    평평하면 최소 외접 사각형의 긴 변. 열린 면(a:..)은 높이 함수가 다른 걷는 면이 덮은 곳을 뺀다(걷는 면이 이김).
  · 길 띠(kind band): 면 밖 바깥 길을 길 폭(모르면 보행 2.0 m, 차와 같이 쓰는 길 3.0 m)만큼 띠로 덮은 것.
    높이는 길 중심선의 가장 가까운 점 높이.
  · 칸(한 변 cell m) = 세모 넷 S(아래 변)·E(오른 변)·N(위 변)·W(왼 변), 모두 칸 가운데 꼭짓점을 낀다.
    면(구멍 포함) 안에 통째로 드는 세모만 쓴다. 칸마다 4 비트(S=1, E=2, N=4, W=8).
  · 칸마다 비용 등급(classes[cls]): 지붕·조명·차량·기울기(길 띠는 그 길의 지표)로 정하고, 등급마다 방식별 1 m 비용
    (ppm 정수, None = 못 지나감)을 싣는다. 서버·앱이 같은 정수 비용으로 같은 길을 찾는다.

경로(서버 MeshRouter, 앱 src/route/mesh.ts 가 같은 계산):
  · 그래프 꼭짓점 = 세모 꼭짓점, 간선 = 쓸 수 있는 세모의 변. 간선 비용 = round(3D 길이 mm × ppm / 10⁶), 정수.
  · 빠르게: 그물을 8×8 칸 구역(block)으로 나누고, 구역 경계 입구·그물 사이 이음·꾸러미 노드를 관문으로 둔다.
    관문 사이 구역 안 최단 비용을 읽을 때 미리 계산해 두고, 경로는 (꾸러미 길 + 관문 표) 그래프에서 찾는다.
    찾은 뒤 지난 구역만 다시 풀어 꼭짓점 줄을 얻는다.
  · 줄 당기기: 꼭짓점 줄을 앞에서부터 "같은 비용 등급 세모만 지나는 직선"으로 보이는 가장 먼 점으로 건너뛰어 편다.
    같은 높이로 이어진 그물(이음)도 한 직선으로 넘는다(높이 끊김 검사).
꾸러미(walkMeshes)에는 세모를 나열하지 않고 그물마다 원점·축·칸 크기·칸 수·비트 묶음·등급만 싣는다.
"""

import base64
import heapq
import math
import time
from collections import defaultdict

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from mapdata import indicators as ind_mod
from mapdata.routing import AREA_DZ_M, MODE_SPEC, MODES, SURFACE_SAMPLE_M, edge_cost
from mapdata.surfaces import STEP_OK_M, surface_z

CELL_M = 2.0  # 바깥 넓은 면
CELL_NARROW_M = 1.0  # 좁은 면(2 m 칸으로 덮이는 비율이 MIN_COVER 밑)·좁은 길 띠
CELL_TINY_M = 0.5  # 1 m 칸으로도 TINY_COVER 밑만 덮이는 작은 면(Claude 결정 2026-10-23)
MIN_COVER = 0.85
TINY_COVER = 0.27
BAND_WIDE_M = 3.0  # 길 띠 폭이 이 이상이면 2 m 칸
BAND_WALK_M, BAND_SHARED_M = 2.0, 3.0  # 폭 모를 때 기본값
BAND_KINDS = {"walkway", "sidewalk", "crossing", "vehicle_road", "ramp"}
MIN_BAND_M2 = 1.0
AREA_CUT_DZ_M = 1.8  # 열린 면과 겹친 걷는 면 높이 차가 이 안이면 같은 바닥(걷는 면이 이김), 넘으면 위·아래 다른 층
COVER_TOL_M = 0.01  # 세모가 면 경계에서 이만큼 밖으로 나가도 안으로 본다(반올림 오차)
ATTACH_CELLS = 1.5  # 노드·점을 이 반경(칸 크기 배수) 안 꼭짓점에 잇는다
ATTACH_MAX = 8  # 그물 안 점: 직선으로 보이는 가까운 꼭짓점 최대 수
ATTACH_OUT_MAX = 2  # 그물 세모 밖 노드(문 등): 가까운 꼭짓점 수
LINK_CELLS = 0.75  # 같은 높이로 맞닿은 두 그물의 경계 꼭짓점을 이 거리(칸 크기 배수) 안이면 잇는다
BLOCK = 8  # 구역 한 변 칸 수
ENTRANCE_EVERY = 4  # 구역 경계 열린 줄에서 입구 간격(꼭짓점 수)
LOS_STEP = 0.25  # 직선 보기 표본 간격(칸 크기 배수)
PPM = 1_000_000
S_BIT, E_BIT, N_BIT, W_BIT = 1, 2, 4, 8
_ASTAR_MODES = {k for k, v in MODE_SPEC.items() if v["base"] == "lengthM" and min(v["mult"].values()) >= 1.0}


# ---------- 공용 계산(앱과 같은 식) ----------


def encode_bits(codes: list[int]) -> str:
    """칸마다 4 비트 → 한 바이트에 두 칸(앞 칸이 낮은 4 비트) → base64."""
    b = bytearray((len(codes) + 1) // 2)
    for k, c in enumerate(codes):
        b[k // 2] |= (c & 15) << (4 * (k % 2))
    return base64.b64encode(bytes(b)).decode("ascii")


def decode_bits(s: str, n: int) -> list[int]:
    b = base64.b64decode(s)
    return [(b[k // 2] >> (4 * (k % 2))) & 15 for k in range(n)]


def encode_runs(vals: list[int]) -> list[int]:
    out: list[int] = []
    for v in vals:
        if out and out[-2] == v:
            out[-1] += 1
        else:
            out += [v, 1]
    return out


def decode_runs(runs: list[int], n: int) -> list[int]:
    out: list[int] = []
    for k in range(0, len(runs), 2):
        out += [runs[k]] * runs[k + 1]
    return out[:n] + [0] * (n - len(out))


def mm(d: float) -> int:
    return math.floor(d * 1000 + 0.5)


def dist_mm(a, b) -> int:
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    return mm(math.sqrt(dx * dx + dy * dy + dz * dz))


def scaled(d_mm: int, ppm: int) -> int:
    return (d_mm * ppm + PPM // 2) // PPM


def line_z(geom: list, x: float, y: float) -> float:
    """선(3D) 위 (x, y) 에서 가장 가까운 점의 높이(같으면 앞 조각)."""
    best, z = math.inf, geom[0][2]
    for a, b in zip(geom, geom[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / l2))
        px, py = a[0] + t * dx, a[1] + t * dy
        d2 = (x - px) * (x - px) + (y - py) * (y - py)
        if d2 < best:
            best, z = d2, a[2] + t * (b[2] - a[2])
    return z


# ---------- 그물 만들기(가져올 때) ----------


def _geom(polygons: list):
    parts = []
    for p in polygons:
        g = Polygon(p[0], p[1:])
        parts.append(g if g.is_valid else g.buffer(0))
    return unary_union(parts)


def _frame(geom, top: dict | None) -> tuple[list[float], list[float]]:
    """(원점, 축 단위 벡터). 원점은 축 좌표계에서 면의 최소 모서리(cm 반올림)."""
    if top and "profile" in top:
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
            [np.stack(corners[k], axis=1) for k in (p, q, "m", p)],
            axis=1,
        )
        codes |= np.where(shapely.covers(region, shapely.polygons(ring)), bit, 0)
    return codes.tolist()


def _cover(codes, cell, area) -> float:
    return sum(bin(x).count("1") for x in codes) * cell * cell / 4 / area if area else 0.0


def _pieces(codes: list[int], cols: int, rows: int, min_vertices: int = 10) -> int:
    """세모 그래프의 덩어리 수(꼭짓점 min_vertices 개 미만 조각은 안 셈)."""
    nc = (cols + 1) * (rows + 1)
    parent = list(range(nc + cols * rows))

    def root(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    used = set()
    for k, c in enumerate(codes):
        if not c:
            continue
        i, j = k % cols, k // cols
        c00 = j * (cols + 1) + i
        c10, c01 = c00 + 1, c00 + cols + 1
        for bit, p, q in ((S_BIT, c00, c10), (E_BIT, c10, c01 + 1), (N_BIT, c01 + 1, c01), (W_BIT, c01, c00)):
            if c & bit:
                for v in (p, q):
                    parent[root(v)] = root(nc + k)
                    used.add(v)
                used.add(nc + k)
    size = defaultdict(int)
    for v in used:
        size[root(v)] += 1
    return sum(1 for n in size.values() if n >= min_vertices)


def build_grid(geom, top: dict | None, cells: list[float]) -> dict:
    """칸 크기 후보를 차례로 덮어 본다: 앞 후보의 덮은 비율이 문턱(MIN_COVER, 1 m → TINY_COVER) 밑이면 다음 후보.
    고른 칸으로 그물이 면 조각 수보다 많은 덩어리로 끊기면(좁은 목) 더 작은 칸을 덮어 보고 덜 끊기면 그것을 쓴다."""
    origin, axis = _frame(geom, top)
    parts = sum(1 for g in getattr(geom, "geoms", [geom]) if g.area >= 2.0)
    grids = []
    for n, c in enumerate(cells):
        cols, rows = _extent(geom, origin, axis, c)
        codes = _tri_codes(geom, origin, axis, c, cols, rows)
        grids.append({"origin": origin, "axis": axis, "cell": c, "cols": cols, "rows": rows, "codes": codes})
        grids[-1]["cover"] = _cover(codes, c, geom.area)
        if n + 1 == len(cells) or grids[-1]["cover"] >= (MIN_COVER if c > CELL_NARROW_M else TINY_COVER):
            break
    g = grids[-1]
    if g["cell"] > CELL_TINY_M:
        pieces = _pieces(g["codes"], g["cols"], g["rows"])
        if pieces > parts:
            c = CELL_TINY_M if g["cell"] <= CELL_NARROW_M else CELL_NARROW_M
            cols, rows = _extent(geom, origin, axis, c)
            codes = _tri_codes(geom, origin, axis, c, cols, rows)
            if _pieces(codes, cols, rows) < pieces:
                g = {"origin": origin, "axis": axis, "cell": c, "cols": cols, "rows": rows, "codes": codes}
                g["cover"] = _cover(codes, c, geom.area)
    return g


def plane_groups(pkg: dict, keys: list[str] | None = None) -> list[dict]:
    """걷는 면(s:..)·열린 면(a:..)을 높이 함수가 같은 것끼리 합친 면 목록. 열린 면은 높이 함수가 다른 걷는 면이
    덮은 곳을 뺀다. keys 를 주면 그 면만."""
    surfs = [s for s in pkg.get("walkSurfaces", []) if keys is None or s["key"] in keys]
    groups: dict[str, dict] = {}
    for s in surfs:
        g = groups.setdefault(repr(sorted(s["top"].items())), {"top": s["top"], "parts": [], "src": []})
        g["parts"].append(_geom(s["polygons"]))
        g["src"].append(s)
    for a in pkg.get("walkAreas", []):
        if a["z"] is None or (keys is not None and a["key"] not in keys):
            continue
        top = {"z": round(float(a["z"]), 2)}
        tk = repr(sorted(top.items()))
        geom = _geom([[a["polygon"]]])
        other = []
        for s in surfs:
            if repr(sorted(s["top"].items())) == tk:
                continue
            g = _geom(s["polygons"])
            cut = g.intersection(geom)
            if cut.is_empty:
                continue
            xy = shapely.get_coordinates(cut)
            if min(abs(surface_z(s["top"], x, y) - top["z"]) for x, y in xy) <= AREA_CUT_DZ_M:
                other.append(g)
        if other:
            geom = geom.difference(unary_union(other))
        if geom.area < MIN_BAND_M2:
            continue
        g = groups.setdefault(tk, {"top": top, "parts": [], "src": []})
        g["parts"].append(geom)
        g["src"].append(a)
    out = []
    for g in sorted(groups.values(), key=lambda g: g["src"][0]["key"]):
        out.append(
            {"key": f"m:{g['src'][0]['key']}", "top": g["top"], "geom": unary_union(g["parts"]), "src": g["src"]}
        )
    return out


class _Structs:
    """칸 가운데 점의 지붕·조명(가져올 때, 구조물 파일). indicators.annotate 와 같은 규칙."""

    def __init__(self, S: dict | None, buildings: list[dict]):
        self.S = S
        if S is None:
            return
        self.grids = {}
        for name in ("overhead", "voids"):
            g = ind_mod._Grid()
            for it in S[name]:
                g.add(it["bbox"], it)
            self.grids[name] = g
        self.lamps = ind_mod._Grid()
        for lp in S["lamps"]:
            self.lamps.add((lp["x"], lp["y"], lp["x"], lp["y"]), lp)
        self.blds = ind_mod._Grid()
        for b in buildings:
            if b.get("footprint"):
                self.blds.add(ind_mod._bbox(b["footprint"]), b["footprint"])

    def covered(self, x, y, z) -> bool | None:
        if self.S is None:
            return None
        if any(
            v["fromM"] is not None
            and v["fromM"] - 0.5 <= z <= v["toM"] - ind_mod.HEADROOM_M
            and ind_mod._inside(x, y, v["ring"])
            for v in self.grids["voids"].near(x, y)
        ):
            return True
        if any(
            o["fromM"] is not None
            and z + ind_mod.HEADROOM_M <= o["fromM"] <= z + ind_mod.OVERHEAD_MAX_M
            and ind_mod._inside(x, y, o["ring"])
            for o in self.grids["overhead"].near(x, y)
        ):
            return True
        return any(ind_mod._inside(x, y, r) and ind_mod._dist_ring(x, y, r) > 1.0 for r in self.blds.near(x, y))

    def lit(self, x, y, z) -> bool | None:
        if self.S is None:
            return None
        r = ind_mod.LAMP_RADIUS_M
        return (
            any(
                math.hypot(lp["x"] - x, lp["y"] - y) <= r and (lp["z"] is None or abs(lp["z"] - z) <= ind_mod.LAMP_DZ_M)
                for lp in self.lamps.near(x, y, r)
            )
            or None
        )


def _factors(e: dict, i: dict) -> list[int | None]:
    """길 하나(1 m 가상 길 또는 실제 길)의 방식별 1 m 비용(ppm). 오가는 두 방향 평균. 못 지나가면 None."""
    out = []
    for m in MODES:
        cs = [edge_cost(e, i, m, rev, False) for rev in (False, True)]
        out.append(None if None in cs else round((cs[0] + cs[1]) / 2 / e["lengthM"] * PPM))
    return out


def surface_class(covered, lit, shared: bool, slope_pct: float) -> dict:
    """면 칸 등급: 3D 1 m 짜리 열린 면 가상 길의 지표로 방식별 비용을 낸다(기울기는 칸 가운데 경사 방향)."""
    g = slope_pct / 100
    h = 1 / math.sqrt(1 + g * g)
    i = {
        "walkS": ind_mod._tobler_s(h, g * h),
        "walkSBack": ind_mod._tobler_s(h, -g * h),
        "wheelchair": ind_mod.wheelchair_derived("open_area", slope_pct, None, "unknown")[0],
        "coveredRatio": None if covered is None else float(covered),
        "litRatio": 1.0 if lit else None,
        "vehicle": "shared" if shared else "none",
        "indoor": False,
    }
    e = {"key": "", "kind": "open_area", "pedestrianAccess": "allowed", "lengthM": 1.0}
    return {
        "f": _factors(e, i),
        "coveredRatio": i["coveredRatio"],
        "litRatio": i["litRatio"],
        "vehicle": i["vehicle"],
        "slopePct": slope_pct,
    }


def band_class(e: dict) -> dict:
    """길 띠 등급 = 그 길의 1 m 비용(길 지표 그대로)."""
    i = e["indicators"]
    return {
        "edge": e["key"],
        "f": _factors(e, i),
        "coveredRatio": i.get("coveredRatio"),
        "litRatio": i.get("litRatio"),
        "vehicle": i.get("vehicle"),
        "slopePct": i.get("maxSlopePct"),
    }


def _slope_pct(zf, x, y) -> float:
    gx = zf(x + 0.5, y) - zf(x - 0.5, y)
    gy = zf(x, y + 0.5) - zf(x, y - 0.5)
    return round(math.hypot(gx, gy) * 100, 1)


def _shared_source(src: dict) -> bool:
    if "polygons" in src:  # 걷는 면: 아스팔트이거나 빗금 차도·주차면
        return src.get("material") == "asphalt" or any(w in (src.get("name") or "") for w in ("차도", "주차"))
    return src.get("kind") == "parking_aisle"


def _entry(key, kind, sources, name, area_kind, grid, classes, cls, **extra) -> dict:
    return {
        "key": key,
        "kind": kind,
        "sources": sources,
        "name": name,
        "areaKind": area_kind,
        **extra,
        "origin": grid["origin"],
        "axis": grid["axis"],
        "cell": grid["cell"],
        "cols": grid["cols"],
        "rows": grid["rows"],
        "tris": encode_bits(grid["codes"]),
        "cover": round(grid["cover"], 3),
        "classes": classes,
        "cls": encode_runs(cls),
    }


def _cell_classes(grid, zf, cls_of) -> tuple[list[dict], list[int]]:
    """칸마다 등급 번호(쓰는 칸만, 빈 칸은 앞 칸 번호로 이어 줄을 줄인다)."""
    classes, index, cls = [], {}, []
    for k, c in enumerate(grid["codes"]):
        if not c:
            cls.append(cls[-1] if cls else 0)
            continue
        i, j = k % grid["cols"], k // grid["cols"]
        x, y = _grid_xy(grid["origin"], grid["axis"], grid["cell"], i + 0.5, j + 0.5)
        c = cls_of(x, y, zf(x, y))
        ck = repr(sorted(c.items()))
        if ck not in index:
            index[ck] = len(classes)
            classes.append(c)
        cls.append(index[ck])
    return classes, cls


def build_entries(
    content: dict, structs: dict | None = None, keys: list[str] | None = None, bands: bool = True
) -> list[dict]:
    """꾸러미(또는 transform 내용) → walkMeshes 항목들: 면 그물 + 길 띠."""
    st = _Structs(structs, content.get("buildings", []))
    out = []
    planes = plane_groups(content, keys)
    for p in planes:
        grid = build_grid(p["geom"], p["top"], [CELL_M, CELL_NARROW_M, CELL_TINY_M])
        zf = lambda x, y, top=p["top"]: surface_z(top, x, y)  # noqa: E731
        shared_geoms = [
            _geom(src["polygons"]) if "polygons" in src else _geom([[src["polygon"]]])
            for src in p["src"]
            if _shared_source(src)
        ]
        for g in shared_geoms:
            shapely.prepare(g)

        def cls_of(x, y, z, shared_geoms=shared_geoms, zf=zf):
            pt = shapely.Point(x, y)
            shared = any(g.covers(pt) for g in shared_geoms)
            return surface_class(st.covered(x, y, z), st.lit(x, y, z), shared, _slope_pct(zf, x, y))

        classes, cls = _cell_classes(grid, zf, cls_of)
        first = p["src"][0]
        out.append(
            _entry(
                p["key"],
                "surface",
                [s["key"] for s in p["src"]],
                first.get("name") or "",
                "surface" if "polygons" in first else first["kind"],
                grid,
                classes,
                cls,
                top=p["top"],
            )
        )
    if bands and keys is None:
        out += _bands(content, planes)
    return out


def _bands(content: dict, planes: list[dict]) -> list[dict]:
    nodes = {n["key"]: n for n in content["nodes"]}
    out = []
    for e in sorted(content["edges"], key=lambda e: e["key"]):
        if (
            e["kind"] not in BAND_KINDS
            or e["pedestrianAccess"] == "prohibited"
            or e.get("direction", "both") != "both"  # 한 방향 길은 띠로 덮지 않는다(거꾸로 못 가게)
            or any(nodes[k]["location"] != "outdoor" for k in (e["from"], e["to"]))
            or not e.get("indicators")
        ):
            continue
        i = e["indicators"]
        width = (
            i.get("minWidthM") or e.get("widthM") or (BAND_SHARED_M if i.get("vehicle") == "shared" else BAND_WALK_M)
        )
        line = LineString([p[:2] for p in e["geometry"]])
        if line.length <= 0:
            continue
        geom = line.buffer(width / 2)
        # 같은 높이 면이 덮은 곳은 면 그물이 맡는다
        cut = []
        for p in planes:
            if not p["geom"].intersects(geom):
                continue
            inside = [q for q in e["geometry"] if p["geom"].buffer(0.5).covers(shapely.Point(q[0], q[1]))]
            pts = inside or [e["geometry"][len(e["geometry"]) // 2]]
            if any(abs(q[2] - surface_z(p["top"], q[0], q[1])) <= AREA_DZ_M for q in pts):
                cut.append(p["geom"])
        if cut:
            geom = geom.difference(unary_union(cut))
        if geom.area < MIN_BAND_M2:
            continue
        cells = [CELL_M, CELL_NARROW_M, CELL_TINY_M] if width >= BAND_WIDE_M else [CELL_NARROW_M, CELL_TINY_M]
        grid = build_grid(geom, None, cells)
        if not any(grid["codes"]):
            continue
        cl = band_class(e)
        out.append(
            _entry(
                f"m:r:{e['key']}",
                "band",
                [e["key"]],
                e["name"] or "",
                e["kind"],
                grid,
                [cl],
                [0] * len(grid["codes"]),
                edge=e["key"],
                widthM=round(float(width), 2),
            )
        )
    return out


# ---------- 그물 다시 만들기(서버 읽을 때·앱) ----------


class Mesh:
    """walkMeshes 항목 하나 → 꼭짓점·간선·구역. 정수 비용."""

    def __init__(self, entry: dict, edges: dict):
        self.key, self.kind = entry["key"], entry.get("kind", "surface")
        self.entry = entry
        self.cell = float(entry["cell"])
        self.ox, self.oy = entry["origin"]
        self.ux, self.uy = entry["axis"]
        self.cols, self.rows = entry["cols"], entry["rows"]
        cols, rows = self.cols, self.rows
        n = cols * rows
        self.codes = decode_bits(entry["tris"], n)
        self.cls = decode_runs(entry["cls"], n)
        self.classes = entry["classes"]
        nm = len(MODES)
        self.cf = [[c["f"][m] for c in self.classes] for m in range(nm)]  # 방식 → 등급 → ppm|None
        self.top = entry.get("top")
        self.line = edges[entry["edge"]]["geometry"] if entry.get("edge") else None
        nc = (cols + 1) * (rows + 1)
        self.nc = nc
        nv = nc + n
        # 꼭짓점 좌표
        xs, ys, zs = [0.0] * nv, [0.0] * nv, [0.0] * nv
        cell, ox, oy, ux, uy = self.cell, self.ox, self.oy, self.ux, self.uy
        for v in range(nv):
            if v < nc:
                i, j = v % (cols + 1), v // (cols + 1)
            else:
                k = v - nc
                i, j = k % cols + 0.5, k // cols + 0.5
            x = ox + cell * (i * ux - j * uy)
            y = oy + cell * (i * uy + j * ux)
            xs[v], ys[v], zs[v] = x, y, self.z(x, y)
        self.xs, self.ys, self.zs = xs, ys, zs
        # 간선: 칸 순서, 세모 S·E·N·W, 변 (p,q)·(p,m)·(q,m) 처음 나온 순서로 번호
        eid: dict = {}
        ea, eb, ecells = [], [], []
        tri_n = [0] * nv
        for k in range(n):
            c = self.codes[k]
            if not c:
                continue
            i, j = k % cols, k // cols
            c00 = j * (cols + 1) + i
            c10, c01 = c00 + 1, c00 + cols + 1
            c11 = c01 + 1
            m = nc + k
            for bit, p, q in ((S_BIT, c00, c10), (E_BIT, c10, c11), (N_BIT, c11, c01), (W_BIT, c01, c00)):
                if not c & bit:
                    continue
                tri_n[p] += 1
                tri_n[q] += 1
                tri_n[m] += 1
                for a, b in ((p, q), (p, m), (q, m)):
                    key = (a, b) if a < b else (b, a)
                    x = eid.get(key)
                    if x is None:
                        eid[key] = len(ea)
                        ea.append(key[0])
                        eb.append(key[1])
                        ecells.append([k])
                    elif ecells[x][-1] != k:
                        ecells[x].append(k)
        self.ea, self.eb, self.eid = ea, eb, eid
        ne = len(ea)
        self.edist = [dist_mm((xs[a], ys[a], zs[a]), (xs[b], ys[b], zs[b])) for a, b in zip(ea, eb)]
        nbx = (cols + BLOCK - 1) // BLOCK
        self.nbx = nbx
        self.eblocks = []
        self.block_cells = defaultdict(list)  # 구역 → 쓰는 칸
        for k in range(n):
            if self.codes[k]:
                self.block_cells[(k // cols) // BLOCK * nbx + (k % cols) // BLOCK].append(k)
        for x in range(ne):
            self.eblocks.append(sorted({(k // cols) // BLOCK * nbx + (k % cols) // BLOCK for k in ecells[x]}))
        self.ecells = ecells
        self._eppm: dict = {}
        self._ecost: dict = {}
        self.adj: list[list[int]] = [[] for _ in range(nv)]  # 꼭짓점 → 간선 번호(번호 순)
        for x in range(ne):
            self.adj[ea[x]].append(x)
            self.adj[eb[x]].append(x)
        self.vblocks: list[list[int]] = [[] for _ in range(nv)]  # 꼭짓점 → 속한 구역
        for v in range(nv):
            bs = set()
            for x in self.adj[v]:
                bs.update(self.eblocks[x])
            self.vblocks[v] = sorted(bs)
        self.rim = [v for v in range(nv) if tri_n[v] and tri_n[v] < (8 if v < nc else 4)]
        # 덩어리 번호(간선으로 이어진 꼭짓점끼리, 번호 작은 꼭짓점부터)
        self.comp = [-1] * nv
        nxt = 0
        for v0 in range(nv):
            if self.comp[v0] >= 0 or not self.adj[v0]:
                continue
            self.comp[v0] = nxt
            stack = [v0]
            while stack:
                u = stack.pop()
                for x in self.adj[u]:
                    w = eb[x] if ea[x] == u else ea[x]
                    if self.comp[w] < 0:
                        self.comp[w] = nxt
                        stack.append(w)
            nxt += 1
        bb = [(xs[v], ys[v]) for v in range(nv) if self.adj[v]]
        self.bbox = (
            (min(p[0] for p in bb), min(p[1] for p in bb), max(p[0] for p in bb), max(p[1] for p in bb))
            if bb
            else (0.0, 0.0, -1.0, -1.0)
        )

    def z(self, x: float, y: float) -> float:
        return surface_z(self.top, x, y) if self.line is None else line_z(self.line, x, y)

    def eppm(self, mi: int) -> list:
        """방식 → 간선마다 1 m 계수(닿는 칸 등급 가운데 가장 낮은 것, ppm) 또는 None."""
        if mi not in self._eppm:
            f, cls = self.cf[mi], self.cls
            out = []
            for cells in self.ecells:
                best = None
                for k in cells:
                    v = f[cls[k]]
                    if v is not None and (best is None or v < best):
                        best = v
                out.append(best)
            self._eppm[mi] = out
        return self._eppm[mi]

    def cost(self, mi: int) -> list:
        """방식 → 간선마다 정수 비용 또는 None."""
        if mi not in self._ecost:
            self._ecost[mi] = [None if f is None else scaled(d, f) for d, f in zip(self.edist, self.eppm(mi))]
        return self._ecost[mi]

    def vf(self, mi: int, v: int) -> int | None:
        """꼭짓점 계수: 닿는 간선 가운데 가장 낮은 것."""
        best, ep = None, self.eppm(mi)
        for x in self.adj[v]:
            f = ep[x]
            if f is not None and (best is None or f < best):
                best = f
        return best

    def xyz(self, v: int) -> tuple:
        return (self.xs[v], self.ys[v], self.zs[v])

    def local(self, x: float, y: float) -> tuple[float, float]:
        dx, dy = x - self.ox, y - self.oy
        return (dx * self.ux + dy * self.uy) / self.cell, (-dx * self.uy + dy * self.ux) / self.cell

    def locate(self, x: float, y: float) -> tuple[int, int] | None:
        """(칸 번호, 세모 비트) 또는 그물 밖이면 None. 쓸 수 없는 세모도 돌려준다."""
        a, b = self.local(x, y)
        i, j = math.floor(a), math.floor(b)
        if i < 0 or j < 0 or i >= self.cols or j >= self.rows:
            return None
        u, w = a - i, b - j
        if w <= u:
            bit = S_BIT if u + w <= 1 else E_BIT
        else:
            bit = W_BIT if u + w <= 1 else N_BIT
        return j * self.cols + i, bit

    def usable(self, x: float, y: float) -> int | None:
        """(x, y) 가 쓸 수 있는 세모 위면 그 칸 번호."""
        t = self.locate(x, y)
        return t[0] if t is not None and self.codes[t[0]] & t[1] else None

    def near_vertices(self, x: float, y: float, r: float) -> list[int]:
        """평면 거리 r 안 꼭짓점(간선 있는 것), (거리², 번호) 순."""
        a, b = self.local(x, y)
        k = math.ceil(r / self.cell) + 1
        fa, fb = math.floor(a), math.floor(b)
        r2 = r * r
        out = []
        for j in range(fb - k, fb + k + 2):
            for i in range(fa - k, fa + k + 2):
                if 0 <= i <= self.cols and 0 <= j <= self.rows:
                    out.append(j * (self.cols + 1) + i)
                if 0 <= i < self.cols and 0 <= j < self.rows:
                    out.append(self.nc + j * self.cols + i)
        res = []
        for v in out:
            if not self.adj[v]:
                continue
            dx, dy = self.xs[v] - x, self.ys[v] - y
            d2 = dx * dx + dy * dy
            if d2 <= r2:
                res.append((d2, v))
        res.sort()
        return [v for _, v in res]

    def sees(self, p, q) -> bool:
        """p → q 직선이 쓸 수 있는 세모만 지나는가(등급은 안 봄, 붙이기용)."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        n = max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / (self.cell * LOS_STEP)))
        for s in range(n):
            t = (s + 0.5) / n
            if self.usable(p[0] + t * dx, p[1] + t * dy) is None:
                return False
        return True


def _in_bbox(b, x, y, r) -> bool:
    return b[0] - r <= x <= b[2] + r and b[1] - r <= y <= b[3] + r


class MeshRouter:
    """꾸러미 길 그래프(MapIndex) + 보행 그물. 그물이 덮은 면의 면 가상 간선(y:·x:)은 쓰지 않는다."""

    def __init__(self, idx, entries: list[dict]):
        t0 = time.perf_counter()
        self.idx = idx
        self.meshes = [Mesh(e, idx.edges) for e in entries]
        self.covered = {k for e in entries if e.get("kind", "surface") == "surface" for k in e["sources"]}
        self.nkeys = [n["key"] for n in idx.pkg["nodes"]]
        self.nid = {k: i for i, k in enumerate(self.nkeys)}
        self._seams()
        self._attach_nodes()
        # 밖으로 이어진 덩어리(노드가 붙었거나 다른 그물과 이음이 있는): 면 위 점은 이런 덩어리에만 붙인다
        self.live = [set() for _ in self.meshes]
        for hits in self.node_att.values():
            for mi, seeds in hits:
                for v, _ in seeds:
                    self.live[mi].add(self.meshes[mi].comp[v])
        for a, va, b, vb, _ in self.links:
            self.live[a].add(self.meshes[a].comp[va])
            self.live[b].add(self.meshes[b].comp[vb])
        self._gates()
        self._tables()
        self.build_ms = round((time.perf_counter() - t0) * 1000)

    # ----- 그물 사이 이음 -----
    def _seams(self):
        """같은 높이(차 STEP_OK_M 안)로 맞닿거나 겹친 두 그물: 한 그물 가장자리 꼭짓점 → 다른 그물의 가장 가까운 꼭짓점."""
        links = {}
        ms = self.meshes
        for ai, A in enumerate(ms):
            for bi, B in enumerate(ms):
                if bi == ai:
                    continue
                r = LINK_CELLS * max(A.cell, B.cell)
                if not (
                    A.bbox[0] - r <= B.bbox[2]
                    and B.bbox[0] - r <= A.bbox[2]
                    and A.bbox[1] - r <= B.bbox[3]
                    and B.bbox[1] - r <= A.bbox[3]
                ):
                    continue
                for v in A.rim:
                    x, y, z = A.xyz(v)
                    if not _in_bbox(B.bbox, x, y, r):
                        continue
                    best = None
                    for w in B.near_vertices(x, y, r):
                        if abs(B.zs[w] - z) <= STEP_OK_M:
                            d = dist_mm((x, y, z), B.xyz(w))
                            if best is None or d < best[0]:
                                best = (d, w)
                    if best:
                        key = (ai, v, bi, best[1]) if ai < bi else (bi, best[1], ai, v)
                        links.setdefault(key, best[0])
        self.links = sorted((*k, d) for k, d in links.items())

    # ----- 노드 붙이기 -----
    def attach_point(self, x, y, z, need_inside: bool) -> list[tuple[int, list[tuple[int, int]]]]:
        """점이 올라선 그물(높이 차 AREA_DZ_M 안)마다 [(꼭짓점, 길이 mm)]. 세모 안이면 직선으로 보이는 가까운 것
        ATTACH_MAX 개, 세모 밖(need_inside 가 아니면)이면 가까운 것 ATTACH_OUT_MAX 개."""
        out = []
        for mi, M in enumerate(self.meshes):
            r = ATTACH_CELLS * M.cell
            if not _in_bbox(M.bbox, x, y, r) or abs(z - M.z(x, y)) > AREA_DZ_M:
                continue
            inside = M.usable(x, y) is not None
            if need_inside and not inside:
                continue
            got = []
            for v in M.near_vertices(x, y, r):
                if len(got) >= (ATTACH_MAX if inside else ATTACH_OUT_MAX):
                    break
                if not inside or M.sees((x, y), M.xyz(v)):
                    got.append((v, dist_mm((x, y, z), M.xyz(v))))
            if got:
                out.append((mi, got))
        return out

    def _attach_nodes(self):
        self.node_att = {}  # 노드 번호 → [(그물, [(꼭짓점, mm)])]
        for i, n in enumerate(self.idx.pkg["nodes"]):
            if n["location"] != "outdoor":
                continue
            hits = self.attach_point(n["x"], n["y"], n["z"], False)
            if hits:
                self.node_att[i] = hits

    # ----- 관문 -----
    def _gates(self):
        """구역 입구(이웃 구역과 같이 쓰는 꼭짓점 줄에서 ENTRANCE_EVERY 마다, 가운데 포함)·이음 대표 꼭짓점."""
        self.gate_sets = [set() for _ in self.meshes]
        for mi, M in enumerate(self.meshes):
            shared = defaultdict(list)
            for v in range(M.nc):
                bs = M.vblocks[v]
                for a in range(len(bs)):
                    for b in range(a + 1, len(bs)):
                        shared[(bs[a], bs[b])].append(v)
            for (b1, b2), vs in sorted(shared.items()):
                step = M.cols + 1 if b1 // M.nbx == b2 // M.nbx else 1  # 같은 줄 구역: 세로 경계
                runs, cur = [], [vs[0]]
                for v in vs[1:]:
                    x = M.eid.get((cur[-1], v))
                    if v - cur[-1] == step and x is not None and b1 in M.eblocks[x] and b2 in M.eblocks[x]:
                        cur.append(v)
                    else:
                        runs.append(cur)
                        cur = [v]
                runs.append(cur)
                for run in runs:
                    mid = len(run) // 2
                    for p, v in enumerate(run):
                        if p % ENTRANCE_EVERY == mid % ENTRANCE_EVERY:
                            self.gate_sets[mi].add(v)
        # 이음: (그물 A 구역, 그물 B 구역) 짝마다, 간선으로 이어진 이음 사슬마다 가운데 이음 하나
        groups = defaultdict(list)
        for link in self.links:
            a, va, b, vb, _ = link
            groups[(a, self.meshes[a].vblocks[va][0], b, self.meshes[b].vblocks[vb][0])].append(link)
        self.seam_links = []
        for k in sorted(groups):
            g = groups[k]
            A, B = self.meshes[k[0]], self.meshes[k[2]]
            chain = list(range(len(g)))

            def root(i):
                while chain[i] != i:
                    chain[i] = chain[chain[i]]
                    i = chain[i]
                return i

            for i in range(len(g)):
                for j in range(i + 1, len(g)):
                    pa, pb = sorted((g[i][1], g[j][1])), sorted((g[i][3], g[j][3]))
                    if g[i][1] == g[j][1] or tuple(pa) in A.eid or g[i][3] == g[j][3] or tuple(pb) in B.eid:
                        chain[root(j)] = root(i)
            members = defaultdict(list)
            for i in range(len(g)):
                members[root(i)].append(g[i])
            for r_ in sorted(members):
                ms = members[r_]
                link = ms[len(ms) // 2]
                self.seam_links.append(link)
                self.gate_sets[link[0]].add(link[1])
                self.gate_sets[link[2]].add(link[3])
        self.gid = {}
        self.gref = []  # 관문 번호 - 노드 수 → (그물, 꼭짓점)
        base = len(self.nkeys)
        for mi, gs in enumerate(self.gate_sets):
            for v in sorted(gs):
                self.gid[(mi, v)] = base + len(self.gref)
                self.gref.append((mi, v))
        self.n_abs = base + len(self.gref)

    def xyz_of(self, a: int, tmp: dict) -> tuple:
        if a in tmp:
            return tmp[a]["xyz"]
        if a < len(self.nkeys):
            n = self.idx.pkg["nodes"][a]
            return (n["x"], n["y"], n["z"])
        mi, v = self.gref[a - len(self.nkeys)]
        return self.meshes[mi].xyz(v)

    # ----- 구역 안 최단 -----
    def block_search(self, mi: int, b: int, seeds: list[tuple[int, int]], mode_i: int):
        """구역 b 안 다익스트라. seeds = [(꼭짓점, 시작 비용)]. (비용, 앞 꼭짓점) dict."""
        M = self.meshes[mi]
        cost, ea, eb, eblocks, adj = M.cost(mode_i), M.ea, M.eb, M.eblocks, M.adj
        dist, prev = {}, {}
        pq = []
        for v, c in seeds:
            if c < dist.get(v, math.inf):
                dist[v] = c
                prev[v] = -1
                heapq.heappush(pq, (c, v))
        while pq:
            d, v = heapq.heappop(pq)
            if d > dist[v]:
                continue
            for x in adj[v]:
                c = cost[x]
                if c is None or b not in eblocks[x]:
                    continue
                w = eb[x] if ea[x] == v else ea[x]
                nd = d + c
                if nd < dist.get(w, math.inf):
                    dist[w] = nd
                    prev[w] = v
                    heapq.heappush(pq, (nd, w))
        return dist, prev

    def _att_cost(self, mi: int, seeds: list[tuple[int, int]], mode_i: int, b: int | None = None):
        """[(꼭짓점, 붙이는 비용)] (구역 b 의 꼭짓점만, 이 방식에서 못 지나가는 꼭짓점은 뺌)."""
        M = self.meshes[mi]
        out = []
        for v, d in seeds:
            f = M.vf(mode_i, v)
            if f is not None and (b is None or b in M.vblocks[v]):
                out.append((v, scaled(d, f)))
        return out

    def _tables(self):
        """관문 사이 구역 안 최단 비용(방식별)을 미리 계산해 추상 그래프 간선으로."""
        nm = len(MODES)
        self.aadj: list[list] = [[] for _ in range(self.n_abs)]
        for i, k in enumerate(self.nkeys):
            for e, w in self.idx.out.get(k, []):
                if e["kind"] == "open_area" and (e.get("surfaceKey") in self.covered or _area_of(e) in self.covered):
                    continue
                self.aadj[i].append((self.nid[w], ("edge", e), None))
        # 구역마다 노드 관문
        node_blocks = defaultdict(list)  # (그물, 구역) → [(노드 번호, seeds)]
        for i in sorted(self.node_att):
            for mi, seeds in self.node_att[i]:
                for b in sorted({b for v, _ in seeds for b in self.meshes[mi].vblocks[v]}):
                    node_blocks[(mi, b)].append((i, seeds))
        self.node_blocks = node_blocks
        self.block_gates = {}
        for mi, M in enumerate(self.meshes):
            gb = defaultdict(list)
            for v in sorted(self.gate_sets[mi]):
                for b in M.vblocks[v]:
                    gb[b].append(v)
            for b in sorted(set(gb) | {b for (m2, b) in node_blocks if m2 == mi}):
                vg, ng = gb.get(b, []), node_blocks.get((mi, b), [])
                self.block_gates[(mi, b)] = vg
                if len(vg) + len(ng) < 2:
                    continue
                # 방식을 구역 안 등급 계수가 같은 것끼리 묶는다
                present = sorted({M.cls[k] for k in M.block_cells.get(b, [])})
                groups = defaultdict(list)
                for m in range(nm):
                    groups[tuple(M.cf[m][c] for c in present)].append(m)
                found = {}  # (from, to) → [비용 × 방식]
                for modes in groups.values():
                    m0 = modes[0]
                    for g in vg:
                        if M.vf(m0, g) is None:
                            continue
                        dist, _ = self.block_search(mi, b, [(g, 0)], m0)
                        src = self.gid[(mi, g)]
                        for h in vg:
                            if h != g and h in dist:
                                found.setdefault((src, self.gid[(mi, h)]), [None] * nm)
                                for m in modes:
                                    found[(src, self.gid[(mi, h)])][m] = dist[h]
                        for n, seeds in ng:
                            c = _best(dist, self._att_cost(mi, seeds, m0, b))
                            if c is not None:
                                for key in ((src, n), (n, src)):
                                    found.setdefault(key, [None] * nm)
                                    for m in modes:
                                        found[key][m] = c[0]
                    for p, (n, seeds) in enumerate(ng):
                        att = self._att_cost(mi, seeds, m0, b)
                        if not att:
                            continue
                        dist, _ = self.block_search(mi, b, att, m0)
                        for n2, seeds2 in ng[p + 1 :]:
                            c = _best(dist, self._att_cost(mi, seeds2, m0, b))
                            if c is not None:
                                for key in ((n, n2), (n2, n)):
                                    found.setdefault(key, [None] * nm)
                                    for m in modes:
                                        found[key][m] = c[0]
                for (s, t), cs in found.items():
                    self.aadj[s].append((t, ("block", mi, b), cs))
        for a, va, b, vb, d in self.seam_links:
            A, B = self.meshes[a], self.meshes[b]
            cs = []
            for m in range(nm):
                fa, fb = A.vf(m, va), B.vf(m, vb)
                cs.append(None if fa is None or fb is None else scaled(d, min(fa, fb)))
            ga, gb_ = self.gid[(a, va)], self.gid[(b, vb)]
            self.aadj[ga].append((gb_, ("seam", a, va, b, vb), cs))
            self.aadj[gb_].append((ga, ("seam", b, vb, a, va), cs))

    # ----- 경로 -----
    def route_points(self, start, goal, mode: str = "shortest") -> tuple[dict | None, list]:
        """출발·도착 = 노드 key 또는 점 (x, y, z). 반환: (경로 또는 None, [출발, 도착] 붙인 결과)."""
        t0 = time.perf_counter()
        mode_i = MODES.index(mode)
        tmp, ends = {}, []
        raw = []  # 점이면 그물에 붙인 결과(아직 거르지 않음)
        for spec in (start, goal):
            if isinstance(spec, str):
                raw.append(None)
                continue
            x, y, z = (float(v) for v in spec)
            raw.append(None if self.idx.node_at(x, y, z) else self.attach_point(x, y, z, True))
        for which, spec in enumerate((start, goal)):
            key = ("q:from", "q:to")[which]
            if isinstance(spec, str):
                ends.append({"nodeKey": spec, "attached": "node"} if spec in self.nid else None)
                continue
            x, y, z = (float(v) for v in spec)
            at = self.idx.node_at(x, y, z)
            if at:
                ends.append({"nodeKey": at["key"], "attached": "node"})
                continue
            # 밖으로 이어진 덩어리이거나 다른 끝 점과 같은 덩어리인 꼭짓점에만 붙인다(막힌 조각이면 가까운 노드)
            other = {(mi, self.meshes[mi].comp[v]) for mi, seeds in (raw[1 - which] or []) for v, _ in seeds}
            hits = []
            for mi, seeds in raw[which]:
                comp = self.meshes[mi].comp
                seeds = [sd for sd in seeds if comp[sd[0]] in self.live[mi] or (mi, comp[sd[0]]) in other]
                if seeds:
                    hits.append((mi, seeds))
            if hits:
                M = self.meshes[hits[0][0]]
                a = self.n_abs + which
                tmp[a] = {"key": key, "xyz": (round(x, 2), round(y, 2), round(M.z(x, y), 2)), "hits": hits}
                ends.append({"nodeKey": key, "attached": "surface", "areas": [self.meshes[h[0]].key for h in hits]})
                continue
            n = self.idx.nearest_node(x, y, z)
            ends.append({"nodeKey": n["key"], "attached": "nearest_node"} if n else None)
        if None in ends:
            return None, ends
        ids = [self.n_abs + w if ends[w]["nodeKey"].startswith("q:") else self.nid[ends[w]["nodeKey"]] for w in (0, 1)]
        extra = self._temp_edges(tmp, ids, mode_i)
        found = self._search(ids[0], ids[1], mode, mode_i, tmp, extra)
        if found is None:
            return None, ends
        out = self._describe(found, tmp, mode, mode_i)
        out["timeMs"] = round((time.perf_counter() - t0) * 1000, 1)
        return out, ends

    def _temp_edges(self, tmp: dict, ids: list[int], mode_i: int) -> dict:
        """면 위 출발·도착 점을 그 점이 든 구역의 관문·노드(와 같은 구역이면 서로)에 잇는 간선."""
        extra = defaultdict(list)
        for which in (0, 1):
            a = ids[which]
            if a not in tmp:
                continue
            for mi, seeds in tmp[a]["hits"]:
                M = self.meshes[mi]
                for b in sorted({b for v, _ in seeds for b in M.vblocks[v]}):
                    att = self._att_cost(mi, seeds, mode_i, b)
                    if not att:
                        continue
                    dist, _ = self.block_search(mi, b, att, mode_i)
                    targets = [(self.gid[(mi, v)], [(v, 0)]) for v in self.block_gates.get((mi, b), [])]
                    targets += [(n, self._att_cost(mi, s2, mode_i, b)) for n, s2 in self.node_blocks.get((mi, b), [])]
                    if which == 0 and ids[1] in tmp:
                        for mj, s2 in tmp[ids[1]]["hits"]:
                            if mj == mi:
                                targets.append((ids[1], self._att_cost(mi, s2, mode_i, b)))
                    for t, seeds2 in targets:
                        c = _best(dist, seeds2)
                        if c is None:
                            continue
                        info = ("qblock", mi, b, which)
                        if which == 0:
                            extra[a].append((t, info, c[0]))
                        elif t != ids[0]:
                            extra[t].append((a, info, c[0]))
        return extra

    def _search(self, start: int, goal: int, mode: str, mode_i: int, tmp: dict, extra: dict):
        gx = self.xyz_of(goal, tmp)
        use_h = mode in _ASTAR_MODES

        def h(a):
            if not use_h:
                return 0
            p = self.xyz_of(a, tmp)
            return dist_mm(p, gx) * 99 // 100

        dist = {(start, 0): 0}
        prev = {}
        pq = [(h(start), start, 0, 0)]
        end = None
        while pq:
            _, u, riding, d = heapq.heappop(pq)
            if d > dist[(u, riding)]:
                continue
            if u == goal:
                end = (u, riding)
                break
            for v, info, cs in [*self.aadj[u], *extra.get(u, [])] if u < self.n_abs else extra.get(u, []):
                if info[0] == "edge":
                    e = info[1]
                    c = self.idx._cost(e, mode, reverse=self.nkeys[v] == e["from"], boarding=not riding)
                    if c is None:
                        continue
                    c = mm(c)
                    nr = 1 if e["kind"] == "elevator" else 0
                else:
                    c = cs[mode_i] if isinstance(cs, list) else cs
                    if c is None:
                        continue
                    nr = 0
                nd = d + c
                sv = (v, nr)
                if nd < dist.get(sv, math.inf):
                    dist[sv] = nd
                    prev[sv] = ((u, riding), info)
                    heapq.heappush(pq, (nd + h(v), v, nr, nd))
        if end is None:
            return None
        steps = []
        cur = end
        while cur in prev:
            p, info = prev[cur]
            steps.append((p[0], cur[0], info))
            cur = p
        steps.reverse()
        return {"start": start, "goal": goal, "steps": steps, "cost": dist[end]}

    # ----- 경로 → 선 -----
    def _ref(self, a: int, tmp: dict):
        """추상 점 → 그물 위 점 표: ("n", 노드 번호) | ("q", 번호) | ("v", 그물, 꼭짓점)."""
        if a in tmp:
            return ("q", a)
        if a < len(self.nkeys):
            return ("n", a)
        mi, v = self.gref[a - len(self.nkeys)]
        return ("v", mi, v)

    def _seeds_of(self, ref, mi: int, b: int, mode_i: int, tmp: dict):
        if ref[0] == "v":
            return [(ref[2], 0)]
        hits = tmp[ref[1]]["hits"] if ref[0] == "q" else self.node_att.get(ref[1], [])
        for mj, seeds in hits:
            if mj == mi:
                return self._att_cost(mi, seeds, mode_i, b)
        return []

    def _expand(self, u: int, v: int, mi: int, b: int, mode_i: int, tmp: dict) -> list:
        """구역 안 간선 u → v 를 꼭짓점 줄로 푼다(양 끝 포함)."""
        ru, rv = self._ref(u, tmp), self._ref(v, tmp)
        dist, prev = self.block_search(mi, b, self._seeds_of(ru, mi, b, mode_i, tmp), mode_i)
        if rv[0] == "v":
            w = rv[2]
        else:
            w = _best(dist, self._seeds_of(rv, mi, b, mode_i, tmp))[1]
        path = []
        while w != -1:
            path.append(("v", mi, w))
            w = prev[w]
        path.reverse()
        return [ru, *path, rv]

    def _point(self, ref, tmp) -> tuple:
        if ref[0] == "q":
            return tmp[ref[1]]["xyz"]
        if ref[0] == "n":
            n = self.idx.pkg["nodes"][ref[1]]
            return (n["x"], n["y"], n["z"])
        return self.meshes[ref[1]].xyz(ref[2])

    def _describe(self, found: dict, tmp: dict, mode: str, mode_i: int) -> dict:
        # 1) 추상 경로 → [길 | 점 줄]
        items: list = []  # ("edge", e, from_key) 또는 ("pts", [ref...])
        for u, v, info in found["steps"]:
            if info[0] == "edge":
                items.append(("edge", info[1], self.nkeys[u]))
                continue
            if info[0] == "seam":
                seq = [self._ref(u, tmp), self._ref(v, tmp)]
            else:
                seq = self._expand(u, v, info[1], info[2], mode_i, tmp)
            if items and items[-1][0] == "pts":
                prev = items[-1][1]
                items[-1] = ("pts", prev + (seq[1:] if prev[-1] == seq[0] else seq))
            else:
                items.append(("pts", seq))
        # 2) 점 줄마다 줄 당기기 → 가상 길
        edges, nodes_seq, tmp_nodes = [], [], {}

        def key_of(ref):
            if ref[0] == "n":
                return self.nkeys[ref[1]]
            if ref[0] == "q":
                return tmp[ref[1]]["key"]
            return f"v:{self.meshes[ref[1]].key}:{ref[2]}"

        start_ref = self._ref(found["start"], tmp)
        nodes_seq.append(key_of(start_ref))
        for it in items:
            if it[0] == "edge":
                e = it[1]
                edges.append(e)
                nodes_seq.append(e["to"] if e["from"] == it[2] else e["from"])
                continue
            for ve in self._virtual_edges(it[1], tmp, key_of, len(edges), mode_i):
                for k, p in ((ve["from"], ve["geometry"][0]), (ve["to"], ve["geometry"][-1])):
                    if k not in self.idx.nodes:
                        tmp_nodes[k] = {
                            "key": k,
                            "location": "outdoor",
                            "buildingKey": None,
                            "floorKey": None,
                            "kind": "point",
                            "x": p[0],
                            "y": p[1],
                            "z": p[2],
                            "estimated": True,
                        }
                edges.append(ve)
                nodes_seq.append(ve["to"])
        view = self.idx._with(tmp_nodes, [e for e in edges if e["key"].startswith("w:")])
        out = view._describe(nodes_seq, edges, mode)
        out["cost"] = round(found["cost"] / 1000, 3)
        return out

    def _virtual_edges(self, refs: list, tmp: dict, key_of, base: int, mode_i: int) -> list[dict]:
        pts = [self._point(r, tmp) for r in refs]
        mesh_ids = []
        for r in refs:
            if r[0] == "v" and r[1] not in mesh_ids:
                mesh_ids.append(r[1])
        run = _Run([self.meshes[i] for i in mesh_ids], mode_i)
        own = [mesh_ids.index(r[1]) if r[0] == "v" else None for r in refs]  # 점마다 제 그물 자리(노드·점은 None)
        keep = _pull(run, pts, own)
        # 조각마다 (그물, 이 방식의 1 m 비용), 같은 것끼리 한 가상 길
        segs = []
        for a, b in zip(keep, keep[1:]):
            segs.append((a, b, run.label(pts[a], pts[b])))
        out = []
        k = 0
        while k < len(segs):
            j = k
            while j + 1 < len(segs) and segs[j + 1][2] == segs[k][2]:
                j += 1
            M = run.meshes[segs[k][2][0]]
            geom = []
            for a, b, _ in segs[k : j + 1]:
                line = run.line(pts[a], pts[b])
                geom.extend(line if not geom else line[1:])
            geom = [[round(c, 2) for c in p] for p in geom]
            length = 0.0
            for p, q in zip(geom, geom[1:]):
                dx, dy, dz = q[0] - p[0], q[1] - p[1], q[2] - p[2]
                length += math.sqrt(dx * dx + dy * dy + dz * dz)
            cls = [c for a, b, _ in segs[k : j + 1] for c in run.classes(pts[a], pts[b])]
            e = {
                "key": f"w:{base + len(out)}:{M.key}",
                "from": key_of(refs[segs[k][0]]),
                "to": key_of(refs[segs[j][1]]),
                "name": M.entry.get("name") or None,
                "kind": "open_area" if M.kind == "surface" else M.entry["areaKind"],
                "direction": "both",
                "pedestrianAccess": "allowed",
                "wheelchairAccess": "unknown",
                "lengthM": round(length, 2),
                "stepCount": None,
                "widthM": None,
                "geometry": geom,
                "estimated": True,
                "meshKey": M.key,
            }
            if M.kind == "surface":
                e["areaKind"] = M.entry.get("areaKind") or "surface"
            i = ind_mod.basic_indicators(e, False)
            i.update(_class_indicators(cls))
            e["indicators"] = i
            out.append(e)
            k = j + 1
        return out


def _class_indicators(cls: list[dict]) -> dict:
    """가상 길 위 표본 칸 등급들 → 지붕·조명 비율(모르는 칸은 0 으로 셈, 다 모르면 None)·차량(절반 넘으면 shared)."""
    out = {}
    for f in ("coveredRatio", "litRatio"):
        vals = [c.get(f) for c in cls]
        out[f] = None if all(v is None for v in vals) else round(sum(v or 0.0 for v in vals) / len(vals), 2)
    shared = sum(1 for c in cls if c.get("vehicle") == "shared")
    out["vehicle"] = (
        "shared" if cls and shared * 2 >= len(cls) else (cls[0].get("vehicle") if cls and shared == 0 else "none")
    )
    return out


def _area_of(e: dict) -> str | None:
    """열린 면 가상 간선 key x:<면 key>:<노드>:<노드> 의 면 key(면 key 는 a:<번호>)."""
    return ":".join(e["key"].split(":")[1:3]) if e["key"].startswith("x:") else None


def _best(dist: dict, seeds: list[tuple[int, int]]):
    """seeds 가운데 dist + 붙이는 비용이 가장 작은 것 (비용, 꼭짓점). 같으면 앞 것."""
    best = None
    for v, c in seeds:
        if v in dist and (best is None or dist[v] + c < best[0]):
            best = (dist[v] + c, v)
    return best


class _Run:
    """한 점 줄에 이어 쓰는 그물들(같은 높이로 이어진 그물). 줄 당기기용 보기·높이·등급."""

    def __init__(self, meshes: list[Mesh], mode_i: int = 0):
        self.meshes = meshes
        self.mode_i = mode_i
        self.step = min((M.cell for M in meshes), default=1.0) * LOS_STEP
        self.cell_max = max((M.cell for M in meshes), default=1.0)

    def _at(self, x, y, prev_m, prev_z, gap=0):
        """(x, y) 를 덮는 쓸 수 있는 세모의 (그물 자리, 칸): 앞 표본 그물 먼저, 아니면 높이가 이어지는 첫 그물."""
        if prev_m is not None:
            k = self.meshes[prev_m].usable(x, y)
            if k is not None:
                return prev_m, k
        for p, M in enumerate(self.meshes):
            if p == prev_m:
                continue
            k = M.usable(x, y)
            if k is not None and (prev_z is None or abs(M.z(x, y) - prev_z) <= STEP_OK_M + self.step * (gap + 1)):
                return p, k
        return None

    def sees(self, p, q, pm0=None, qm0=None) -> bool:
        """p → q 직선이 그물들의 쓸 수 있는 세모만, 이 방식에서 1 m 비용이 같은 칸으로만 지나는가.
        pm0·qm0 = 두 끝의 제 그물 자리(노드·점이면 None). 그물이 바뀌는 곳(이음)·그물 밖 끝의 틈은 칸 하나 길이까지
        건넌다. 한 그물 안 구멍(틈 앞뒤가 같은 그물)은 못 건넌다."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        n = max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / self.step))
        sig, first = None, True  # sig = 첫 표본 칸의 1 m 비용
        pm, pz, gap, seam = pm0, (p[2] if pm0 is not None else None), 0, False  # gap = 이어진 틈 표본 수
        for s in range(n):
            t = (s + 0.5) / n
            x, y = p[0] + t * dx, p[1] + t * dy
            got = self._at(x, y, pm, pz, gap)
            if got is None:
                if self._seamy(x, y):  # 두 그물 칸이 겹친 이음 틈: 그물 안 구멍이 아님
                    seam = True
                    continue
                gap += 1
                if gap * self.step > self.cell_max:
                    return False
                continue
            M = self.meshes[got[0]]
            if gap and pm is not None and got[0] == pm and not seam:
                return False
            seam = False
            g = M.cf[self.mode_i][M.cls[got[1]]]
            if first:
                sig, first = g, False
            elif g != sig:
                return False
            pm, pz, gap = got[0], M.z(x, y), 0
        return not first and not (gap and qm0 is not None and qm0 == pm)

    def _seamy(self, x, y) -> bool:
        """(x, y) 가 둘 이상 그물의 빈 칸이 아닌 칸(쓰는 세모가 있는 칸) 안인가."""
        n = 0
        for M in self.meshes:
            t = M.locate(x, y)
            if t is not None and M.codes[t[0]]:
                n += 1
                if n >= 2:
                    return True
        return False

    def classes(self, p, q) -> list[dict]:
        """p → q 표본 점마다 든 칸의 등급(그물 밖 표본은 뺌)."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        n = max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / self.step))
        out, pm, pz = [], None, None
        for s in range(n):
            t = (s + 0.5) / n
            x, y = p[0] + t * dx, p[1] + t * dy
            got = self._at(x, y, pm, pz)
            if got is not None:
                M = self.meshes[got[0]]
                out.append(M.classes[M.cls[got[1]]])
                pm, pz = got[0], M.z(x, y)
        return out

    def label(self, p, q) -> tuple:
        """조각의 (그물 자리, 이 방식의 1 m 비용): 가운데 점(없으면 앞에서부터 표본)이 든 세모."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        n = max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / self.step))
        order = [n // 2] + [s for s in range(n) if s != n // 2]
        for s in order:
            t = (s + 0.5) / n
            got = self._at(p[0] + t * dx, p[1] + t * dy, None, None)
            if got is not None:
                M = self.meshes[got[0]]
                return got[0], M.cf[self.mode_i][M.cls[got[1]]]
        return 0, None

    def z(self, x, y, prev) -> float:
        best = None
        for M in self.meshes:
            if M.usable(x, y) is not None:
                z = M.z(x, y)
                if best is None or abs(z - prev) < abs(best - prev):
                    best = z
        return prev if best is None else best

    def line(self, p, q) -> list:
        """p → q 직선을 SURFACE_SAMPLE_M 마다 그물 높이로 찍은 3D 선(두 끝은 주어진 높이)."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        k = max(1, math.ceil(math.sqrt(dx * dx + dy * dy) / SURFACE_SAMPLE_M))
        pts, prev = [list(p)], p[2]
        for s in range(1, k):
            t = s / k
            x, y = p[0] + t * dx, p[1] + t * dy
            prev = self.z(x, y, prev)
            pts.append([x, y, prev])
        pts.append(list(q))
        return pts


def _pull(run: _Run, pts: list, own: list) -> list[int]:
    """줄 당기기: 앞 점에서 끝이 보이면 끝으로, 아니면 직선으로 보이는 먼 점으로 건너뛴다(2배씩 늘려 보고 사이는 반씩
    좁힘). 남길 점 번호."""
    keep = [0]
    i, last = 0, len(pts) - 1
    while i < last:
        if run.sees(pts[i], pts[last], own[i], own[last]):  # 끝이 바로 보이면 끝으로
            keep.append(last)
            break
        ok, step, fail = i + 1, 1, None
        while True:
            j = min(i + 2 * step, last)
            if run.sees(pts[i], pts[j], own[i], own[j]):
                ok = j
                if j == last:
                    break
                step *= 2
            else:
                fail = j
                break
        if fail is not None:
            lo, hi = ok, fail
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if run.sees(pts[i], pts[mid], own[i], own[mid]):
                    lo = mid
                else:
                    hi = mid
            ok = lo
        keep.append(ok)
        i = ok
    return keep
