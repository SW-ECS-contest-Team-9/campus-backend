"""경로 지표 계산 단위 시험: 꾸러미 내용(content) dict 와 작은 구조물 피처로. DB 없이."""

import pytest

from mapdata import indicators as ind


def node(key, x, y, z=100.0, location="outdoor", building=None, floor=None, kind="junction"):
    return {
        "key": key,
        "location": location,
        "buildingKey": building,
        "floorKey": floor,
        "kind": kind,
        "x": x,
        "y": y,
        "z": z,
        "estimated": False,
    }


def edge(key, a, b, geometry, kind="walkway", name=None, building=None, width=None, access="allowed", wc="unknown"):
    return {
        "key": key,
        "from": a,
        "to": b,
        "name": name,
        "kind": kind,
        "direction": "both",
        "pedestrianAccess": access,
        "wheelchairAccess": wc,
        "lengthM": round(ind.poly_len(geometry), 2),
        "stepCount": None,
        "widthM": width,
        "geometry": geometry,
        "estimated": False,
        "_building": building,
    }


def content(nodes, edges, places=(), floors=(), buildings=()):
    return {
        "buildings": list(buildings),
        "floors": list(floors),
        "nodes": nodes,
        "edges": edges,
        "places": list(places),
        "walkAreas": [],
        "walkAreaNodes": [],
    }


def box(id, ring, type, fromM, toM, **props):
    return {
        "type": "Feature",
        "properties": {"id": id, "kind": "extrude", "type": type, "fromM": fromM, "toM": toM, **props},
        "geometry": {"type": "Polygon", "coordinates": [ring + ring[:1]]},
    }


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ---------- 기하 ----------


def test_geometry_slope_climb_and_lengths():
    g = ind.geometry_indicators([[0, 0, 100], [10, 0, 101], [20, 0, 100]], "walkway")
    assert g["horizontalM"] == 20.0
    assert g["climbM"] == 1.0 and g["descentM"] == 1.0
    assert g["maxSlopePct"] == 10.0
    assert g["avgSlopePct"] == 0.0


def test_slope_window_ignores_tiny_vertex_noise():
    # 0.2 m 사이에 0.1 m 튐(50 %)은 2 m 창으로 묶여 가파른 길로 보지 않는다.
    g = ind.geometry_indicators([[0, 0, 100], [5, 0, 100], [5.2, 0, 100.1], [10, 0, 100.1]], "walkway")
    assert g["maxSlopePct"] < 5


def test_walk_time_flat_uphill_downhill():
    flat = ind.geometry_indicators([[0, 0, 100], [12, 0, 100]], "walkway")
    assert flat["walkS"] == pytest.approx(10.0, abs=0.1) and flat["walkSBack"] == flat["walkS"]
    up = ind.geometry_indicators([[0, 0, 100], [12, 0, 101.2]], "walkway")  # 10 % 오르막
    assert up["walkS"] > flat["walkS"] * 1.2
    assert up["walkSBack"] < up["walkS"]  # 거꾸로는 내리막


def test_elevator_has_no_slope_and_ride_time():
    g = ind.geometry_indicators([[0, 0, 100], [0, 0, 108]], "elevator")
    assert g["horizontalM"] == 0.0 and g["maxSlopePct"] is None
    assert g["walkS"] == pytest.approx(8 / ind.ELEVATOR_MPS + ind.ELEVATOR_STOP_S)


# ---------- 계단 ----------


def stairs_case(name=None, features=()):
    n = {"a": node("a", 0, 0, 100), "b": node("b", 4, 0, 102)}
    e = edge("s", "a", "b", [[0, 0, 100], [4, 0, 102]], kind="stairs", name=name)
    c = content(list(n.values()), [e])
    ind.annotate(c, ind.parse_structures(list(features)))
    return c["edges"][0]["indicators"]


def test_steps_from_name_exact_and_estimated():
    i = stairs_case("정문 계단 (12칸)")
    assert i["steps"] == 12 and i["src"]["steps"] == "name"
    assert i["stepRiseM"] == pytest.approx(2 / 12, abs=0.01)
    i = stairs_case("앞 계단 (13단 추정)")
    assert i["steps"] == 13 and i["src"]["steps"] == "name_estimated"
    i = stairs_case("바깥 12칸 + 안쪽 5칸 = 17칸")
    assert i["steps"] == 17


def test_steps_from_structures_then_height_estimate():
    steps = [
        box(f"S{k}", rect(0.3 * k, -1, 0.3 * k + 0.3, 1), "stair_step", 99.8, 100 + 0.16 * k, stair="ST1", widthM=2.0)
        for k in range(1, 13)
    ]
    i = stairs_case(None, steps)
    assert i["steps"] == 12 and i["src"]["steps"] == "structure"
    assert i["flights"] == 1 and i["minWidthM"] == 2.0 and i["src"]["minWidthM"] == "structure"
    i = stairs_case(None)
    assert i["steps"] == round(2 / ind.STEP_RISE_EST_M) and i["src"]["steps"] == "estimated"
    assert i["flights"] is None


