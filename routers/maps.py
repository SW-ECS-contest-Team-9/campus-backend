from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from database import get_conn
from errors import ApiError
from mapdata import store
from mapdata.routing import MODE_SPEC, MODES

router = APIRouter(prefix="/v1")


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _need_index(conn):
    got = store.latest_index(conn)
    if got is None:
        raise ApiError(503, "no_release", "공개된 지도 릴리스가 없음")
    return got


@router.get("/map/releases/latest")
def latest_release(conn=Depends(get_conn)):
    rel = store.latest(conn)
    if rel is None:
        raise ApiError(404, "no_release", "공개된 지도 릴리스가 없음")
    return {
        "id": rel["id"],
        "version": rel["version"],
        "schemaVersion": rel["schema_version"],
        "sizeBytes": rel["size_bytes"],
        "sha256": rel["sha256"].strip(),
        "url": f"/v1/map/releases/{rel['id']}/package",
        "publishedAt": _iso(rel["published_at"]),
    }


@router.get("/map/releases/{release_id}/package")
def release_package(release_id: int, request: Request, conn=Depends(get_conn)):
    rel = conn.execute("SELECT * FROM map_release WHERE id = %s", (release_id,)).fetchone()
    if rel is None or rel["status"] != "published":
        raise ApiError(404, "release_not_found", "공개된 릴리스가 아님", {"id": release_id})
    sha = rel["sha256"].strip()
    etag = f'"{sha}"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    path = Path(rel["file_path"])
    if not path.exists():
        raise ApiError(500, "package_missing", "꾸러미 파일이 없음", {"id": release_id})
    return Response(path.read_bytes(), media_type="application/json; charset=utf-8", headers=headers)


@router.get("/places")
def places(
    q: str = "",
    building: str | None = None,
    floor: str | None = None,
    limit: int = Query(30, ge=1, le=200),
    conn=Depends(get_conn),
):
    rel, idx = _need_index(conn)
    hits = idx.search(q, building, floor, limit)
    return {"releaseId": rel["id"], "version": rel["version"], "count": len(hits), "items": hits}


def _endpoint(idx, s: str, which: str) -> dict:
    if s in idx.places:
        p = idx.places[s]
        if not p["nodeKey"]:
            raise ApiError(422, "place_not_routable", f"{which}: 이 장소는 길에 붙어 있지 않음", {"place": s})
        return {"type": "place", "place": idx.place_out(p), "nodeKey": p["nodeKey"]}
    parts = s.split(",")
    if len(parts) == 3:
        try:
            x, y, z = (float(v) for v in parts)
        except ValueError:
            parts = None
        else:
            n = idx.nearest_node(x, y, z)
            if n is None:
                raise ApiError(404, "no_nearby_node", f"{which}: 25 m·높이 차 2 m 안에 길이 없음", {"point": [x, y, z]})
            return {"type": "point", "point": [x, y, z], "nodeKey": n["key"]}
    raise ApiError(400, "bad_endpoint", f"{which} 는 장소 key 또는 'x,y,z'", {which: s})


@router.get("/route-modes")
def route_modes():
    """경로 방식별 비용 규칙(꾸러미 routeModes 와 같음)."""
    return {"modes": MODE_SPEC}


@router.get("/routes")
def routes(from_: str = Query(..., alias="from"), to: str = Query(...), mode: str = "shortest", conn=Depends(get_conn)):
    if mode not in MODES:
        raise ApiError(400, "bad_mode", f"mode 는 {', '.join(MODES)}", {"mode": mode})
    rel, idx = _need_index(conn)
    a, b = _endpoint(idx, from_, "from"), _endpoint(idx, to, "to")
    r = idx.route(a["nodeKey"], b["nodeKey"], mode)
    if r is None:
        raise ApiError(
            404,
            "no_route",
            "이 조건으로 이어지는 길이 없음",
            {"from": a["nodeKey"], "to": b["nodeKey"], "mode": mode, "releaseId": rel["id"]},
        )
    return {"releaseId": rel["id"], "version": rel["version"], "from": a, "to": b, **r}
