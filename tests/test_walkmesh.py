"""보행 그물(아3): 칸 4등분 세모 그물 만들기(면·길 띠·칸 등급)와 그물 위 경로·줄 당기기."""

import math

from test_routing import area, edge, node, pkg
from test_surfaces import SLOPE_X, members, surface

from mapdata import walkmesh
from mapdata.routing import MODES, MapIndex

SQ = [[0, 0], [20, 0], [20, 20], [0, 20]]


def ring_bbox(r):
    xs, ys = [p[0] for p in r], [p[1] for p in r]
    return (min(xs), min(ys), max(xs), max(ys))


def structs(canopies=(), lamps=()):
    """구조물 묶음(지붕·가로등만): 차양 밑면 103 m(면 100 m 위 3 m)."""
    over = [
        {"id": f"C{i}", "ring": r, "bbox": ring_bbox(r), "fromM": 103.0, "toM": 103.3} for i, r in enumerate(canopies)
    ]
    return {
        "overhead": over,
        "voids": [],
        "lamps": [{"id": f"L{i}", "x": x, "y": y, "z": 100.0} for i, (x, y) in enumerate(lamps)],
    }


def router(surfaces, nodes=None, edges=(), links=(), areas=(), st=None, bands=False):
    p = pkg(nodes or {}, list(edges), areas, surfaces=surfaces, surface_nodes=list(links))
    for e in p["edges"]:  # 길 띠는 길 지표가 있어야 만든다
        e.setdefault("indicators", MapIndex(p).ind[e["key"]])
    p["walkMeshes"] = walkmesh.build_entries(p, st, bands=bands)
    return MapIndex(p).mesh


def straight(geom) -> bool:
    """모든 점이 처음–끝 직선 위(5 cm 안)."""
    (x0, y0), (x1, y1) = geom[0][:2], geom[-1][:2]
    n = math.hypot(x1 - x0, y1 - y0)
    return all(abs((x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)) / n < 0.05 for p in geom)


def go(r, a, b, mode="shortest"):
    return r.route_points(a, b, mode)[0]


# ---------- 꾸러미 항목 ----------


def test_bits_and_runs_round_trip():
    codes = [0, 15, 1, 2, 4, 8, 15, 3, 12]
    assert walkmesh.decode_bits(walkmesh.encode_bits(codes), len(codes)) == codes
    vals = [0, 0, 1, 1, 1, 0, 2]
    assert walkmesh.encode_runs(vals) == [0, 2, 1, 3, 0, 1, 2, 1]
    assert walkmesh.decode_runs(walkmesh.encode_runs(vals), len(vals)) == vals


def test_square_plaza_entry_and_rebuild():
    e = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[SQ]], {"z": 100.0})]))[0]
    assert e["key"] == "m:s:1" and e["kind"] == "surface" and e["cell"] == 2.0 and (e["cols"], e["rows"]) == (10, 10)
    assert walkmesh.decode_bits(e["tris"], 100) == [15] * 100
    assert len(e["classes"]) == 1 and e["cls"] == [0, 100]
    assert len(e["classes"][0]["f"]) == len(MODES)
    m = walkmesh.Mesh(e, {})
    assert sum(1 for a in m.adj if a) == 11 * 11 + 10 * 10  # 모서리 + 가운데
    assert set(m.edist) == {2000, 1414}


def test_narrow_strip_uses_1m_and_tiny_face_uses_half_metre():
    strip = walkmesh.build_entries(
        pkg({}, [], surfaces=[surface("s:1", [[[[0, 0], [30, 0], [30, 1.5], [0, 1.5]]]], {"z": 100.0})])
    )
    assert strip[0]["cell"] == 1.0
    # 0.9 m 폭 띠를 비스듬히: 1 m 칸으로는 거의 안 덮인다 → 0.5 m
    tilted = [[0, 0], [20, 14], [19.48, 14.74], [-0.52, 0.74]]
    tiny = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[tilted]], {"z": 100.0})]))[0]
    assert tiny["cell"] == 0.5 and tiny["cover"] > 0.27


