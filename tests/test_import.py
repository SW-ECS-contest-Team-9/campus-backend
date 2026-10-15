import hashlib
import json
from pathlib import Path

import pytest

from fixture_source import source
from mapdata import release, sources, transform


def build():
    return transform.build(source())


def test_transform_counts_and_fixes():
    c, rep = build()
    assert rep["counts"]["edges"] == 11  # 13 - 자기 고리(r12) - 같은 길 두 번(r13)
    assert rep["fixed"]["self_loop_dropped"] == ["r12"]
    assert rep["fixed"]["duplicate_pair_kind_dropped"] == ["r13"]
    assert [b["key"] for b in c["buildings"]] == ["b:가관"]  # 별동은 본 건물에 합침
    assert [f["code"] for f in c["floors"]] == ["B1", "1F", "2F"]
    nodes = {n["key"]: n for n in c["nodes"]}
    assert nodes["n:i1"]["floorKey"] == "f:가관:1F"
    assert nodes["n:i2"]["floorKey"] == "f:가관:2F"  # 높이형 층 표시 Z104.3 → 2F
    assert any("i2" in s for s in rep["fixed"]["level_height_id"])
    assert nodes["n:d"]["location"] == "outdoor" and nodes["n:d"]["kind"] == "door"
    assert nodes["n:e1"]["kind"] == "elevator_stop"


def test_place_attachment_respects_height_and_radius():
    c, rep = build()
    p = {x["key"]: x for x in c["places"]}
    assert p["p:p_ent"]["floorKey"] == "f:가관:1F"
    assert p["p:p_ent"]["nodeKey"] == "n:i1"  # 평면상 더 가까운 2층 i2 가 아님
    assert any("가관 입구" in s for s in rep["fixed"]["place_attach_height_corrected"])
    assert p["p:p_room"]["nodeKey"] == "n:i3"
    assert p["p:p_far"]["nodeKey"] is None
    assert "먼 곳" in rep["unfixed"]["place_unattached"]
    assert p["p:p_gate"]["location"] == "outdoor" and p["p:p_gate"]["nodeKey"] == "n:o1"
    assert p["p:p_ent"]["category"] == "entrance"


def test_level_height_far_from_floors_is_unknown():
    s = source()
    s["roads"][4]["coords"] = [[25, 5, 106.5], [30, 5, 106.5]]  # 2층(104)에서 2.5 m
    s["roads"][3]["coords"][-1] = [25, 5, 106.5]
    s["node_levels"] = {"i2": "가관_Z106.5_추정"}
    c, rep = transform.build(s)
    assert {n["key"]: n for n in c["nodes"]}["n:i2"]["floorKey"] is None
    assert any("i2" in x for x in rep["unfixed"]["indoor_node_floor_unknown"])


def test_draft_publish_and_checksum(clean):
    c, rep = build()
    rid = release.create_draft(clean, c, rep, {"label": "fixture"})
    clean.commit()
    pkg = release.build_package(clean, rid)
    v = release.validate(pkg)
    assert v["errors"] == []
    assert any("끊긴 덩어리" in w for w in v["warnings"])
    assert any("안 붙은 장소" in w for w in v["warnings"])
    out = release.publish(clean, rid)
    clean.commit()
    body = Path(out["path"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == out["sha256"] and len(body) == out["sizeBytes"]
    data = json.loads(body)
    assert data["schemaVersion"] == 1 and data["campus"]["crs"] == 5186
    assert {n["key"] for n in data["nodes"]} >= {"n:i1", "n:o1"}
    with pytest.raises(release.PublishError, match="draft"):
        release.publish(clean, rid)


def test_publish_blocked_by_errors(clean):
    c, rep = build()
    c["edges"].append(
        {**c["edges"][0], "key": "e:bad", "from": "n:i1", "to": "n:i3", "kind": "corridor", "name": "층이 바뀌는 복도"}
    )
    rid = release.create_draft(clean, c, rep, {"label": "fixture"})
    clean.commit()
    with pytest.raises(release.PublishError, match="층이 바뀌는"):
        release.publish(clean, rid)
    assert clean.execute("SELECT status FROM map_release WHERE id = %s", (rid,)).fetchone()["status"] == "draft"


def test_ewkb_parse():
    # LINESTRING Z (1 2 3, 4 5 6), SRID 5186
    import struct

    b = (
        b"\x01"
        + struct.pack("<I", 0x80000002 | 0x20000000)
        + struct.pack("<I", 5186)
        + struct.pack("<I", 2)
        + struct.pack("<6d", 1, 2, 3, 4, 5, 6)
    )
    assert sources.parse_ewkb(b.hex()) == ("LineString", [[1, 2, 3], [4, 5, 6]])
