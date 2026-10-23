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
    p["walkMeshes"], p["walkMeshClasses"] = walkmesh.build_entries(p, st, bands=bands)
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
    es, classes = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[SQ]], {"z": 100.0})]))
    e = es[0]
    assert e["key"] == "m:s:1" and e["kind"] == "surface" and e["cell"] == 2.0 and (e["cols"], e["rows"]) == (10, 10)
    assert walkmesh.decode_bits(e["tris"], 100) == [15] * 100
    assert "classes" not in e and len(classes) == 1 and e["cls"] == [0, 100]
    assert len(classes[0]["f"]) == len(MODES)
    m = walkmesh.Mesh(e, {}, classes)
    assert sum(1 for a in m.adj if a) == 11 * 11 + 10 * 10  # 모서리 + 가운데
    assert set(m.edist) == {2000, 1414}


def test_narrow_strip_uses_1m_and_tiny_face_uses_half_metre():
    strip, _ = walkmesh.build_entries(
        pkg({}, [], surfaces=[surface("s:1", [[[[0, 0], [30, 0], [30, 1.5], [0, 1.5]]]], {"z": 100.0})])
    )
    assert strip[0]["cell"] == 1.0
    # 0.95 m 폭 띠를 비스듬히: 1 m 칸으로는 아래 세모 한 줄(26 %)만 덮인다 → 0.5 m
    tilted = [[0, 0], [20, 14], [19.455, 14.778], [-0.545, 0.778]]
    tiny = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[tilted]], {"z": 100.0})]))[0][0]
    assert tiny["cell"] == 0.5 and tiny["cover"] > 0.27


def test_open_area_under_other_height_surface_is_cut_out():
    """열린 면 a:2(평평 100.0)와 겹친 기운 걷는 면: 겹친 곳은 걷는 면만 그물로 덮는다."""
    a2 = area("a:2", [[0, 0], [40, 0], [40, 20], [0, 20]], z=100.0)
    slope = {"profile": {"origin": [20, 0], "direction": [1, 0], "points": [[0, 100.0], [20, 102.0]]}}
    s = surface("s:T", [[[[20, 0], [40, 0], [40, 20], [20, 20]]]], slope)
    es, _ = walkmesh.build_entries(pkg({}, [], [a2], surfaces=[s]))
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
    by = {e["key"]: e for e in walkmesh.build_entries(pkg({}, [], [a5], surfaces=[s]))[0]}
    assert by["m:a:5"]["cover"] > 0.9 and walkmesh.decode_bits(by["m:a:5"]["tris"], 100) == [15] * 100


def test_cell_classes_from_canopy_lamp_and_slope():
    s = surface("s:1", [[SQ]], {"z": 100.0})
    st = structs(canopies=[[[0, 0], [10, 0], [10, 20], [0, 20]]], lamps=[(20, 20)])
    es, classes = walkmesh.build_entries(pkg({}, [], surfaces=[s]), st)
    m = walkmesh.Mesh(es[0], {}, classes)
    left = classes[m.cls[m.usable(1.0, 1.0)]]  # 왼쪽 아래 칸: 지붕 밑, 가로등 멀다
    right = classes[m.cls[m.usable(19.0, 19.0)]]  # 오른쪽 위 칸: 지붕 없음, 가로등 가까움
    assert left["coveredRatio"] == 1.0 and left["litRatio"] is None
    assert right["coveredRatio"] == 0.0 and right["litRatio"] == 1.0
    rain = MODES.index("rainProtected")
    assert left["f"][rain] < right["f"][rain]
    _, steep = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[SQ]], SLOPE_X)]))
    assert steep[0]["slopePct"] == 10.0
    assert steep[0]["f"][MODES.index("accessible")] is None  # 1/12 넘음


def test_band_covers_road_outside_surface_only():
    n = {"a": node("a", -20, 10), "b": node("b", 10, 10)}  # 길 절반이 면(0~20) 안
    e = edge("ab", "a", "b", n)
    p = pkg(n, [e], surfaces=[surface("s:1", [[SQ]], {"z": 100.0})])
    e["indicators"] = MapIndex(p).ind["ab"]
    es, classes = walkmesh.build_entries(p)
    band = next(x for x in es if x["kind"] == "band")
    assert band["key"] == "m:r:ab" and band["edge"] == "ab" and band["widthM"] == 2.0 and band["cell"] == 1.0
    assert classes[band["cls"][0]]["f"] == walkmesh.band_class(e)["f"]
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
    e = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [poly], {"z": 100.0})]))[0][0]
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
    assert pkg["walkMeshes"] == c["walkMeshes"] and pkg["walkMeshClasses"] == c["walkMeshClasses"]
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
    assert walkmesh.build_entries(p) == ([], [])


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