def test_open_area_under_other_height_surface_is_cut_out():
    """열린 면 a:2(평평 100.0)와 겹친 기운 걷는 면: 겹친 곳은 걷는 면만 그물로 덮는다."""
    a2 = area("a:2", [[0, 0], [40, 0], [40, 20], [0, 20]], z=100.0)
    slope = {"profile": {"origin": [20, 0], "direction": [1, 0], "points": [[0, 100.0], [20, 102.0]]}}
    s = surface("s:T", [[[[20, 0], [40, 0], [40, 20], [20, 20]]]], slope)
    es = walkmesh.build_entries(pkg({}, [], [a2], surfaces=[s]))
    by = {e["key"]: e for e in es}
    assert set(by) == {"m:a:2", "m:s:T"}
    a = by["m:a:2"]
    assert math.isclose(a["cols"] * a["cell"], 20, abs_tol=2.01) or math.isclose(
        a["rows"] * a["cell"], 20, abs_tol=2.01
    )
    covered = (
        sum(bin(c).count("1") for c in walkmesh.decode_bits(a["tris"], a["cols"] * a["rows"])) * a["cell"] ** 2 / 4
    )
    assert 360 <= covered <= 400  # 남은 곳 20 × 20


def test_open_area_far_below_surface_is_kept():
    """지하 주차장 통로(열린 면 140.0) 위 운동장 걷는 면(148.9): 높이가 멀면 빼지 않는다."""
    a5 = area("a:5", [[0, 0], [20, 0], [20, 20], [0, 20]], z=140.0)
    s = surface("s:F", [[SQ]], {"z": 148.9})
    by = {e["key"]: e for e in walkmesh.build_entries(pkg({}, [], [a5], surfaces=[s]))}
    assert by["m:a:5"]["cover"] > 0.9 and walkmesh.decode_bits(by["m:a:5"]["tris"], 100) == [15] * 100


def test_cell_classes_from_canopy_lamp_and_slope():
    s = surface("s:1", [[SQ]], {"z": 100.0})
    st = structs(canopies=[[[0, 0], [10, 0], [10, 20], [0, 20]]], lamps=[(20, 20)])
    e = walkmesh.build_entries(pkg({}, [], surfaces=[s]), st)[0]
    m = walkmesh.Mesh(e, {})
    left = e["classes"][m.cls[m.usable(1.0, 1.0)]]  # 왼쪽 아래 칸: 지붕 밑, 가로등 멀다
    right = e["classes"][m.cls[m.usable(19.0, 19.0)]]  # 오른쪽 위 칸: 지붕 없음, 가로등 가까움
    assert left["coveredRatio"] == 1.0 and left["litRatio"] is None
    assert right["coveredRatio"] == 0.0 and right["litRatio"] == 1.0
    rain = MODES.index("rainProtected")
    assert left["f"][rain] < right["f"][rain]
    steep = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[SQ]], SLOPE_X)]))[0]
    assert steep["classes"][0]["slopePct"] == 10.0
    assert steep["classes"][0]["f"][MODES.index("accessible")] is None  # 1/12 넘음


def test_band_covers_road_outside_surface_only():
    n = {"a": node("a", -20, 10), "b": node("b", 10, 10)}  # 길 절반이 면(0~20) 안
    e = edge("ab", "a", "b", n)
    p = pkg(n, [e], surfaces=[surface("s:1", [[SQ]], {"z": 100.0})])
    e["indicators"] = MapIndex(p).ind["ab"]
    es = walkmesh.build_entries(p)
    band = next(x for x in es if x["kind"] == "band")
    assert band["key"] == "m:r:ab" and band["edge"] == "ab" and band["widthM"] == 2.0 and band["cell"] == 1.0
    assert band["classes"][0]["edge"] == "ab"
    covered = sum(bin(c).count("1") for c in walkmesh.decode_bits(band["tris"], band["cols"] * band["rows"])) / 4
    assert 30 <= covered <= 45  # 20 m × 2 m + 끝 반원, 면 안은 빠짐


# ---------- 경로 ----------


def test_square_plaza_diagonal_is_one_straight_line():
    r = router([surface("s:1", [[SQ]], {"z": 100.0})])
    out, ends = r.route_points((1.0, 1.0, 100.0), (19.0, 15.0, 100.0), "shortest")
    assert out is not None and ends[0]["attached"] == "surface" and ends[0]["areas"] == ["m:s:1"]
    assert straight(out["geometry"])
    assert math.isclose(out["distanceM"], math.hypot(18, 14), abs_tol=0.01)
    assert len(out["edgeKeys"]) == 1 and out["edgeKeys"][0].startswith("w:0:m:s:1")
    assert out["steps"][0]["kind"] == "open_area"


