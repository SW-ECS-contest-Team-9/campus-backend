"""기단 면(구조물 platform) 위 가로지르기: 면 만들기(가져올 때), 경로(읽을 때), 꾸러미 왕복."""

import math

from test_routing import edge, node, pkg

from fixture_source import road
from mapdata import indicators, release, surfaces, transform
from mapdata.routing import MapIndex

SQ = [[0, 0], [20, 0], [20, 20], [0, 20]]
SLOPE_X = {"profile": {"origin": [0, 0], "direction": [1, 0], "points": [[0, 100.0], [20, 102.0]]}}  # x 로 10 % 오르막


def platform(pid, ring, top_m=None, profile=None, material="paving"):
    return {"id": pid, "ring": ring, "topM": top_m, "topProfile": profile, "material": material}


def solid(sid, ring, from_m, to_m, typ="wall"):
    return {"id": sid, "type": typ, "ring": ring, "fromM": from_m, "toM": to_m}


def surface(key, polygons, top, name=""):
    return {"key": key, "name": name, "material": "paving", "top": top, "polygons": polygons, "estimated": True}


def members(skey, keys):
    return [{"surfaceKey": skey, "nodeKey": k} for k in keys]


# ---------- 면 만들기 ----------


def test_surface_height_flat_and_profile():
    assert surfaces.surface_z({"z": 130.0}, 5, 5) == 130.0
    assert surfaces.surface_z(SLOPE_X, 10, 3) == 101.0
    assert surfaces.surface_z(SLOPE_X, -5, 0) == 100.0  # 끝 밖은 끝 높이


def test_build_clips_to_walkable_outline_and_picks_same_height_nodes():
    nodes = [
        node("n:a", 1, 1, 100.1),
        node("n:b", 19, 1, 101.9),
        node("n:up", 10, 10, 104.0),  # 면 위 3 m: 다른 단
        node("n:out", 19, 19, 101.9),  # 면 위 높이지만 걷는 곳 외곽 밖
        node("n:in", 10, 10, 101.0, location="indoor"),
    ]
    outline = [[[-5, -5], [25, -5], [25, 10], [-5, 10]]]  # 면 아래 절반만
    out, links, rep = surfaces.build([platform("P1", SQ, profile=SLOPE_X["profile"])], [], outline, [], [], nodes)
    assert len(out) == 1
    s = out[0]
    assert s["key"] == "s:P1" and s["top"] == SLOPE_X
    assert math.isclose(sum(_area(p[0]) for p in s["polygons"]), 200.0, rel_tol=1e-6)
    assert {w["nodeKey"] for w in links} == {"n:a", "n:b"}


def test_build_excludes_planting_and_merges_same_top():
    plats = [
        platform("A", [[0, 0], [10, 0], [10, 10], [0, 10]], top_m=100.0),
        platform("B", [[10, 0], [20, 0], [20, 10], [10, 10]], top_m=100.0),  # 같은 높이 이웃: 한 면
        platform("BED", [[0, 10], [20, 10], [20, 20], [0, 20]], top_m=100.0, material="planting"),
    ]
    out, _, _ = surfaces.build(plats, [], [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], [], [], [])
    assert len(out) == 1
    assert math.isclose(sum(_area(p[0]) for p in out[0]["polygons"]), 200.0, rel_tol=1e-6)


def test_build_cuts_obstacles_that_stand_on_the_surface_only():
    plats = [platform("P", SQ, top_m=100.0)]
    sols = [
        solid("WALL", [[9, 5], [11, 5], [11, 15], [9, 15]], 99.0, 101.0),  # 면 위로 1 m: 구멍
        solid("CURB", [[2, 2], [4, 2], [4, 3], [2, 3]], 100.0, 100.1),  # 10 cm 턱: 넘어감
        solid("RETAIN", [[15, 15], [17, 15], [17, 17], [15, 17]], 95.0, 100.0),  # 면 아래 옹벽 머리
        solid("CANOPY", [[2, 15], [5, 15], [5, 18], [2, 18]], 104.0, 104.3, "canopy"),  # 머리 위
    ]
    out, _, rep = surfaces.build(plats, sols, [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], [], [], [])
    holes = [h for p in out[0]["polygons"] for h in p[1:]]
    assert len(holes) == 1
    assert rep["obstaclesCut"] == 1