# ---------- 공용 등급 표 ----------


def test_class_table_is_shared_by_all_meshes():
    """같은 등급(평평·지붕 모름·차 없음)인 두 면은 꾸러미 공용 표의 한 줄을 같이 쓴다."""
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[30, 0], [40, 0], [40, 10], [30, 10]]]], {"z": 105.0})
    es, classes = walkmesh.build_entries(pkg({}, [], surfaces=[a, b]))
    assert len(classes) == 1 and all(e["cls"][0] == 0 and "classes" not in e for e in es)


# ---------- 이음은 얇은 벽을 넘지 않는다 ----------


def test_seam_does_not_join_across_thin_wall():
    """같은 높이(차 5 cm, 높이 함수가 달라 다른 그물) 두 면 사이 0.3 m 벽: 이음 없음(계단·문 없이 못 감). 맞닿으면 이음."""
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    walled = surface("s:b", [[[[10.3, 0], [20, 0], [20, 10], [10.3, 10]]]], {"z": 100.05})
    es, _ = walkmesh.build_entries(pkg({}, [], surfaces=[a, walled]))
    assert not any(e.get("seams") for e in es)
    assert go(router([a, walled]), (2.0, 5.0, 100.0), (18.0, 5.0, 100.05)) is None
    touching = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 100.05})
    es, _ = walkmesh.build_entries(pkg({}, [], surfaces=[a, touching]))
    assert es[0]["seams"][0][0] == "m:s:b" and len(es[0]["seams"][0][1]) >= 2
    assert go(router([a, touching]), (2.0, 5.0, 100.0), (18.0, 5.0, 100.05)) is not None


def test_line_does_not_cut_through_thin_wall_between_two_meshes():
    """ㄷ자: 아래 면 A·위 면 C 사이 0.3 m 벽, 오른쪽 면 B 가 둘을 잇는다. A → C 직선이 벽을 넘지 않는다."""
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 4], [0, 4]]]], {"z": 100.0})
    c = surface("s:c", [[[[0, 4.3], [10, 4.3], [10, 8.3], [0, 8.3]]]], {"z": 100.1})
    b = surface("s:b", [[[[10, 0], [14, 0], [14, 8.3], [10, 8.3]]]], {"z": 100.05})
    out = go(router([a, b, c]), (1.0, 3.0, 100.0), (1.0, 5.3, 100.1))
    assert out is not None and out["distanceM"] > 18


# ---------- 실내 층 그물 ----------