def test_obstacle_in_middle_goes_around():
    hole = [[8, 6], [12, 6], [12, 14], [8, 14]]
    r = router([surface("s:1", [[SQ, hole]], {"z": 100.0})])
    out = go(r, (2.0, 10.0, 100.0), (18.0, 10.0, 100.0))
    best = 2 * math.hypot(6, 4) + 4
    assert out is not None and best - 0.01 <= out["distanceM"] <= best + 0.6
    from shapely.geometry import LineString, Polygon

    assert LineString([p[:2] for p in out["geometry"]]).intersection(Polygon(hole).buffer(-0.01)).length == 0


def test_concave_face_without_nodes_routes_around_corner():
    """ㄱ자 면: 노드가 없어도 모서리를 돌아 간다(지금 그래프는 못 함)."""
    ell = [[0, 0], [30, 0], [30, 30], [20, 30], [20, 10], [0, 10]]
    r = router([surface("s:1", [[ell]], {"z": 100.0})])
    out = go(r, (2.0, 5.0, 100.0), (25.0, 28.0, 100.0))
    assert out is not None
    assert (
        math.hypot(18, 5) + math.hypot(5, 18) - 0.1 <= out["distanceM"] <= math.hypot(18, 5) + math.hypot(5, 18) + 1.0
    )


def test_two_planes_with_step_do_not_join_without_stairs():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 101.0})
    assert go(router([a, b]), (2.0, 5.0, 100.0), (18.0, 5.0, 101.0)) is None


def test_two_planes_with_step_join_through_stairs():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 101.0})
    n = {"lo": node("lo", 9.5, 5, 100.0), "hi": node("hi", 10.5, 5, 101.0)}
    st = edge("st", "lo", "hi", n, kind="stairs")
    r = router([a, b], n, [st], members("s:a", ["lo"]) + members("s:b", ["hi"]))
    out = go(r, (2.0, 5.0, 100.0), (18.0, 5.0, 101.0))
    assert out is not None and "st" in out["edgeKeys"]
    assert math.isclose(out["distanceM"], 7.5 + st["lengthM"] + 7.5, abs_tol=0.05)
    assert go(r, (2.0, 5.0, 100.0), (18.0, 5.0, 101.0), "avoidStairs") is None


def test_line_stays_straight_across_same_height_seam():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 100.1})
    out = go(router([a, b]), (1.0, 1.0, 100.0), (19.0, 8.0, 100.1))
    assert out is not None and straight(out["geometry"])
    assert math.isclose(out["distanceM"], math.hypot(18, 7), abs_tol=0.02)