def test_build_keeps_flat_dressing_on_slope_but_cuts_wall():
    plats = [platform("P", SQ, profile=SLOPE_X["profile"])]
    sols = [
        solid("PAINT", [[2, 2], [8, 2], [8, 8], [2, 8]], 99.5, 100.9),  # 기운 면(100.2~100.8) 위 평평한 덧판
        solid("WALL", [[12, 2], [13, 2], [13, 8], [12, 8]], 100.0, 102.0),
    ]
    out, _, rep = surfaces.build(plats, sols, [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], [], [], [])
    assert rep["obstaclesCut"] == 1


def test_build_snaps_door_node_just_outside_into_surface():
    # 건물 발자국을 빼면 문 노드가 면 경계 밖 0.3 m 에 놓인다: 면에 붙이고 그 노드에서 면으로 들어가는 길은 남긴다.
    bld = [[[8, 15], [12, 15], [12, 25], [8, 25]]]
    door = node("n:door", 10, 15.3, 100.0)  # 발자국 안 0.3 m
    out, links, _ = surfaces.build(
        [platform("P", SQ, top_m=100.0)], [], [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], bld, [], [door]
    )
    assert links == [{"surfaceKey": "s:P", "nodeKey": "n:door"}]
    idx = MapIndex(
        pkg({"n:door": door, "n:a": node("n:a", 1, 1)}, [], surfaces=out, surface_nodes=links + members("s:P", ["n:a"]))
    )
    assert idx.route("n:door", "n:a", "shortest") is not None


def test_build_names_surface_after_hatched_zone():
    zones = [{"name": "분수 쪽 차도", "polygon": [[-1, -1], [21, -1], [21, 21], [-1, 21]]}]
    out, _, _ = surfaces.build(
        [platform("P", SQ, top_m=100.0)], [], [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], [], zones, []
    )
    assert out[0]["name"] == "분수 쪽 차도"


# ---------- 경로 ----------


def sloped_square():
    n = {
        "a": node("a", 0, 0, 100.0),
        "b": node("b", 20, 0, 102.0),
        "c": node("c", 20, 20, 102.0),
        "d": node("d", 0, 20, 100.0),
    }
    e = [edge("ab", "a", "b", n), edge("bc", "b", "c", n), edge("cd", "c", "d", n), edge("da", "d", "a", n)]
    return n, e


def test_sloped_plane_diagonal():
    n, e = sloped_square()
    idx = MapIndex(pkg(n, e, surfaces=[surface("s:1", [[SQ]], SLOPE_X)], surface_nodes=members("s:1", n)))
    r = idx.route("a", "c", "shortest")
    assert [s["kind"] for s in r["steps"]] == ["open_area"]
    assert r["distanceM"] == round(math.sqrt(20**2 + 20**2 + 2**2), 2)
    zs = [p[2] for p in r["geometry"]]
    assert len(zs) > 2 and zs == sorted(zs)  # 면 높이를 따라 오른다
    assert math.isclose(zs[len(zs) // 2], 101.0, abs_tol=0.15)
    assert r["summary"]["climbM"] == 2.0
    flat_s = math.hypot(20, 20) / 1.2
    assert r["durationS"] > flat_s  # 오르막이라 평지보다 느리다
    back = idx.route("c", "a", "fastest")
    assert back["durationS"] < r["durationS"]  # 내리막(5 % 남짓)은 오르막보다 빠르다


def test_two_planes_with_step_do_not_join():
    # 낮은 면(x 0~10, 100 m)과 높은 면(x 10~20, 101 m)이 맞닿는다. 사이는 벽: 가로지르는 간선이 없다.
    lo, hi = [[0, 0], [10, 0], [10, 10], [0, 10]], [[10, 0], [20, 0], [20, 10], [10, 10]]
    n = {"a": node("a", 2, 5, 100.0), "a2": node("a2", 8, 2, 100.0), "b": node("b", 18, 5, 101.0)}
    ss = [surface("s:lo", [[lo]], {"z": 100.0}), surface("s:hi", [[hi]], {"z": 101.0})]
    # 잘못 넣은 소속(높은 면에 낮은 노드)도 높이로 걸러야 한다
    links = members("s:lo", ["a", "a2"]) + members("s:hi", ["b", "a2"])
    idx = MapIndex(pkg(n, [], surfaces=ss, surface_nodes=links))
    assert idx.route("a", "b", "shortest") is None
    assert idx.route("a", "a2", "shortest")["steps"][0]["kind"] == "open_area"


def test_concave_notch_and_hole_are_not_cut():
    poly = [[0, 0], [20, 0], [20, 20], [12, 20], [12, 5], [8, 5], [8, 20], [0, 20]]
    n = {k: node(k, x, y) for k, x, y in [("a", 2, 18), ("b", 18, 18), ("m", 10, 0)]}
    idx = MapIndex(pkg(n, [], surfaces=[surface("s:1", [[poly]], {"z": 100.0})], surface_nodes=members("s:1", n)))
    r = idx.route("a", "b", "shortest")
    assert r["nodeKeys"] == ["a", "m", "b"]
    # 가운데 구멍(화단·벽): 구멍을 지나는 직선은 없고 옆 노드로 돈다
    hole = [[8, 8], [12, 8], [12, 12], [8, 12]]
    n = {k: node(k, x, y) for k, x, y in [("a", 2, 10), ("b", 18, 10), ("s", 10, 2)]}
    idx = MapIndex(pkg(n, [], surfaces=[surface("s:1", [[SQ, hole]], {"z": 100.0})], surface_nodes=members("s:1", n)))
    assert idx.route("a", "b", "shortest")["nodeKeys"] == ["a", "s", "b"]


def test_surface_and_area_on_same_pair_make_one_edge():
    n, e = sloped_square()
    for k in n:
        n[k]["z"] = 100.0
    area = {
        "key": "a:1",
        "location": "outdoor",
        "floorKey": None,
        "name": "광장",
        "kind": "plaza",
        "polygon": SQ,
        "z": 100.0,
        "estimated": False,
    }
    p = pkg(
        n,
        e,
        [area],
        [{"areaKey": "a:1", "nodeKey": k} for k in n],
        surfaces=[surface("s:1", [[SQ]], {"z": 100.0})],
        surface_nodes=members("s:1", n),
    )
    idx = MapIndex(p)
    assert not [k for k in idx.edges if k.startswith("y:")]  # 열린 면이 이미 이은 쌍은 기단 면이 다시 잇지 않는다
    assert idx.route("a", "c", "shortest")["steps"][0]["instruction"].startswith("광장을 가로질러")


def _area(ring):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))) / 2


