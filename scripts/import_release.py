"""campus-tracker 도로망 → 지도 릴리스(draft) → (선택) 공개.

예:
  uv run python scripts/import_release.py --api http://127.0.0.1:3210 --export C:/Users/ssant/campus-mobility-20261012c.sql --publish
  uv run python scripts/import_release.py --export C:/Users/ssant/campus-mobility-20261012c.sql
  uv run python scripts/import_release.py --publish-id 3
  지표에 구조물(계단 단·차양·기단·가로등)을 쓰려면 --structures C:/campus-tracker/frontend/public/structures
  기단 면 가로지르기(기울어진 면 포함)는 --structures 와 --walkable mapdata/data/walkable-areas-5186.geojson
  둘 다 안 주면 기본 경로(DEFAULT_STRUCTURES, DEFAULT_WALKABLE)가 있을 때 그것을 쓴다(공개할 때 걷는 면이 빠지지 않게).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import connect  # noqa: E402
from mapdata import indicators, release, sources, surfaces, transform  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STRUCTURES = Path("C:/campus-tracker-testdb/frontend/public/structures")  # 시험용 트래커 체크아웃
DEFAULT_WALKABLE = ROOT / "mapdata" / "data" / "walkable-areas-5186.geojson"  # 저장소에 있음(WALK02 뒤 판)


def with_defaults(structures, walkable, log=print):
    """주지 않은 --structures·--walkable 을 기본 경로로 채운다(있을 때만). 무엇을 썼는지 한 줄씩 남긴다."""
    out = []
    for name, given, default in (
        ("structures", structures, DEFAULT_STRUCTURES),
        ("walkable", walkable, DEFAULT_WALKABLE),
    ):
        if not given and default.exists():
            log(f"[import_release] --{name} 기본값 사용: {default}")
            given = str(default)
        elif not given:
            log(f"[import_release] --{name} 없음, 기본 경로도 없음({default}): 걷는 면 없이 가져옴")
        out.append(given)
    return tuple(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", help="campus-tracker 시험 서버 주소 (예: http://127.0.0.1:3210)")
    ap.add_argument("--export", help="pg_dump SQL 내보내기 (API 없으면 단독 원천, 있으면 빈 칸 채움)")
    ap.add_argument("--publish", action="store_true", help="검증 오류가 없으면 바로 공개")
    ap.add_argument("--publish-id", type=int, help="이미 있는 draft 공개")
    ap.add_argument(
        "--structures", help="트래커 구조물 폴더(frontend/public/structures). 지붕·바닥·조명·계단 단 지표에 씀"
    )
    ap.add_argument(
        "--walkable", help="사람이 다니는 곳 외곽 GeoJSON(5186). --structures 와 함께 주면 기단 면(walkSurfaces)을 만듦"
    )
    ap.add_argument("--report", help="보고서 JSON 저장 경로")
    a = ap.parse_args(argv)

    with connect() as conn:
        if a.publish_id:
            print(json.dumps(release.publish(conn, a.publish_id), ensure_ascii=False, indent=1))
            return 0
        if a.api:
            src = sources.load_api(a.api, a.export)
            label = f"campus-tracker test api {src['meta']['mapVersionId']}"
        elif a.export:
            src = sources.load_export(a.export)
            label = f"campus-tracker export {Path(a.export).stem}"
        else:
            ap.error("--api 또는 --export 가 필요함")
        a.structures, a.walkable = with_defaults(a.structures, a.walkable, lambda m: print(m, file=sys.stderr))
        if a.structures:
            src["structures"] = indicators.load_structures(a.structures)
            src["meta"]["structures"] = a.structures
        if a.walkable:
            src["walkable"] = surfaces.load_walkable(a.walkable)
            src["meta"]["walkable"] = a.walkable
        content, rep = transform.build(src)
        rid = release.create_draft(conn, content, rep, {"label": label, **src["meta"]})
        conn.commit()
        pkg = release.build_package(conn, rid)
        v = release.validate(pkg)
        out = {
            "releaseId": rid,
            "counts": rep["counts"],
            "fixed": {k: len(x) for k, x in rep["fixed"].items()},
            "unfixed": rep["unfixed"],
            "indicators": rep["indicators"],
            "surfaces": {k: v for k, v in (rep["surfaces"] or {}).items() if not k.startswith("skipped")},
            "validation": v,
        }
        if a.report:
            Path(a.report).write_text(
                json.dumps({**out, "fixedDetail": rep["fixed"]}, ensure_ascii=False, indent=1), encoding="utf8"
            )
        if a.publish:
            out["published"] = release.publish(conn, rid) if not v["errors"] else "검증 오류로 공개 안 함"
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 1 if v["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