def test_stairs_time_uses_steps():
    i = stairs_case("계단 (12칸)")
    assert i["walkS"] == pytest.approx(12 * ind.STEP_UP_S + max(0, 4 - 12 * ind.STEP_RUN_M) / ind.WALK_MPS, abs=0.1)
    assert i["walkSBack"] == pytest.approx(
        12 * ind.STEP_DOWN_S + max(0, 4 - 12 * ind.STEP_RUN_M) / ind.WALK_MPS, abs=0.1
    )


# ---------- 지붕·조명·차량·바닥 ----------


def line_case(features=(), edges_extra=(), places=(), building=None, kind="walkway", length=20.0):
    n = {"a": node("a", 0, 0), "b": node("b", length, 0)}
    e = [edge("w", "a", "b", [[0, 0, 100], [length, 0, 100]], kind=kind, building=building)]
    for k, (x0, y0, x1, y1, kd) in enumerate(edges_extra):
        n[f"p{k}"], n[f"q{k}"] = node(f"p{k}", x0, y0), node(f"q{k}", x1, y1)
        e.append(edge(f"x{k}", f"p{k}", f"q{k}", [[x0, y0, 100], [x1, y1, 100]], kind=kd))
    c = content(list(n.values()), e, places)
    cov = ind.annotate(c, ind.parse_structures(list(features)))
    return {x["key"]: x["indicators"] for x in c["edges"]}, {x["key"]: x.get("indicators") for x in c["nodes"]}, cov


def test_canopy_covers_half():
    e, _, _ = line_case([box("C1", rect(0, -2, 10, 2), "canopy", 103, 103.3)])
    assert e["w"]["coveredRatio"] == pytest.approx(0.5, abs=0.06)
    assert e["w"]["cover"] == "mixed" and e["w"]["src"]["cover"] == "structure"


def test_canopy_too_low_or_too_high_does_not_cover():
    e, _, _ = line_case(
        [box("C1", rect(0, -2, 20, 2), "canopy", 100.5, 101), box("C2", rect(0, -2, 20, 2), "deck", 130, 131)]
    )
    assert e["w"]["coveredRatio"] == 0.0 and e["w"]["cover"] == "none"


def test_indoor_and_underground_cover():
    e, _, _ = line_case(building="b:가관")
    assert e["w"]["indoor"] is True and e["w"]["coveredRatio"] == 1.0 and e["w"]["cover"] == "indoor"
    void = {
        "type": "Feature",
        "properties": {"id": "V", "kind": "void", "type": "void", "fromM": 99, "toM": 103},
        "geometry": {"type": "Polygon", "coordinates": [rect(-1, -2, 21, 2) + [[-1, -2]]]},
    }
    e, _, _ = line_case([void])
    assert e["w"]["cover"] == "underground" and e["w"]["coveredRatio"] == 1.0


def test_lamps_light_nearby_part():
    lamp = box("X22-LAMP-T-01", rect(-0.1, 2.9, 0.1, 3.1), "wall", 100, 106)
    head = box("X22-LAMP-T-01-HEAD1", rect(-0.1, 2.9, 0.6, 3.1), "wall", 105.8, 106)
    e, n, _ = line_case([lamp, head], length=40.0)
    assert e["w"]["lampCount"] == 1  # 머리는 따로 세지 않는다
    assert e["w"]["litRatio"] == pytest.approx(15 / 40, abs=0.05)
    assert n["a"]["lit"] is True and n["b"].get("lit") is None
    e, _, _ = line_case([])
    assert e["w"]["litRatio"] is None and e["w"]["src"]["lit"] == "unknown"  # 가로등 자료 없음 = 어둡다가 아니라 모름


def test_vehicle_exposure():
    e, _, _ = line_case(
        edges_extra=[(0, 2, 20, 2, "vehicle_road"), (0, 12, 20, 12, "walkway"), (5, -5, 5, 5, "crossing")]
    )
    assert e["w"]["vehicle"] == "adjacent"
    assert e["x0"]["vehicle"] == "shared"
    assert e["x1"]["vehicle"] == "none"
    assert e["x2"]["vehicle"] == "crossing" and e["x2"]["crossing"] == "marked"


