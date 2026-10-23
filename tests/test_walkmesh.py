"""보행 그물(아3 시제품): 정사각형 칸 4등분 세모 그물 위 경로와 줄 당기기."""

import math

from test_routing import edge, node, pkg
from test_surfaces import SLOPE_X, members, surface

from mapdata import walkmesh
from mapdata.routing import MapIndex

SQ = [[0, 0], [20, 0], [20, 20], [0, 20]]


def router(surfaces, nodes=None, edges=(), links=()):
    nodes = nodes or {}
    idx = MapIndex(pkg(nodes, list(edges), surfaces=surfaces, surface_nodes=list(links)))
    entries = [walkmesh.build_plane(f"m:{s['key']}", s["polygons"], s["top"], sources=[s["key"]]) for s in surfaces]
    return walkmesh.MeshRouter(idx, entries)


def straight(geom) -> bool:
    """모든 점이 처음–끝 직선 위(5 cm 안)."""
    (x0, y0), (x1, y1) = geom[0][:2], geom[-1][:2]
    n = math.hypot(x1 - x0, y1 - y0)
    return all(abs((x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)) / n < 0.05 for p in geom)


def test_bits_round_trip():
    codes = [0, 15, 1, 2, 4, 8, 15, 3, 12]
    assert walkmesh.decode_bits(walkmesh.encode_bits(codes), len(codes)) == codes


def test_square_plaza_full_cells_and_rebuild_is_same():
    e = walkmesh.build_plane("m:1", [[SQ]], {"z": 100.0})
    assert e["cell"] == 2.0 and (e["cols"], e["rows"]) == (10, 10)
    assert walkmesh.decode_bits(e["tris"], 100) == [15] * 100
    p = walkmesh.Plane(e, [[SQ]])
    assert len(p.adj) == 11 * 11 + 10 * 10  # 모서리 + 가운데
    assert {round(d, 3) for v in p.adj for _, d in p.adj[v]} == {2.0, round(math.sqrt(2), 3)}


def test_square_plaza_diagonal_is_one_straight_line():
    r = router([surface("s:1", [[SQ]], {"z": 100.0})])
    out = r.route_points((1.0, 1.0, 100.0), (19.0, 15.0, 100.0))
    assert out is not None
    assert straight(out["geometry"])
    assert math.isclose(out["distanceM"], math.hypot(18, 14), abs_tol=0.01)


def test_obstacle_in_middle_goes_around():
    hole = [[8, 6], [12, 6], [12, 14], [8, 14]]
    r = router([surface("s:1", [[SQ, hole]], {"z": 100.0})])
    out = r.route_points((2.0, 10.0, 100.0), (18.0, 10.0, 100.0))
    assert out is not None
    # 구멍 모서리를 스치는 가장 짧은 길: (2,10)→(8,6)→(12,6)→(18,10) 또는 위쪽 대칭
    best = 2 * math.hypot(6, 4) + 4
    assert best - 0.01 <= out["distanceM"] <= best + 0.5
    from shapely.geometry import LineString, Polygon

    assert LineString([p[:2] for p in out["geometry"]]).intersection(Polygon(hole).buffer(-0.01)).length == 0


def test_two_planes_with_step_do_not_join_without_stairs():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 101.0})
    r = router([a, b])
    assert r.route_points((2.0, 5.0, 100.0), (18.0, 5.0, 101.0)) is None


def test_two_planes_with_step_join_through_stairs():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 101.0})
    n = {"lo": node("lo", 9.5, 5, 100.0), "hi": node("hi", 10.5, 5, 101.0)}
    st = edge("st", "lo", "hi", n, kind="stairs")
    r = router([a, b], n, [st], members("s:a", ["lo"]) + members("s:b", ["hi"]))
    out = r.route_points((2.0, 5.0, 100.0), (18.0, 5.0, 101.0))
    assert out is not None and out["edgeKeys"] == ["st"]
    assert math.isclose(out["distanceM"], 7.5 + st["lengthM"] + 7.5, abs_tol=0.05)


def test_same_height_planes_side_by_side_join():
    a = surface("s:a", [[[[0, 0], [10, 0], [10, 10], [0, 10]]]], {"z": 100.0})
    b = surface("s:b", [[[[10, 0], [20, 0], [20, 10], [10, 10]]]], {"z": 100.1})
    out = router([a, b]).route_points((2.0, 5.0, 100.0), (18.0, 5.0, 100.1))
    assert out is not None and out["distanceM"] < 16.5


def test_sloped_plane_length_is_3d():
    r = router([surface("s:1", [[SQ]], SLOPE_X)])
    out = r.route_points((0.0, 10.0, 100.0), (20.0, 10.0, 102.0))
    assert out is not None and straight(out["geometry"])
    assert math.isclose(out["distanceM"], 20 * math.sqrt(1.01), abs_tol=0.01)
    assert out["geometry"][len(out["geometry"]) // 2][2] == 101.0


def test_narrow_strip_uses_small_cells():
    e = walkmesh.build_plane("m:1", [[[[0, 0], [30, 0], [30, 1.5], [0, 1.5]]]], {"z": 100.0})
    assert e["cell"] == 1.0 and e["cover"] >= 0.6


def test_package_node_attaches_and_graph_edges_still_work():
    # 면 밖 길(노드 o→a) 끝 a 가 면 위: 면 밖 점에서 면 위 점까지 길 + 그물
    n = {"o": node("o", -10, 10), "a": node("a", 0, 10)}
    r = router([surface("s:1", [[SQ]], {"z": 100.0})], n, [edge("oa", "o", "a", n)], members("s:1", ["a"]))
    out = r.route_points("o", (19.0, 19.0, 100.0))
    assert out is not None and out["edgeKeys"] == ["oa"]
    assert math.isclose(out["distanceM"], 10 + math.hypot(19, 9), abs_tol=0.01)
