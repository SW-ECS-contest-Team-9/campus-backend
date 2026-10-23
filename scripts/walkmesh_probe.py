"""보행 그물 시제품 재기: 공개 꾸러미로 그물을 만들어 크기·경로 시간을 재고 지금 그래프 경로와 비교한다.

uv run python scripts/walkmesh_probe.py data/releases/<꾸러미>.json
"""

import gzip
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapdata import walkmesh  # noqa: E402
from mapdata.routing import MapIndex  # noqa: E402

PROTO = {
    "운동장": ["s:Z02-PLATFORM-APRON", "a:1"],
    "회차 공간": ["s:Z05-PLATFORM-TURN-PLAZA", "a:2"],
    "분수 광장": ["s:Z03-PLATFORM-PLAZA", "a:3"],
}


def build(pkg, keys=None):
    t = time.perf_counter()
    entries = [
        walkmesh.build_plane(p["key"], p["polygons"], p["top"], sources=p["sources"])
        for p in walkmesh.planes_from_package(pkg, keys)
    ]
    return entries, time.perf_counter() - t


def sizes(entries):
    raw = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    return len(raw), len(gzip.compress(raw))


def far_pair(r: walkmesh.MeshRouter, pi: int, idx: MapIndex):
    """면을 비스듬히 가로지르는 쌍: 축 좌표 (a+b) 가 작은 쪽·큰 쪽 가운데 꼭짓점 가운데 지금 색인도 면에 붙이는 것."""
    P = r.planes[pi]
    cs = sorted((v for v in P.adj if v >= P._nc), key=lambda v: sum(P.local(*P.vertex(v)[:2])))

    def ok(v):
        return idx.route_points(P.vertex(v), P.vertex(v), "shortest")[1][0]["attached"] == "surface"

    lo = next(v for v in cs if ok(v))
    hi = next(v for v in reversed(cs) if ok(v))
    return P.vertex(lo), P.vertex(hi)


def main(path):
    pkg = json.load(open(path, encoding="utf8"))
    print(f"꾸러미 {path}: {len(json.dumps(pkg, ensure_ascii=False).encode('utf8')) / 1024:.0f} KB")
    t = time.perf_counter()
    idx = MapIndex(pkg)
    print(f"지금 색인 만들기 {time.perf_counter() - t:.2f} s")

    print("\n== 시제품 구역 ==")
    for name, keys in PROTO.items():
        entries, bt = build(pkg, keys)
        r = walkmesh.MeshRouter(idx, entries)
        for pi, (e, P) in enumerate(zip(entries, r.planes)):
            raw, gz = sizes([e])
            print(
                f"{name} {e['key']} ({'+'.join(e['sources'])}): 칸 {e['cell']} m, {e['cols']}×{e['rows']}, "
                f"덮음 {e['cover']:.0%}, 꼭짓점 {len(P.adj)}, 간선 {sum(map(len, P.adj.values())) // 2}, "
                f"항목 {raw} B (gzip {gz} B), 만들기 {bt * 1000:.0f} ms"
            )
            a, b = far_pair(r, pi, idx)
            for mode in ("shortest", "nightSafe"):
                cur, _ = idx.route_points(a, b, mode)
                t = time.perf_counter()
                new = r.route_points(a, b, mode)
                dt = (time.perf_counter() - t) * 1000
                straight = math.dist(a, b)
                print(
                    f"   {mode}: 직선 {straight:.1f} m | 지금 {cur['distanceM'] if cur else None} m "
                    f"(선 점 {len(cur['geometry']) if cur else 0}) | 그물 {new['distanceM'] if new else None} m "
                    f"(선 점 {len(new['geometry']) if new else 0}, 꼭짓점 {new['meshVertices'] if new else 0}, "
                    f"방문 {new['visited'] if new else 0}, {dt:.0f} ms)"
                )

    print("\n== 캠퍼스 전체(걷는 면 84 + 열린 면 5) ==")
    entries, bt = build(pkg)
    t = time.perf_counter()
    r = walkmesh.MeshRouter(idx, entries)
    rt = time.perf_counter() - t
    raw, gz = sizes(entries)
    nv = sum(len(P.adj) for P in r.planes)
    ne = sum(sum(map(len, P.adj.values())) // 2 for P in r.planes)
    cells = sum(e["cols"] * e["rows"] for e in entries)
    tris = sum(bin(c).count("1") for P in r.planes for c in P.codes)
    by_cell = {c: sum(e["cell"] == c for e in entries) for c in sorted({e["cell"] for e in entries})}
    print(
        f"면 {len(entries)} (칸 크기별 {by_cell}), 격자 칸 {cells}, 세모 {tris}, 꼭짓점 {nv}, 간선 {ne}, "
        f"잇는 간선 {sum(map(len, r.links.values())) // 2}, 노드 붙임 {sum(len(v) for k, v in r.attach.items() if isinstance(k, str))}"
    )
    print(f"walkMeshes {raw / 1024:.1f} KB (gzip {gz / 1024:.1f} KB), 만들기 {bt:.1f} s, 그물 색인 {rt:.1f} s")
    low = sorted(entries, key=lambda e: e["cover"])[:5]
    print("덮음 낮은 면:", ", ".join(f"{e['key']} {e['cover']:.0%}" for e in low))

    outdoor = [n for n in pkg["nodes"] if n["location"] == "outdoor" and r.attach.get(n["key"])]
    rng = random.Random(7)
    rows = []
    for _ in range(30):
        a, b = rng.sample(outdoor, 2)
        t = time.perf_counter()
        cur = idx.route(a["key"], b["key"], "shortest")
        ct = (time.perf_counter() - t) * 1000
        t = time.perf_counter()
        new = r.route_points(a["key"], b["key"], "shortest")
        nt = (time.perf_counter() - t) * 1000
        rows.append(
            (cur and cur["distanceM"], new and new["distanceM"], ct, nt, new and new["visited"], a["key"], b["key"])
        )
    both = [x for x in rows if x[0] and x[1]]
    ratio = sorted(x[1] / x[0] for x in both)
    print(
        f"면 위 노드 무작위 30쌍(최단): 둘 다 경로 {len(both)}, 지금만 {sum(1 for x in rows if x[0] and not x[1])}, "
        f"그물만 {sum(1 for x in rows if x[1] and not x[0])}"
    )
    if ratio:
        print(
            f"  그물/지금 거리 비: 최소 {ratio[0]:.3f}, 가운데 {ratio[len(ratio) // 2]:.3f}, 최대 {ratio[-1]:.3f}; "
            f"시간 지금 가운데 {sorted(x[2] for x in rows)[15]:.0f} ms, 그물 가운데 {sorted(x[3] for x in rows)[15]:.0f} ms "
            f"최대 {max(x[3] for x in rows):.0f} ms, 방문 최대 {max((x[4] or 0) for x in rows)}"
        )
    for x in sorted(both, key=lambda x: x[1] / x[0])[:3]:
        print(f"  차이 큰 쌍 {x[5]} → {x[6]}: 지금 {x[0]} m, 그물 {x[1]} m")


if __name__ == "__main__":
    main(sys.argv[1])
