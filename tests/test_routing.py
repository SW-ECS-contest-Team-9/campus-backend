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


# ---------- 지표를 쓰는 경로 방식 ----------

from mapdata.routing import MODE_SPEC, MODES  # noqa: E402


def with_ind(e, **kw):
    """지표를 직접 준 길(가져오기 때 계산된 것처럼)."""
    from mapdata.indicators import basic_indicators

    i = basic_indicators(e, False)
    i.update(kw)
    return {**e, "indicators": i}


def detour(z_direct=100.0, direct_kind="walkway", direct_kw=None, detour_kw=None):
    """s→t: 곧은 길 100 m 또는 w 를 거쳐 돌아가는 길 2 × 70 m(평지)."""
    n = {"s": node("s", 0, 0), "t": node("t", 100, 0, z_direct), "w": node("w", 50, 48.99)}
    direct = with_ind(edge("d", "s", "t", n, kind=direct_kind), **(direct_kw or {}))
    e = [
        direct,
        with_ind(edge("a", "s", "w", n), **(detour_kw or {})),
        with_ind(edge("b", "w", "t", n), **(detour_kw or {})),
    ]
    return n, e


def kinds_of(r):
    return [s["kind"] for s in r["steps"]]


def test_modes_listed_in_spec():
    assert set(MODES) == set(MODE_SPEC)
    assert {"shortest", "fastest", "avoidStairs", "accessible", "preferElevator", "rainProtected", "nightSafe"} <= set(
        MODES
    )


def test_fastest_avoids_steep_climb_shortest_does_not():
    # s→t 곧은 길 100 m 에 20 m 오르막(20 %) 또는 평지 100 m + 승강기 20 m.
    n = {"s": node("s", 0, 0), "t": node("t", 100, 0, 120), "w": node("w", 100, 1, 100)}
    e = [edge("d", "s", "t", n), edge("a", "s", "w", n), edge("c", "w", "t", n, kind="elevator")]
    idx = MapIndex(pkg(n, e))  # 지표 없는 옛 꾸러미: 기하로 계산
    assert idx.route("s", "t", "shortest")["edgeKeys"] == ["d"]
    r = idx.route("s", "t", "fastest")
    assert r["edgeKeys"] == ["a", "c"]
    assert r["summary"]["elevatorRides"] == 1
    back = idx.route("t", "s", "fastest")  # 내리막은 곧은 길이 빠르다
    assert back["edgeKeys"] == ["d"]


def test_elevator_wait_counted_once_per_ride():
    n = {k: node(k, 0, 0, z) for k, z in (("a", 100), ("b", 104), ("c", 108))}
    e = [edge("l1", "a", "b", n, kind="elevator"), edge("l2", "b", "c", n, kind="elevator")]
    r = MapIndex(pkg(n, e)).route("a", "c", "fastest")
    from mapdata import indicators as ind

    ride = 2 * (4 / ind.ELEVATOR_MPS + ind.ELEVATOR_STOP_S)
    assert r["durationS"] == round(ind.ELEVATOR_WAIT_S + ride)
    assert r["summary"]["elevatorRides"] == 1


def test_accessible_excludes_stairs_and_too_steep_takes_ramp():
    n = {"s": node("s", 0, 0), "t": node("t", 30, 0, 102), "m": node("m", 15, 20, 101), "r": node("r", 0, -40)}
    n["r2"] = node("r2", 30, -40, 102)
    e = [
        edge("st", "s", "t", n, kind="stairs"),
        edge("steep1", "s", "m", n),  # 25 m, 4 % ... 이어지는 m→t 는 25 m 4 %
        edge("steep2", "m", "t", n),
        edge("r0", "s", "r", n),
        edge("r1", "r", "r2", n, kind="ramp"),  # 30 m 에 2 m: 6.7 % (steep)
        edge("r3", "r2", "t", n),
    ]
    idx = MapIndex(pkg(n, e))
    assert idx.route("s", "t", "shortest")["edgeKeys"] == ["st"]
    assert idx.route("s", "t", "accessible")["edgeKeys"] == ["steep1", "steep2"]
    # 가운데 길을 10 % 로 만들면 경사로(6.7 %)로 돈다
    n["m"]["z"] = 102.5
    e2 = [edge(x["key"], x["from"], x["to"], n, kind=x["kind"]) for x in e]
    r = MapIndex(pkg(n, e2)).route("s", "t", "accessible")
    assert r["edgeKeys"] == ["r0", "r1", "r3"]
    assert r["summary"]["wheelchair"] == "steep"


def test_accessible_no_route_when_only_stairs():
    n = {"s": node("s", 0, 0), "t": node("t", 5, 0, 102)}
    assert MapIndex(pkg(n, [edge("st", "s", "t", n, kind="stairs")])).route("s", "t", "accessible") is None


def test_rain_protected_prefers_covered_detour():
    n, e = detour(direct_kw={"coveredRatio": 0.0, "cover": "none"}, detour_kw={"coveredRatio": 1.0, "cover": "canopy"})
    idx = MapIndex(pkg(n, e))
    assert idx.route("s", "t", "shortest")["edgeKeys"] == ["d"]
    r = idx.route("s", "t", "rainProtected")
    assert r["edgeKeys"] == ["a", "b"]
    assert r["summary"]["rainExposedM"] == 0.0


def test_rain_unknown_cover_counts_as_exposed():
    n, e = detour(direct_kw={"coveredRatio": None, "cover": None}, detour_kw={"coveredRatio": 1.0, "cover": "canopy"})
    r = MapIndex(pkg(n, e)).route("s", "t", "rainProtected")
    assert r["edgeKeys"] == ["a", "b"]


def test_night_safe_prefers_lit_and_avoids_vehicle_road():
    n, e = detour(direct_kw={"litRatio": None}, detour_kw={"litRatio": 1.0})
    idx = MapIndex(pkg(n, e))
    r = idx.route("s", "t", "nightSafe")
    assert r["edgeKeys"] == ["a", "b"]
    assert r["summary"]["litOutdoorM"] == r["summary"]["outdoorM"]
    n, e = detour(direct_kw={"litRatio": 1.0, "vehicle": "shared"}, detour_kw={"litRatio": 1.0})
    e[0]["kind"] = "vehicle_road"
    assert MapIndex(pkg(n, e)).route("s", "t", "nightSafe")["edgeKeys"] == ["a", "b"]


def test_summary_counts_steps_and_climb_by_direction():
    n = {"a": node("a", 0, 0), "b": node("b", 4, 0, 102), "c": node("c", 14, 0, 102)}
    e = [with_ind(edge("st", "a", "b", n, kind="stairs"), steps=12), edge("w", "b", "c", n)]
    idx = MapIndex(pkg(n, e))
    up = idx.route("a", "c", "shortest")["summary"]
    assert up["steps"] == 12 and up["climbM"] == 2.0 and up["descentM"] == 0.0
    down = idx.route("c", "a", "shortest")["summary"]
    assert down["climbM"] == 0.0 and down["descentM"] == 2.0