def test_sloped_plane_length_is_3d():
    r = router([surface("s:1", [[SQ]], SLOPE_X)])
    out = go(r, (0.5, 10.0, 100.05), (19.5, 10.0, 101.95))
    assert out is not None and straight(out["geometry"])
    assert math.isclose(out["distanceM"], 19 * math.sqrt(1.01), abs_tol=0.01)
    assert math.isclose(out["geometry"][len(out["geometry"]) // 2][2], 101.0, abs_tol=0.11)


def test_steep_plane_has_no_route_for_wheelchair():
    steep = {"profile": {"origin": [0, 0], "direction": [1, 0], "points": [[0, 100.0], [20, 104.0]]}}  # 20 %
    r = router([surface("s:1", [[SQ]], steep)])
    assert go(r, (1.0, 10.0, 100.2), (19.0, 10.0, 103.8), "accessible") is None
    assert go(r, (1.0, 10.0, 100.2), (19.0, 10.0, 103.8), "shortest") is not None


def test_package_node_attaches_and_graph_edges_still_work():
    n = {"o": node("o", -10, 10), "a": node("a", 0, 10)}
    r = router([surface("s:1", [[SQ]], {"z": 100.0})], n, [edge("oa", "o", "a", n)], members("s:1", ["a"]))
    out, ends = r.route_points("o", (19.0, 19.0, 100.0), "shortest")
    assert out is not None and out["edgeKeys"][0] == "oa" and ends[0] == {"nodeKey": "o", "attached": "node"}
    assert math.isclose(out["distanceM"], 10 + math.hypot(19, 9), abs_tol=0.01)
    assert out["nodeKeys"][0] == "o" and out["nodeKeys"][-1] == "q:to"


def test_node_to_node_across_plaza_and_all_modes():
    n = {k: node(k, x, y) for k, x, y in [("a", 0, 0), ("b", 20, 0), ("c", 20, 20), ("d", 0, 20)]}
    e = [edge("ab", "a", "b", n), edge("bc", "b", "c", n), edge("cd", "c", "d", n), edge("da", "d", "a", n)]
    r = router([surface("s:1", [[SQ]], {"z": 100.0})], n, e, members("s:1", list(n)))
    for m in MODES:
        out = go(r, "a", "c", m)
        assert out is not None, m
        assert math.isclose(out["distanceM"], math.hypot(20, 20), abs_tol=0.01), m
        assert out["mode"] == m and out["durationS"] > 0


def test_rain_mode_keeps_to_covered_cells_and_line_does_not_cross_classes():
    """왼쪽 절반(x<10)만 지붕: 비 안 맞는 길은 지붕 밑으로 돌고, 직선이 지붕 경계를 넘지 않는다."""
    s = surface("s:1", [[[[0, 0], [40, 0], [40, 20], [0, 20]]]], {"z": 100.0})
    st = structs(canopies=[[[0, 0], [10, 0], [10, 20], [0, 20]]])
    r = router([s], st=st)
    a, b = (2.0, 1.0, 100.0), (2.0, 19.0, 100.0)
    short, rain = go(r, a, b, "shortest"), go(r, a, b, "rainProtected")
    assert short is not None and rain is not None
    assert math.isclose(rain["distanceM"], 18.0, abs_tol=0.01)  # 지붕 밑 곧은 길
    assert rain["summary"]["rainExposedM"] == 0.0
    a2, b2 = (2.0, 1.0, 100.0), (30.0, 19.0, 100.0)
    out = go(r, a2, b2, "shortest")
    assert (
        out is not None and len(out["edgeKeys"]) == 1 and straight(out["geometry"])
    )  # 최단은 지붕 칸·맨 칸 비용이 같다
    assert 0.2 < out["summary"]["rainExposedM"] / out["distanceM"] < 0.8 or out["summary"]["rainExposedM"] > 0
    out = go(r, a2, b2, "rainProtected")
    assert out is not None and len(out["edgeKeys"]) == 2  # 비 안 맞는 길: 지붕 칸 → 맨 칸에서 나뉨
    mid = out["geometry"][[p[:2] for p in out["geometry"]].index(next(p[:2] for p in out["geometry"] if p[0] >= 9.99))]
    assert math.isclose(mid[0], 10.0, abs_tol=0.01)


def test_band_joins_road_nodes_and_lets_route_leave_road():
    """길 띠: 노드 없는 길 옆 점에서 출발해 길로 들어간다."""
    n = {"a": node("a", 0, 0), "b": node("b", 40, 0)}
    e = edge("ab", "a", "b", n)
    r = router([], n, [e], bands=True)
    out, ends = r.route_points((20.0, 0.6, 100.0), "b", "shortest")
    assert out is not None and ends[0]["attached"] == "surface" and ends[0]["areas"] == ["m:r:ab"]
    assert math.isclose(out["distanceM"], math.hypot(20, 0.6), abs_tol=0.05)
    assert out["steps"][0]["kind"] == "walkway"


def test_same_result_twice_and_temp_does_not_change_index():
    r = router([surface("s:1", [[SQ]], {"z": 100.0})])
    a = go(r, (1.0, 1.0, 100.0), (19.0, 15.0, 100.0))
    b = go(r, (1.0, 1.0, 100.0), (19.0, 15.0, 100.0))
    a.pop("timeMs")
    b.pop("timeMs")
    assert a == b


def test_narrow_neck_that_splits_the_grid_uses_smaller_cells():
    """두 마당을 1.3 m 폭 비스듬한 목이 잇는다: 1 m 칸으로 끊기면 0.5 m 칸."""
    from shapely.geometry import LineString, box

    g = box(0, 0, 10, 10).union(box(17, 7, 27, 17)).union(LineString([(9, 9), (18, 8)]).buffer(0.65))
    poly = [list(g.exterior.coords)]
    e = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [poly], {"z": 100.0})]))[0]
    assert walkmesh._pieces(walkmesh.decode_bits(e["tris"], e["cols"] * e["rows"]), e["cols"], e["rows"]) == 1
    r = router([surface("s:1", [poly], {"z": 100.0})])
    assert go(r, (2.0, 2.0, 100.0), (25.0, 15.0, 100.0)) is not None