# ---------- 가져오기·꾸러미 ----------


def _src():
    return {
        "roads": [
            road("r1", "a", "b", [[1, 1, 100.1], [19, 1, 101.9]]),
            road("r2", "b", "c", [[19, 1, 101.9], [19, 19, 101.9]]),
            road("r3", "c", "d", [[19, 19, 101.9], [1, 19, 100.1]]),
        ],
        "node_levels": {},
        "places": [],
        "buildings": [],
        "open_areas": [],
        "structures": indicators.parse_structures(
            [
                {
                    "type": "Feature",
                    "properties": {
                        "id": "P1",
                        "kind": "platform",
                        "type": "platform",
                        "material": "paving",
                        "topProfile": SLOPE_X["profile"],
                    },
                    "geometry": {"type": "Polygon", "coordinates": [SQ + [SQ[0]]]},
                },
                {
                    "type": "Feature",
                    "properties": {"id": "W1", "kind": "extrude", "type": "wall", "fromM": 99, "toM": 102},
                    "geometry": {"type": "Polygon", "coordinates": [[[13, 4], [15, 4], [15, 6], [13, 6], [13, 4]]]},
                },
            ]
        ),
        "walkable": {"outline": [[[-1, -1], [30, -1], [30, 30], [-1, 30]]], "zones": [], "file": "t.geojson"},
    }


def test_transform_makes_surfaces_and_package_carries_them(clean):
    c, rep = transform.build(_src())
    assert [s["key"] for s in c["walkSurfaces"]] == ["s:P1"]
    assert len(c["walkSurfaces"][0]["polygons"][0]) == 2  # 바깥 + 벽 구멍
    assert {w["nodeKey"] for w in c["walkSurfaceNodes"]} == {"n:a", "n:b", "n:c", "n:d"}
    assert rep["surfaces"]["surfaces"] == 1 and rep["surfaces"]["sloped"] == 1
    rid = release.create_draft(clean, c, rep, {"label": "fixture"})
    clean.commit()
    pkg = release.build_package(clean, rid)
    assert pkg["walkSurfaces"] == c["walkSurfaces"]
    assert pkg["walkSurfaceNodes"] == sorted(c["walkSurfaceNodes"], key=lambda w: (w["surfaceKey"], w["nodeKey"]))
    assert release.validate(pkg)["errors"] == []
    r = MapIndex(pkg).route("n:a", "n:c", "shortest")
    assert [s["kind"] for s in r["steps"]] == ["open_area"]  # 벽 구멍을 비껴 가는 대각선


