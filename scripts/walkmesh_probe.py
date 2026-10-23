"""보행 그물 재기: 공개 꾸러미로 그물(면 + 길 띠)을 만들어 크기·만들기 시간·경로 시간을 재고 지금 그래프 경로와 비교한다.

uv run python scripts/walkmesh_probe.py data/releases/<꾸러미>.json [구조물 폴더]
꾸러미에 walkMeshes 가 있으면 그것을 쓰고, 없으면 여기서 만든다(구조물 폴더를 주면 칸 지붕·조명 등급도).
"""

import copy
import gzip
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mapdata import indicators, walkmesh  # noqa: E402
from mapdata.routing import MODES, MapIndex  # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main(path, structures=None):
    pkg = json.load(open(path, encoding="utf8"))
    old = copy.deepcopy(pkg)
    old.pop("walkMeshes", None)
    if not pkg.get("walkMeshes"):
        st = indicators.load_structures(structures) if structures else None
        t = time.perf_counter()
        pkg["walkMeshes"] = walkmesh.build_entries(pkg, st)
        print(f"그물 항목 만들기(가져올 때) {time.perf_counter() - t:.1f} s")
    es = pkg["walkMeshes"]
    raw = json.dumps(es, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    print(
        f"walkMeshes {len(es)}개(면 {sum(e['kind'] == 'surface' for e in es)}, 길 띠 {sum(e['kind'] == 'band' for e in es)}), "
        f"{len(raw) / 1024:.0f} KB (gzip {len(gzip.compress(raw)) / 1024:.0f} KB); "
        f"칸 크기별 {dict(sorted({c: sum(e['cell'] == c for e in es) for c in {e['cell'] for e in es}}.items()))}"
    )
    idx = MapIndex(pkg)
    import gc

    t = time.perf_counter()
    r = idx.mesh
    gc.collect()
    gc.freeze()
    print(
        f"그물 색인 {time.perf_counter() - t:.2f} s: 꼭짓점 {sum(sum(1 for a in M.adj if a) for M in r.meshes)}, "
        f"간선 {sum(len(M.ea) for M in r.meshes)}, 이음 {len(r.links)}(대표 {len(r.seam_links)}), "
        f"관문 {len(r.gref)}, 추상 간선 {sum(len(a) for a in r.aadj)}"
    )
    oidx = MapIndex(old)
    nodes = [n for n in pkg["nodes"] if n["location"] == "outdoor"] + [
        n for n in pkg["nodes"] if n["location"] != "outdoor"
    ]
    rnd = random.Random(7)
    pairs = []
    for _ in range(150):
        a, b = rnd.sample(nodes, 2)
        pairs.append((a["key"], b["key"]))
    # 면 위 아무 점(그물 세모 가운데) 쌍
    cents = []
    for mi, M in enumerate(r.meshes):
        if M.kind != "surface":
            continue
        for k in range(0, len(M.codes), 7):
            if M.codes[k] == 15:
                cents.append(M.xyz(M.nc + k))
    for _ in range(150):
        a, b = rnd.sample(cents, 2)
        pairs.append((tuple(round(c, 2) for c in a), tuple(round(c, 2) for c in b)))
    times, ratios, nores, worse = {m: [] for m in MODES}, [], 0, []
    for a, b in pairs:
        for m in MODES:
            t = time.perf_counter()
            out, _ = idx.route_points(a, b, m)
            times[m].append((time.perf_counter() - t) * 1000)
            if m == "shortest":
                cur, _ = oidx.route_points(a, b, m)
                if out is None:
                    if cur is not None:
                        nores += 1
                        print("  그물 길 없음:", a, b, m)
                elif cur:
                    ratios.append((out["distanceM"] / cur["distanceM"] if cur["distanceM"] else 1.0, a, b, cur, out))
    allt = [x for v in times.values() for x in v]
    print(
        f"\n경로 {len(pairs)}쌍 × 7방식: p50 {pct(allt, 50):.1f} ms, p95 {pct(allt, 95):.1f} ms, 최대 {max(allt):.1f} ms"
    )
    for m in MODES:
        print(f"  {m}: p50 {pct(times[m], 50):.1f}, p95 {pct(times[m], 95):.1f}, 최대 {max(times[m]):.1f} ms")
    rs = [x[0] for x in ratios]
    print(
        f"최단 거리 그물/지금: 가운데 {statistics.median(rs):.3f}, 범위 {min(rs):.3f}~{max(rs):.3f}; "
        f"지금은 길이 있는데 그물은 없음 {nores}"
    )
    for rt, a, b, cur, out in sorted(ratios, key=lambda x: x[0])[:5]:
        print(f"  짧아짐 {rt:.3f}: {a} → {b}: {cur['distanceM']} → {out['distanceM']} m")
    for rt, a, b, cur, out in sorted(ratios, key=lambda x: x[0])[-8:]:
        print(f"  길어짐 {rt:.3f}: {a} → {b}: {cur['distanceM']} → {out['distanceM']} m")
    _ = math, worse


if __name__ == "__main__":
    main(*sys.argv[1:])
