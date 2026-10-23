"""공개된 최신 릴리스를 메모리에 올려 둔다. 요청마다 DB 에서 최신 id 만 확인하고 바뀌면 다시 읽는다."""

import gc
import json
import threading
from pathlib import Path

from mapdata.routing import MapIndex

LATEST_SQL = (
    "SELECT r.* FROM map_release r JOIN campus c ON c.id = r.campus_id"
    " WHERE c.code = %s AND r.status = 'published' ORDER BY r.version DESC LIMIT 1"
)

_lock = threading.Lock()
_cache: dict = {"id": None, "index": None}


def latest(conn, campus: str = "skuniv") -> dict | None:
    return conn.execute(LATEST_SQL, (campus,)).fetchone()


def latest_index(conn) -> tuple[dict, MapIndex] | None:
    rel = latest(conn)
    if rel is None:
        return None
    with _lock:
        if _cache["id"] != rel["id"]:
            pkg = json.loads(Path(rel["file_path"]).read_bytes())
            index = MapIndex(pkg)
            _ = index.mesh  # 보행 그물 관문 표를 미리(첫 경로 요청이 느리지 않게)
            gc.collect()
            gc.freeze()  # 큰 색인을 GC 가 매번 훑지 않게(경로 시간 튐 줄이기)
            _cache.update(id=rel["id"], index=index)
        return rel, _cache["index"]


def clear() -> None:
    with _lock:
        _cache.update(id=None, index=None)