def test_transform_without_walkable_has_no_surfaces():
    s = _src()
    del s["walkable"]
    c, rep = transform.build(s)
    assert c["walkSurfaces"] == [] and rep["surfaces"] is None


# ---------- 길이 면 경계를 지나는 곳에 노드 (가져올 때) ----------


def _content_edge(key, a, b, coords, kind="walkway", access="allowed"):
    return {
        "key": key,
        "from": a,
        "to": b,
        "name": "앞길",
        "kind": kind,
        "direction": "both",
        "pedestrianAccess": access,
        "wheelchairAccess": "unknown",
        "lengthM": round(transform.poly_len(coords), 2),
        "stepCount": None,
        "widthM": None,
        "geometry": coords,
        "estimated": False,
        "_building": None,
        "_level": None,
    }


def _flat_surface():
    out, links, _ = surfaces.build(
        [platform("P", SQ, top_m=100.0)], [], [[[-30, -30], [50, -30], [50, 50], [-30, 50]]], [], [], []
    )
    return out, links


def test_split_adds_boundary_nodes_where_road_crosses_surface_edge():
    out, links = _flat_surface()
    nodes = [node("n:w", -10, 10, 100.0), node("n:e", 30, 10, 100.0)]
    edges = [_content_edge("e:r", "n:w", "n:e", [[-10, 10, 100.0], [30, 10, 100.0]])]
    nodes2, edges2, links2, rep = surfaces.split_edges(out, links, nodes, edges)
    assert [e["key"] for e in edges2] == ["e:r~1", "e:r~2", "e:r~3"]
    assert [(e["from"], e["to"]) for e in edges2] == [("n:w", "n:r~1"), ("n:r~1", "n:r~2"), ("n:r~2", "n:e")]
    new = {n["key"]: n for n in nodes2 if n["key"].startswith("n:r~")}
    assert (new["n:r~1"]["x"], new["n:r~1"]["y"], new["n:r~1"]["z"]) == (0.0, 10.0, 100.0)
    assert (new["n:r~2"]["x"], new["n:r~2"]["y"]) == (20.0, 10.0)
    assert new["n:r~1"]["location"] == "outdoor" and new["n:r~1"]["kind"] == "junction"
    assert sum(e["lengthM"] for e in edges2) == 40.0
    assert {w["nodeKey"] for w in links2} == {"n:r~1", "n:r~2"}
    assert [0.0, 10.0] in out[0]["polygons"][0][0]  # 노드가 면 경계 꼭짓점에 정확히 놓인다
    assert rep["splitEdges"] == 1 and rep["boundaryNodes"] == 2
    # 이제 면 노드가 있어 면을 가로지른다
    idx = MapIndex(pkg({n["key"]: n for n in nodes2}, edges2, surfaces=out, surface_nodes=links2))
    assert idx.route("n:r~1", "n:r~2", "shortest") is not None


def test_split_skips_other_height_stairs_prohibited_and_near_end():
    out, links = _flat_surface()
    nodes = [
        node("n:a", -10, 5, 103.0),
        node("n:b", 30, 5, 103.0),  # 면보다 3 m 위(다리)
        node("n:c", -10, 15, 100.0),
        node("n:d", 30, 15, 100.0),
        node("n:e", 0.3, 18, 100.0),  # 끝이 경계에서 0.3 m
        node("n:f", -10, 18, 100.0),
    ]
    edges = [
        _content_edge("e:hi", "n:a", "n:b", [[-10, 5, 103.0], [30, 5, 103.0]]),
        _content_edge("e:st", "n:c", "n:d", [[-10, 15, 100.0], [30, 15, 100.0]], kind="stairs"),
        _content_edge("e:no", "n:c", "n:d", [[-10, 15, 100.0], [30, 15, 100.0]], access="prohibited"),
        _content_edge("e:near", "n:f", "n:e", [[-10, 18, 100.0], [0.3, 18, 100.0]]),
    ]
    nodes2, edges2, links2, rep = surfaces.split_edges(out, links, nodes, edges)
    assert [e["key"] for e in edges2] == ["e:hi", "e:st", "e:no", "e:near"]
    assert len(nodes2) == len(nodes) and links2 == links and rep["boundaryNodes"] == 0