def test_surface_from_platform():
    plat = {
        "type": "Feature",
        "properties": {"id": "P", "kind": "platform", "type": "platform", "topM": 100.0, "material": "paving"},
        "geometry": {"type": "Polygon", "coordinates": [rect(-1, -2, 30, 2) + [[-1, -2]]]},
    }
    e, _, _ = line_case([plat])
    assert e["w"]["surface"] == "paving" and e["w"]["src"]["surface"] == "structure"
    e, _, _ = line_case([])
    assert e["w"]["surface"] is None
    e, _, _ = line_case(building="b:가관")
    assert e["w"]["surface"] == "indoor_floor"


def test_platform_profile_height():
    p = ind.parse_structures(
        [
            {
                "type": "Feature",
                "properties": {
                    "id": "P",
                    "kind": "platform",
                    "type": "platform",
                    "material": "asphalt",
                    "topProfile": {"origin": [0, 0], "direction": [1, 0], "points": [[0, 100], [10, 110]]},
                },
                "geometry": {"type": "Polygon", "coordinates": [rect(-5, -5, 20, 5) + [[-5, -5]]]},
            }
        ]
    )["platforms"][0]
    assert ind.platform_top(p, 5, 0) == 105 and ind.platform_top(p, -3, 0) == 100 and ind.platform_top(p, 15, 0) == 110


# ---------- 휠체어 ----------


@pytest.mark.parametrize(
    "z1,kind,width,expect",
    [
        (100.0, "walkway", None, "likely"),
        (100.7, "walkway", None, "steep"),  # 7 %
        (101.0, "walkway", None, "no"),  # 10 %
        (100.0, "walkway", 0.8, "no"),
        (102.0, "stairs", None, "no"),
    ],
)
def test_wheelchair_derived(z1, kind, width, expect):
    n = [node("a", 0, 0, 100), node("b", 10, 0, z1)]
    c = content(n, [edge("w", "a", "b", [[0, 0, 100], [10, 0, z1]], kind=kind, width=width)])
    ind.annotate(c, None)
    i = c["edges"][0]["indicators"]
    assert i["wheelchair"] == expect
    assert i["stepFree"] is (kind != "stairs")


def test_wheelchair_prohibited_in_source_wins():
    n = [node("a", 0, 0), node("b", 10, 0)]
    c = content(n, [edge("w", "a", "b", [[0, 0, 100], [10, 0, 100]], wc="prohibited")])
    ind.annotate(c, None)
    assert c["edges"][0]["indicators"]["wheelchair"] == "no"
    assert c["edges"][0]["indicators"]["src"]["wheelchair"] == "surveyed"


# ---------- 승강기·노드 ----------


def test_elevator_shaft_floors_served():
    floors = [
        {"key": f"f:가관:{c}", "buildingKey": "b:가관", "code": c, "sortOrder": s, "floorZ": z, "zEstimated": True}
        for c, s, z in (("B1", -1, 96), ("1F", 1, 100), ("2F", 2, 104))
    ]
    n = [
        node(k, 0, 0, z, "indoor", "b:가관", f"f:가관:{c}", "elevator_stop")
        for k, z, c in (("e0", 96, "B1"), ("e1", 100, "1F"), ("e2", 104, "2F"))
    ]
    e = [
        edge("l1", "e0", "e1", [[0, 0, 96], [0, 0, 100]], kind="elevator", building="b:가관"),
        edge("l2", "e1", "e2", [[0, 0, 100], [0, 0, 104]], kind="elevator", building="b:가관"),
    ]
    c = content(n, e, floors=floors)
    ind.annotate(c, None)
    for x in c["edges"] + c["nodes"]:
        assert x["indicators"]["elevator"]["floors"] == ["B1", "1F", "2F"]
        assert x["indicators"]["elevator"]["shaft"] == c["edges"][0]["indicators"]["elevator"]["shaft"]
    assert c["edges"][0]["indicators"]["elevator"]["waitS"] == ind.ELEVATOR_WAIT_S


def test_node_landmark_and_door():
    places = [
        {"key": "p:1", "name": "분수", "category": "landmark", "x": 3, "y": 4, "z": 100, "nodeKey": None},
        {"key": "p:2", "name": "먼 곳", "category": "landmark", "x": 300, "y": 0, "z": 100, "nodeKey": None},
    ]
    _, n, _ = line_case(places=places, length=40.0)
    assert n["a"]["landmark"] == {"key": "p:1", "name": "분수", "distM": 5.0}
    assert n["b"].get("landmark") is None


# ---------- 덮개율 보고 ----------


def test_coverage_report():
    e, _, cov = line_case([box("C1", rect(0, -2, 10, 2), "canopy", 103, 103.3)])
    assert cov["edges"] == 1
    assert cov["fields"]["cover"]["known"] == 1 and cov["fields"]["cover"]["pct"] == 100.0
    assert cov["fields"]["litRatio"]["known"] == 0
    assert cov["fields"]["thresholdCm"]["known"] == 0  # 현장 수집 필요 항목도 0 % 로 보고
