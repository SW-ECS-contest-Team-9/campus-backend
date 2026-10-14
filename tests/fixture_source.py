"""작은 원천: 건물 '가관'(B1·1F·2F, 4층 없음), 바깥 길, 계단·승강기, 통행 금지 지름길, 한 방향 길, 떨어진 섬."""

import copy

LON, LAT = 127.0125, 37.6150  # 캠퍼스 근처 (발자국용)


def road(
    id,
    a,
    b,
    coords,
    structure="ordinary",
    building=None,
    level=None,
    access="allowed",
    direction="both",
    road_class="pedestrian",
    name=None,
):
    return {
        "id": id,
        "name": name,
        "road_class": road_class,
        "structure": structure,
        "pedestrian_access": access,
        "direction": direction,
        "wheelchair_access": "unknown",
        "width_m": None,
        "building_id": building,
        "level_id": level,
        "from_node": a,
        "to_node": b,
        "coords": coords,
    }


SOURCE = {
    "roads": [
        road("r1", "o1", "o2", [[0, 0, 100], [10, 0, 100]], name="앞길"),
        road("r2", "o2", "d", [[10, 0, 100], [20, 0, 100]], name="앞길"),
        road("r3", "d", "i1", [[20, 0, 100], [25, 0, 100]], "indoor_corridor", "가관", name="1층 복도"),
        road("r4", "i1", "i2", [[25, 0, 100], [25, 5, 104]], "stairs", "가관", name="중앙 계단 (8칸)"),
        road("r5", "i2", "i3", [[25, 5, 104], [30, 5, 104]], "indoor_corridor", "가관", name="2층 복도"),
        road("r6", "i1", "e1", [[25, 0, 100], [28, 0, 100]], "indoor_corridor", "가관"),
        road("r7", "e1", "e2", [[28, 0, 100], [28, 0, 104]], "elevator", "가관", name="가관 승강기"),
        road("r8", "e2", "i3", [[28, 0, 104], [30, 5, 104]], "indoor_corridor", "가관"),
        road("r9", "o1", "i3", [[0, 0, 100], [30, 5, 104]], "ramp", "가관", access="prohibited", name="금지 지름길"),
        road("r10", "o2", "o3", [[10, 0, 100], [10, 10, 100]], direction="forward", name="한 방향"),
        road("r11", "z1", "z2", [[200, 200, 100], [210, 200, 100]], name="섬"),
        road("r12", "o3", "o3", [[10, 10, 100], [10, 10, 100]], name="자기 고리"),
        road("r13", "o1", "o2", [[0, 0, 100], [10, 0, 100]], name="같은 길 두 번"),
    ],
    "node_levels": {"i2": "가관_Z104.3_추정"},
    "places": [
        {
            "id": "p_gate",
            "name": "정문",
            "category": "landmark",
            "building_id": None,
            "level_id": None,
            "coords": [0, 1, 100],
        },
        {
            "id": "p_room",
            "name": "가관 2층 휴게실",
            "category": "facility",
            "building_id": "가관",
            "level_id": None,
            "coords": [30, 6, 104],
        },
        {
            "id": "p_ent",
            "name": "가관 입구",
            "category": "building_entrance",
            "building_id": "가관",
            "level_id": None,
            "coords": [24, 4, 100],
        },
        {
            "id": "p_far",
            "name": "먼 곳",
            "category": "landmark",
            "building_id": None,
            "level_id": None,
            "coords": [500, 500, 100],
        },
        {
            "id": "p_island",
            "name": "섬 정자",
            "category": "landmark",
            "building_id": None,
            "level_id": None,
            "coords": [200, 201, 100],
        },
        {
            "id": "p_o3",
            "name": "한 방향 끝",
            "category": "other",
            "building_id": None,
            "level_id": None,
            "coords": [10, 11, 100],
        },
    ],
    "buildings": [
        {
            "id": "가관",
            "name": "가관",
            "ground_floors": 2,
            "rings_lonlat": [
                [[LON, LAT], [LON + 0.0003, LAT], [LON + 0.0003, LAT + 0.0002], [LON, LAT + 0.0002], [LON, LAT]]
            ],
            "floor_names": ["B1", "1층", "2층"],
            "floors_m": [96, 100, 104],
        },
        {
            "id": "가관#별동",
            "name": None,
            "ground_floors": None,
            "rings_lonlat": [[[LON, LAT], [LON + 0.0001, LAT], [LON, LAT + 0.0001], [LON, LAT]]],
            "floor_names": None,
            "floors_m": None,
        },
    ],
    "open_areas": [
        {
            "id": "ar1",
            "name": "앞마당",
            "kind": "plaza",
            "elevation_m": 100,
            "polygon": [[-5, -5], [15, -5], [15, 5], [-5, 5], [-5, -5]],
        },
    ],
    "meta": {"fixture": True},
}


def source():
    return copy.deepcopy(SOURCE)