def test_split_on_sloped_surface_uses_surface_height_at_crossing():
    out, links, _ = surfaces.build(
        [platform("P", SQ, profile=SLOPE_X["profile"])], [], [[[-30, -30], [50, -30], [50, 50], [-30, 50]]], [], [], []
    )
    # 남북으로 지나는 길(x = 10, 면 높이 101): 아래(y=0)·위(y=20) 경계에서 노드
    nodes = [node("n:s", 10, -10, 101.0), node("n:n", 10, 30, 101.0)]
    edges = [_content_edge("e:ns", "n:s", "n:n", [[10, -10, 101.0], [10, 30, 101.0]])]
    _, _, links2, _ = surfaces.split_edges(out, links, nodes, edges)
    assert {w["nodeKey"] for w in links2} == {"n:ns~1", "n:ns~2"}


def test_transform_splits_road_through_surface_without_nodes():
    s = _src()
    # 면(x 0~20, 높이 100~102)을 동서로 지나는 길: 면 안에 노드가 없다
    s["roads"] = [road("rx", "w", "e", [[-10, 10, 100.0], [0, 10, 100.0], [20, 10, 102.0], [30, 10, 102.0]])]
    c, rep = transform.build(s)
    assert [e["key"] for e in c["edges"]] == ["e:rx~1", "e:rx~2", "e:rx~3"]
    assert {w["nodeKey"] for w in c["walkSurfaceNodes"]} == {"n:rx~1", "n:rx~2"}
    assert all(e.get("indicators") for e in c["edges"])  # 나눈 길도 지표가 있다
    assert rep["surfaces"]["boundaryNodes"] == 2 and rep["surfaces"]["seamNodes"] == 0


# ---------- 면 위 아무 점에서 출발·도착 (읽을 때) ----------


def test_point_on_surface_without_nodes_inside_connects_to_surface_nodes():
    n, e = sloped_square()
    idx = MapIndex(pkg(n, e, surfaces=[surface("s:1", [[SQ]], SLOPE_X)], surface_nodes=members("s:1", n)))
    r, ends = idx.route_points((10.0, 5.0, 101.0), "c", "shortest")
    assert ends[0]["attached"] == "surface" and ends[0]["nodeKey"] == "q:from"
    assert r["nodeKeys"] == ["q:from", "c"] and r["edgeKeys"] == ["y:s:1:q:from:c"]
    assert r["steps"][0]["kind"] == "open_area"
    assert math.isclose(r["distanceM"], math.sqrt(10**2 + 15**2 + 1**2), abs_tol=0.02)
    assert r["geometry"][0] == [10.0, 5.0, 101.0]


def test_point_z_snaps_to_surface_and_wrong_height_falls_back_to_nearest_node():
    n, e = sloped_square()
    idx = MapIndex(pkg(n, e, surfaces=[surface("s:1", [[SQ]], SLOPE_X)], surface_nodes=members("s:1", n)))
    r, ends = idx.route_points((10.0, 5.0, 101.3), "c", "shortest")
    assert ends[0]["attached"] == "surface" and r["geometry"][0][2] == 101.0
    r, ends = idx.route_points((1.0, 1.0, 101.8), "c", "shortest")  # 면보다 1.7 m 위: 면이 아님 → 가까운 노드
    assert ends[0] == {"nodeKey": "a", "attached": "nearest_node"}
    assert r["nodeKeys"][0] == "a"
    assert idx.route_points((500.0, 500.0, 100.0), "c", "shortest") == (
        None,
        [None, {"nodeKey": "c", "attached": "node"}],
    )


def test_two_points_on_same_surface_join_directly_and_holes_block():
    hole = [[8, 8], [12, 8], [12, 12], [8, 12]]
    n = {k: node(k, x, y) for k, x, y in [("s", 10, 2), ("t", 10, 18)]}
    idx = MapIndex(pkg(n, [], surfaces=[surface("s:1", [[SQ, hole]], {"z": 100.0})], surface_nodes=members("s:1", n)))
    r, _ = idx.route_points((2.0, 2.0, 100.0), (18.0, 2.0, 100.0), "shortest")
    assert r["edgeKeys"] == ["y:s:1:q:to:q:from"] and r["distanceM"] == 16.0
    r, _ = idx.route_points((2.0, 10.0, 100.0), (18.0, 10.0, 100.0), "shortest")  # 구멍이 가운데를 막음
    assert "y:s:1:q:to:q:from" not in r["edgeKeys"] and r["nodeKeys"][1] in ("s", "t")


