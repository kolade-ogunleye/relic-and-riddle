"""Compare two render_surfaces.py results (the base tree and this row's tree, the default brand).

    python compare.py <base.json> <head.json> [<second-brand.json>]

Prints JSON: surfaces in both, identical pixel hashes, the differing ones, surfaces or errors only on one side, the
summed render seconds of each tree, and, with a second-brand result, how many surfaces drew differently there.
"""
import json
import sys
from pathlib import Path


def main(base_path: str, head_path: str, second_path: str | None = None) -> None:
    base, head = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (base_path, head_path))
    b, h = base["surfaces"], head["surfaces"]
    both = sorted(set(b) & set(h))
    out = {
        "src_sha256_base_head": [base.get("src_sha256"), head.get("src_sha256")],
        "surfaces_compared": len(both),
        "frames_compared": sum(h[k].get("frames", 0) for k in both),
        "identical": sum(b[k] == h[k] for k in both),
        "differing": [k for k in both if b[k] != h[k]],
        "only_base": sorted(set(b) - set(h)), "only_head": sorted(set(h) - set(b)),
        "errors_base": sorted(base["errors"]), "errors_head": sorted(head["errors"]),
        "seconds_base": round(sum(base["seconds"].values()), 1),
        "seconds_head": round(sum(head["seconds"].values()), 1),
    }
    if second_path:
        second = json.loads(Path(second_path).read_text(encoding="utf-8"))
        s = second["surfaces"]
        drawn = [k for k in both if k in s and s[k].get("sha256")]
        out["second_brand"] = {"surfaces": len(s), "drawn": len(drawn),
                               "differs_from_default": sum(s[k] != h[k] for k in drawn),
                               "same_as_default": [k for k in drawn if s[k] == h[k]],
                               "refused": {k: v["refused"] for k, v in s.items() if "refused" in v},
                               "errors": {k: v.strip().splitlines()[-1] for k, v in second["errors"].items()}}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit("usage: compare.py <base.json> <head.json> [<second-brand.json>]")
    main(*sys.argv[1:])