def indoor_case(outdoor=False, door=False, lobby_h=10.0, ramp=False):
    """X 동 1F(100 m) 로비 빈 곳 20 × lobby_h m(복도 e:aaaaaaaa 를 덮음), 2F(104 m)는 빈 곳 없는 복도 e:bbbbbbbb(띠),
    계단 e:cccccccc 이 잇는다. outdoor 면 s:o 는 로비 서쪽에 같은 높이로 맞닿음, door 는 문 길.
    ramp 는 로비 안 경사로 e:dddddddd(x 5 → 15, y 2, 100.0 → 100.6, 폭 1.5 m)를 더하고 빈 곳 roads 에 넣는다."""
    n = {
        "a1": node("a1", 1, 5, 100.0, "indoor"),
        "s1": node("s1", 18, 5, 100.0, "indoor"),
        "s2": node("s2", 19, 5, 104.0, "indoor"),
        "b2": node("b2", 1, 5, 104.0, "indoor"),
    }
    for k in n:
        n[k].update(buildingKey="b:X", floorKey="f:X:1F" if n[k]["z"] < 102 else "f:X:2F")
    es = [
        edge("e:aaaaaaaa-1", "a1", "s1", n, kind="corridor"),
        edge("e:cccccccc-1", "s1", "s2", n, kind="stairs"),
        edge("e:bbbbbbbb-1", "s2", "b2", n, kind="corridor"),
    ]
    es[0]["widthM"] = es[2]["widthM"] = 2.4
    surfs = []
    if outdoor:  # 바깥 면 위 노드 o(면 위 점이 막힌 조각으로 안 보이게)
        surfs.append(surface("s:o", [[[[-10, 0], [0, 0], [0, 10], [-10, 10]]]], {"z": 100.0}))
        n["o"] = node("o", -8, 2, 100.0)
    if ramp:
        n["r1"] = node("r1", 5, 2, 100.0, "indoor")
        n["r2"] = node("r2", 15, 2, 100.6, "indoor")
        for k in ("r1", "r2"):
            n[k].update(buildingKey="b:X", floorKey="f:X:1F")
        es.append(edge("e:dddddddd-1", "r1", "r2", n, kind="ramp"))
        es[-1]["widthM"] = 1.5
    if door:
        n["d"] = node("d", -0.5, 5, 100.0)
        n["i"] = node("i", 0.5, 5, 100.0, "indoor")
        n["i"].update(buildingKey="b:X", floorKey="f:X:1F")
        es += [edge("door", "d", "i", n, kind="corridor"), edge("e:aaaaaaaa-2", "i", "a1", n, kind="corridor")]
    p = pkg(n, es, surfaces=surfs, surface_nodes=members("s:o", ["d"]) if door else [])
    p["buildings"] = [{"key": "b:X", "name": "X"}]
    p["floors"] = [
        {"key": "f:X:1F", "buildingKey": "b:X", "code": "1F", "floorZ": 100.0},
        {"key": "f:X:2F", "buildingKey": "b:X", "code": "2F", "floorZ": 104.0},
    ]
    interiors = [
        {
            "building": "X",
            "kind": "lobby",
            "floors": ["1층"],
            "floorsM": [100.0],
            "roads": ["aaaaaaaa"] + (["dddddddd"] if ramp else []),
            "polygons": [[[[0, 0], [20, 0], [20, lobby_h], [0, lobby_h]]]],
        },
        {  # 계단실은 걷는 바닥이 아니다
            "building": "X",
            "kind": "stair",
            "floors": ["1층", "2층"],
            "floorsM": [100.0, 104.0],
            "roads": ["cccccccc"],
            "polygons": [[[[17, 4], [20, 4], [20, 6], [17, 6]]]],
        },
    ]
    for e in p["edges"]:
        e.setdefault("indicators", MapIndex(p).ind[e["key"]])
    p["walkMeshes"], p["walkMeshClasses"] = walkmesh.build_entries(p, None, interiors=interiors)
    return p


def test_indoor_floor_meshes_from_voids_and_corridor_bands():
    p = indoor_case()
    by = {e["key"]: e for e in p["walkMeshes"] if e["kind"] == "floor"}
    assert set(by) == {"m:i:X:1F", "m:i:X:2F"}
    f1, f2 = by["m:i:X:1F"], by["m:i:X:2F"]
    assert f1["top"] == {"z": 100.0} and f1["floorKey"] == "f:X:1F" and f1["buildingKey"] == "b:X"
    assert f1["cell"] == 1.0 and f1["cover"] > 0.9  # 로비 빈 곳 20 × 10
    assert f2["top"] == {"z": 104.0} and f2["cell"] <= 1.0  # 빈 곳 없는 복도: 2.4 m 띠
    n2 = sum(bin(c).count("1") for c in walkmesh.decode_bits(f2["tris"], f2["cols"] * f2["rows"])) * f2["cell"] ** 2 / 4
    assert 35 <= n2 <= 2.4 * 18 + 5
    cls = p["walkMeshClasses"][f1["cls"][0]]
    assert cls["coveredRatio"] == 1.0 and cls["vehicle"] == "none"


def test_indoor_lobby_diagonal_and_floor_change_only_by_stairs():
    r = MapIndex(indoor_case()).mesh
    out = go(r, (2.0, 1.0, 100.0), (17.0, 9.0, 100.0))
    assert out is not None and straight(out["geometry"]) and len(out["edgeKeys"]) == 1
    assert out["steps"][0]["kind"] == "corridor" and out["summary"]["indoorM"] == round(out["distanceM"], 1)
    up = go(r, (2.0, 8.0, 100.0), (5.0, 5.0, 104.0))
    assert up is not None and "e:cccccccc-1" in up["edgeKeys"]
    assert up["nodeKeys"][-1] == "q:to" and up["steps"][-1]["to"]["floorKey"] == "f:X:2F"
    assert go(r, (2.0, 8.0, 100.0), (5.0, 5.0, 104.0), "avoidStairs") is None


