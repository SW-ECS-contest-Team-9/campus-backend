"""경로 계산 단위 시험: 열린 면(광장·운동장) 가로지르기, 보행 허용 차도의 비용. DB 없이 꾸러미 dict 로."""

import math

from mapdata.routing import MapIndex


def node(key, x, y, z=100.0, location="outdoor"):
    return {
        "key": key,
        "location": location,
        "buildingKey": None,
        "floorKey": None,
        "kind": "junction",
        "x": x,
        "y": y,
        "z": z,
        "estimated": False,
    }


def edge(key, a, b, nodes, kind="walkway", access="allowed", name=None):
    pa, pb = nodes[a], nodes[b]
    return {
        "key": key,
        "from": a,
        "to": b,
        "name": name,
        "kind": kind,
        "direction": "both",
        "pedestrianAccess": access,
        "wheelchairAccess": "unknown",
        "lengthM": round(math.dist((pa["x"], pa["y"], pa["z"]), (pb["x"], pb["y"], pb["z"])), 2),
        "stepCount": None,
        "widthM": None,
        "geometry": [[pa["x"], pa["y"], pa["z"]], [pb["x"], pb["y"], pb["z"]]],
        "estimated": False,
    }


def pkg(nodes, edges, areas=(), area_nodes=()):
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "places": [],
        "buildings": [],
        "floors": [],
        "walkAreas": list(areas),
        "walkAreaNodes": list(area_nodes),
    }


def area(key, polygon, z=100.0, kind="plaza", name="앞마당"):
    return {
        "key": key,
        "location": "outdoor",
        "floorKey": None,
        "name": name,
        "kind": kind,
        "polygon": polygon,
        "z": z,
        "estimated": False,
    }


def square():
    """20 m 정사각형 광장. 네 모서리 노드는 가장자리 길로만 이어져 있다."""
    n = {k: node(k, x, y) for k, x, y in [("a", 0, 0), ("b", 20, 0), ("c", 20, 20), ("d", 0, 20)]}
    e = [edge("ab", "a", "b", n), edge("bc", "b", "c", n), edge("cd", "c", "d", n), edge("da", "d", "a", n)]
    poly = [[0, 0], [20, 0], [20, 20], [0, 20]]
    return n, e, poly


def test_square_area_routes_diagonally():
    n, e, poly = square()
    idx = MapIndex(pkg(n, e, [area("a:1", poly)], [{"areaKey": "a:1", "nodeKey": k} for k in n]))
    r = idx.route("a", "c", "shortest")
    assert r["distanceM"] == round(math.hypot(20, 20), 2)
    assert [s["kind"] for s in r["steps"]] == ["open_area"]
    assert r["steps"][0]["instruction"].startswith("광장을 가로질러 28 m")
    assert r["geometry"] == [[0, 0, 100.0], [20, 20, 100.0]]


def test_without_area_uses_edges():
    n, e, _ = square()
    r = MapIndex(pkg(n, e)).route("a", "c", "shortest")
    assert r["distanceM"] == 40


def test_concave_notch_is_not_cut():
    # ㄷ자 면: 위쪽 가운데가 파여 있다(x 8~12, y 5~20). a(2,18) → b(18,18) 직선은 파인 곳을 지난다.
    poly = [[0, 0], [20, 0], [20, 20], [12, 20], [12, 5], [8, 5], [8, 20], [0, 20]]
    n = {k: node(k, x, y) for k, x, y in [("a", 2, 18), ("b", 18, 18), ("m", 10, 0)]}
    idx = MapIndex(pkg(n, [], [area("a:1", poly)], [{"areaKey": "a:1", "nodeKey": k} for k in n]))
    r = idx.route("a", "b", "shortest")
    assert r["nodeKeys"] == ["a", "m", "b"]  # 파인 곳 아래 m 을 거쳐 돈다
    assert r["distanceM"] == round(2 * math.hypot(8, 18), 2)


def test_area_does_not_join_other_levels_or_outside_nodes():
    # 면 높이 100. u 는 면 안이지만 3 m 위 단(벽으로 갈림), o 는 면 밖. 계단으로만 u 에 오른다.
    n, e, poly = square()
    n["u"] = node("u", 10, 10, 103)
    n["s"] = node("s", 10, 0, 100)
    n["o"] = node("o", 30, 0)
    e = e + [edge("as", "a", "s", n), edge("su", "s", "u", n, kind="stairs"), edge("bo", "b", "o", n)]
    members = [{"areaKey": "a:1", "nodeKey": k} for k in ("a", "b", "c", "d", "u")]
    idx = MapIndex(pkg(n, e, [area("a:1", poly)], members))
    assert idx.route("d", "u", "avoidStairs") is None  # 면을 건너 3 m 위로 가지 않는다
    r = idx.route("d", "u", "shortest")
    assert "stairs" in [s["kind"] for s in r["steps"]]
    r = idx.route("d", "o", "avoidStairs")
    assert r is not None and "stairs" not in [s["kind"] for s in r["steps"]]


def test_neighbor_cap_keeps_area_connected():
    # 한 줄로 늘어선 노드 30개: 이웃 수를 줄여도 끝에서 끝까지 직선 거리로 간다.
    n = {f"n{i}": node(f"n{i}", i * 2.0, 0.0) for i in range(30)}
    poly = [[-1, -1], [60, -1], [60, 1], [-1, 1]]
    idx = MapIndex(pkg(n, [], [area("a:1", poly)], [{"areaKey": "a:1", "nodeKey": k} for k in n]))
    assert idx.route("n0", "n29", "shortest")["distanceM"] == 58
    assert max(len(v) for v in idx.out.values()) <= 2 * 12


def test_vehicle_road_penalty_small_only_when_pedestrians_allowed():
    # s→t: 차도 100 m 또는 인도 125 m.
    n = {k: node(k, x, y) for k, x, y in [("s", 0, 0), ("t", 100, 0), ("w", 50, 37.5)]}
    sidewalk = [edge("sw1", "s", "w", n), edge("sw2", "w", "t", n)]
    for access, expect in (("allowed", "vehicle_road"), ("unknown", "walkway")):
        e = sidewalk + [edge("road", "s", "t", n, kind="vehicle_road", access=access)]
        r = MapIndex(pkg(n, e)).route("s", "t", "shortest")
        assert r["steps"][0]["kind"] == expect, access