def test_point_in_open_area_prefers_area_edge_on_same_pair():
    n, e = sloped_square()
    for k in n:
        n[k]["z"] = 100.0
    area = {
        "key": "a:1",
        "location": "outdoor",
        "floorKey": None,
        "name": "광장",
        "kind": "plaza",
        "polygon": SQ,
        "z": 100.0,
        "estimated": False,
    }
    p = pkg(
        n,
        e,
        [area],
        [{"areaKey": "a:1", "nodeKey": k} for k in n],
        surfaces=[surface("s:1", [[SQ]], {"z": 100.0})],
        surface_nodes=members("s:1", n),
    )
    r, ends = MapIndex(p).route_points((10.0, 10.0, 100.0), "c", "shortest")
    assert r["edgeKeys"] == ["x:a:1:q:from:c"] and ends[0]["areas"] == ["a:1", "s:1"]
    assert r["steps"][0]["instruction"].startswith("광장을 가로질러")


def test_point_routes_do_not_change_the_shared_index():
    n, e = sloped_square()
    idx = MapIndex(pkg(n, e, surfaces=[surface("s:1", [[SQ]], SLOPE_X)], surface_nodes=members("s:1", n)))
    before = (len(idx.nodes), len(idx.edges), sum(len(v) for v in idx.out.values()))
    idx.route_points((10.0, 5.0, 101.0), (5.0, 15.0, 100.5), "fastest")
    assert (len(idx.nodes), len(idx.edges), sum(len(v) for v in idx.out.values())) == before
    assert "q:from" not in idx.nodes


# ---------- 같은 높이로 맞닿은 두 면 사이 이음 노드 (가져올 때) ----------


def _two_surfaces(left_top, right_top):
    lo, hi = [[0, 0], [10, 0], [10, 30], [0, 30]], [[10, 0], [20, 0], [20, 30], [10, 30]]
    out, links, _ = surfaces.build(
        [platform("L", lo, top_m=left_top), platform("R", hi, profile=right_top)],
        [],
        [[[-30, -30], [50, -30], [50, 50], [-30, 50]]],
        [],
        [],
        [],
    )
    return out, links


def test_seam_nodes_join_surfaces_that_meet_at_same_height():
    # 오른쪽 면은 x 로 오르막(경계 x=10 에서 100.0): 같은 높이로 맞닿음 → 경계 30 m 에 10 m 마다 이음 노드
    up = {"origin": [10, 0], "direction": [1, 0], "points": [[0, 100.0], [10, 101.0]]}
    out, links = _two_surfaces(100.0, up)
    nodes, links2, rep = surfaces.seam_nodes(out, links, [])
    seams = [n for n in nodes if n["key"].startswith("n:seam:")]
    assert sorted(round(n["y"]) for n in seams) == [5, 15, 25] and {n["x"] for n in seams} == {10.0}
    assert all(n["z"] == 100.0 and n["location"] == "outdoor" for n in seams)
    by_s = {}
    for w in links2:
        by_s.setdefault(w["surfaceKey"], set()).add(w["nodeKey"])
    assert by_s["s:L"] == by_s["s:R"] == {n["key"] for n in seams}
    assert rep["seamNodes"] == 3
    a, b = node("n:a", 2, 15, 100.0), node("n:b", 18, 15, 100.8)
    idx = MapIndex(
        pkg(
            {n["key"]: n for n in [*seams, a, b]},
            [],
            surfaces=out,
            surface_nodes=links2 + members("s:L", ["n:a"]) + members("s:R", ["n:b"]),
        )
    )
    assert idx.route("n:a", "n:b", "shortest")["nodeKeys"] == [
        "n:a",
        seams[[round(n["y"]) for n in seams].index(15)]["key"],
        "n:b",
    ]


def test_no_seam_where_surfaces_meet_with_a_step():
    step = {"origin": [10, 0], "direction": [1, 0], "points": [[0, 100.5], [10, 101.0]]}  # 경계에서 0.5 m 턱
    out, links = _two_surfaces(100.0, step)
    nodes, links2, rep = surfaces.seam_nodes(out, links, [])
    assert nodes == [] and links2 == links and rep["seamNodes"] == 0


def test_validate_counts_surface_crossings_as_connected():
    s = _src()
    s["roads"] = [
        road("r1", "a", "b", [[1, 1, 100.1], [19, 1, 101.9]]),
        road("r2", "c", "d", [[1, 19, 100.1], [5, 19, 100.5]]),
    ]
    c, _ = transform.build(s)
    p = {**c, "places": [], "floors": [], "buildings": []}
    assert release.validate(p)["components"] == [4]  # 두 길이 면 가로지르기로 한 덩어리