def test_indoor_and_outdoor_meshes_join_only_through_door_edge():
    r = MapIndex(indoor_case(outdoor=True)).mesh
    out, ends = r.route_points((-5.0, 5.0, 100.0), (5.0, 5.0, 100.0), "shortest")
    assert ends[0]["areas"] == ["m:s:o"] and out is None  # 같은 높이로 맞닿아도 벽(문 없음)
    r = MapIndex(indoor_case(outdoor=True, door=True)).mesh
    out = go(r, (-5.0, 8.0, 100.0), (5.0, 8.0, 100.0))
    assert out is not None and "door" in out["edgeKeys"]
    # 바깥 노드는 실내 그물에, 실내 노드는 바깥 그물에 붙지 않는다
    for i, hits in r.node_att.items():
        loc = r.idx.pkg["nodes"][i]["location"]
        assert all((r.meshes[mi].kind == "floor") == (loc == "indoor") for mi, _ in hits)


# ---------- 덧붙임 3: 큰 좌표 격자 원점, 좁은 실내 띠, 빈 곳 밑 경사로 ----------


def tilted_strip(x0, y0, length, width, d=(0.908682, -0.41749)):
    n = math.hypot(*d)
    ux, uy = d[0] / n, d[1] / n
    return [
        [x0, y0],
        [x0 + length * ux, y0 + length * uy],
        [x0 + length * ux - width * uy, y0 + length * uy + width * ux],
        [x0 - width * uy, y0 + width * ux],
    ]


def test_grid_origin_sits_on_face_corner_at_large_coordinates():
    """5186 큰 좌표에서도 원점이 면의 축 좌표 최소 모서리(1 cm 안)에 놓인다. 축 반올림으로 0.5 m, 원점 cm 반올림으로
    1~2 cm 어긋나면 2.4 m 복도의 1 m 칸 한 줄이 빠져 반만 덮였다(문예관 복도)."""
    for k in range(10):
        ring = tilted_strip(201088.91 + k * 0.0037, 557356.41 + k * 0.0061, 30.0, 2.4)
        e = walkmesh.build_entries(pkg({}, [], surfaces=[surface("s:1", [[ring]], {"z": 100.0})]))[0][0]
        m = walkmesh.Mesh(e, {}, [])
        ab = [m.local(x, y) for x, y in ring]
        assert -0.01 < min(a for a, _ in ab) * e["cell"] < 0.01 and -0.01 < min(b for _, b in ab) * e["cell"] < 0.01
        assert e["cover"] > 0.8


def floor_mesh(p, key="m:i:X:1F"):
    e = next(e for e in p["walkMeshes"] if e["key"] == key)
    return e, walkmesh.Mesh(e, {x["key"]: x for x in p["edges"]}, p["walkMeshClasses"])


def test_narrow_indoor_corridor_uses_half_metre_cells():
    """1.8 m 보다 좁은 실내 띠(빈 곳·복도)가 있는 층은 0.5 m 칸(1 m 칸은 반쯤만 덮음). 넓으면 1 m 그대로."""
    e, _ = floor_mesh(indoor_case(lobby_h=1.5))
    assert e["cell"] == 0.5 and e["cover"] > 0.8
    e, _ = floor_mesh(indoor_case(lobby_h=2.4))
    assert e["cell"] == 1.0


def test_ramp_under_void_is_cut_out_of_flat_floor_and_kept_as_edge():
    """빈 곳이 덮은 경사로(높이 차 0.2 m 넘음)는 평평한 바닥 그물에서 경사로 폭만큼 뺀다. 경사로는 길 간선으로 간다."""
    p = indoor_case(ramp=True)
    _, m = floor_mesh(p)
    assert m.usable(10.0, 2.0) is None and m.usable(4.0, 2.0) is not None and m.usable(10.0, 6.0) is not None
    r = MapIndex(p).mesh
    out = go(r, (3.0, 2.0, 100.0), (16.5, 2.0, 100.0))
    assert out is not None and "e:dddddddd-1" not in out["edgeKeys"]  # 평평한 바닥으로 경사로를 돌아감
    up = MapIndex(p).route("r1", "r2", "shortest")
    assert up is not None and "e:dddddddd-1" in up["edgeKeys"]
