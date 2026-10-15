"""공개된 꾸러미 하나로 하는 검색·경로 계산 (메모리 안)."""

import heapq
import math
import re
from collections import defaultdict

WALK_MPS = 1.2
MODES = ("shortest", "avoidStairs", "preferElevator")
ATTACH_RADIUS_M = 25.0
ATTACH_DZ_M = 2.0


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

    @staticmethod
    def _cost(e: dict, mode: str) -> float | None:
        c = e["lengthM"]
        if e["kind"] == "stairs":
            if mode == "avoidStairs":
                return None
            if mode == "preferElevator":
                c *= 4
        if e["kind"] == "elevator" and mode == "preferElevator":
            c *= 0.5
        if e["pedestrianAccess"] == "unknown":
            c *= 1.2
        if e["kind"] == "vehicle_road":
            c *= 1.5
        return c

    def route(self, start: str, goal: str, mode: str) -> dict | None:
        dist = {start: 0.0}
        prev: dict[str, tuple[str, dict]] = {}
        pq = [(0.0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == goal:
                break
            if d > dist.get(u, math.inf):
                continue
            for e, v in self.out[u]:
                c = self._cost(e, mode)
                if c is None:
                    continue
                nd = d + c
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    prev[v] = (u, e)
                    heapq.heappush(pq, (nd, v))
        if goal not in dist:
            return None
        path_nodes, path_edges = [goal], []
        cur = goal
        while cur != start:
            u, e = prev[cur]
            path_edges.append(e)
            path_nodes.append(u)
            cur = u
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
        duration = total / WALK_MPS + sum(20 for e in edges if e["kind"] == "elevator")
        return {
            "mode": mode,
            "distanceM": round(total, 2),
            "durationS": round(duration),
            "stairsEdges": stairs,
            "nodeKeys": nodes,
            "edgeKeys": [e["key"] for e in edges],
            "geometry": geometry,
            "steps": steps,
        }


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
    verb = {"ramp": "경사로", "crossing": "횡단보도 건너기", "corridor": "복도"}.get(s["kind"], "걷기")
    move = f" {_where(s['from'])} → {_where(s['to'])}" if s["floorChange"] else ""
    return f"{verb} {round(s['distanceM'])} m{move}{name}"