def test_point_on_dead_end_piece_falls_back_to_nearest_node():
    """노드도 이음도 없는 떨어진 조각 위 점: 그물에 안 붙이고 가까운 노드(전과 같음)."""
    main_ = [[0, 0], [20, 0], [20, 20], [0, 20]]
    lone = [[30, 0], [40, 0], [40, 10], [30, 10]]
    n = {"a": node("a", 10, 10), "b": node("b", 25, 5)}
    r = router([surface("s:1", [[main_], [lone]], {"z": 100.0})], n, [edge("ab", "a", "b", n)], members("s:1", ["a"]))
    out, ends = r.route_points((35.0, 5.0, 100.0), "a", "shortest")
    assert ends[0] == {"nodeKey": "b", "attached": "nearest_node"} and out is not None
    # 두 끝이 같은 조각이면 그 조각 안에서 간다
    out, ends = r.route_points((32.0, 2.0, 100.0), (38.0, 8.0, 100.0), "shortest")
    assert (
        ends[0]["attached"] == "surface"
        and out is not None
        and math.isclose(out["distanceM"], math.hypot(6, 6), abs_tol=0.01)
    )


# ---------- 가져오기·꾸러미·API ----------


def test_transform_makes_meshes_and_package_round_trips(clean):
    from test_surfaces import _src

    from mapdata import release, transform

    c, rep = transform.build(_src())
    assert rep["walkMeshes"]["surfaces"] == 1
    keys = [m["key"] for m in c["walkMeshes"]]
    assert keys[0] == "m:s:P1" and all(k.startswith("m:r:") for k in keys[1:])
    rid = release.create_draft(clean, c, rep, {"label": "mesh"})
    clean.commit()
    pkg = release.build_package(clean, rid)
    assert pkg["walkMeshes"] == c["walkMeshes"]
    assert release.validate(pkg)["errors"] == []
    out, _ = MapIndex(pkg).route_points("n:a", "n:c", "shortest")
    assert out is not None and out["edgeKeys"][0].startswith("w:")


def test_api_routes_over_mesh_for_all_modes(clean):
    from fastapi.testclient import TestClient
    from test_surfaces import _src

    import main
    from mapdata import release, transform

    c, rep = transform.build(_src())
    rid = release.create_draft(clean, c, rep, {"label": "mesh"})
    clean.commit()
    release.publish(clean, rid)
    clean.commit()
    client = TestClient(main.app)
    for mode in MODES:
        r = client.get("/v1/routes", params={"from": "3,12,100.3", "to": "17,16,101.7", "mode": mode})
        assert r.status_code in (200, 404), mode
        if r.status_code == 200:
            body = r.json()
            assert body["from"]["attached"] == "surface" and body["from"]["areas"] == ["m:s:P1"]
            assert body["mode"] == mode and body["steps"][0]["kind"] == "open_area"
    body = client.get("/v1/routes", params={"from": "3,12,100.3", "to": "17,16,101.7"}).json()
    assert straight(body["geometry"]) and math.isclose(body["distanceM"], math.hypot(14, 4) * 1.0, rel_tol=0.01)


def test_one_way_road_gets_no_band():
    n = {"a": node("a", 0, 0), "b": node("b", 40, 0)}
    e = edge("ab", "a", "b", n)
    e["direction"] = "forward"
    p = pkg(n, [e])
    e["indicators"] = MapIndex(p).ind["ab"]
    assert walkmesh.build_entries(p) == []


def test_line_crosses_seam_gap_between_misaligned_grids():
    """격자 방향이 다른 두 면이 맞닿은 이음의 틈(어느 세모에도 안 드는 얇은 틈)은 건넌다."""
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 1.5], [19, 11.5], [10, 10]]]], {"z": 100.05})
    out = go(router([a, b]), (1.0, 5.0, 100.0), (18.0, 6.0, 100.05))
    assert out is not None and straight(out["geometry"])


def test_line_does_not_jump_hole_inside_one_mesh():
    hole = [[9.6, 0.5], [10.4, 0.5], [10.4, 19.5], [9.6, 19.5]]  # 0.8 m 폭 벽(한 그물 안 구멍)
    r = router([surface("s:1", [[SQ, hole]], {"z": 100.0})])
    out = go(r, (5.0, 10.0, 100.0), (15.0, 10.0, 100.0))
    assert out is not None and out["distanceM"] > 18
