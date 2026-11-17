"""QF-81 real-data counts (outside the repo): python real_data.py <dev repo root> <prod sample copy> [<worktree>].
Prints counts only. The dev DB is copied to a temp file and opened read-only with sqlite3 (never PipelineDatabase)."""
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path


def slides(run_dir: Path) -> bool:
    return any((run_dir / "carousel").glob("carousel_slide_*.jpg"))


def dev(root: Path) -> dict:
    out = {"run_folders": 0, "run_folders_with_carousel": 0, "with_facebook_copy": 0}
    for d in (root / "output").glob("run_*"):
        out["run_folders"] += 1
        out["run_folders_with_carousel"] += slides(d)
        out["with_facebook_copy"] += any((d / "carousel_fb").glob("carousel_slide_*.jpg"))
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "db.sqlite"
        shutil.copyfile(root / "data" / "pipeline.db", copy)
        conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro", uri=True)
        rows = conn.execute("SELECT status, COUNT(*) FROM scheduled_posts WHERE kind='carousel' GROUP BY status").fetchall()
        out["carousel_bookings_by_status"] = dict(rows)
        pubs = conn.execute("SELECT status, COUNT(*) FROM publications WHERE channel='upload-post:carousel' "
                            "GROUP BY status").fetchall()
        out["carousel_publications_by_status"] = dict(pubs)
        try:
            labels = conn.execute("SELECT platform, source, COUNT(*) FROM audience_metrics WHERE platform LIKE '%carousel' "
                                  "GROUP BY platform, source").fetchall()
        except sqlite3.OperationalError:
            labels = []
        out["carousel_metric_rows"] = {f"{p}/{s}": n for p, s, n in labels}
        conn.close()
    return out


def prod(sample: Path) -> dict:
    data = json.loads((sample / "metrics.json").read_text(encoding="utf-8"))
    runs = list((sample / "output").glob("run_*"))
    drawn = [d.name for d in runs if slides(d)]
    pubs = [p for p in data["publications"] if p["channel"] == "upload-post:carousel"]
    posted = {p["run_id"] for p in pubs if p["status"] == "published"}
    car_metrics = Counter((m["platform"], m["source"]) for m in data["audience_metrics"] if m["platform"].endswith("_carousel"))
    reels_measured = len({m["run_id"] for m in data["audience_metrics"] if m["platform"] == "instagram"})
    return {"run_folders": len(runs), "run_folders_with_carousel": len(drawn),
            "with_facebook_copy": sum(any((d / "carousel_fb").glob("carousel_slide_*.jpg")) for d in runs),
            "carousel_publications_by_status": dict(Counter(p["status"] for p in pubs)),
            "drawn_and_posted": len(set(drawn) & posted), "drawn_never_posted": len(set(drawn) - posted),
            "carousel_metric_rows_by_label_source": {f"{p}/{s}": n for (p, s), n in car_metrics.items()},
            "carousel_metric_rows_with_reach_and_saves": sum(
                1 for m in data["audience_metrics"] if m["platform"].endswith("_carousel")
                and isinstance(m.get("metrics"), dict) and m["metrics"].get("reach") is not None
                and (m["metrics"].get("saved") is not None or m["metrics"].get("saves") is not None)),
            "instagram_reels_measured": reels_measured}


def digest_kinds(sample: Path, tree: Path) -> dict:
    """The digest's carousel lines (audience.carousel_comparison of <tree>) on the sample's metric rows, loaded into
    a fresh temporary database: which kinds of line print, never their text or numbers."""
    sys.path.insert(0, str(tree))
    from src import audience
    from src.db import PipelineDatabase
    data = json.loads((sample / "metrics.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        bank = Path(tmp) / "bank.json"
        bank.write_text(json.dumps({"topics": []}), encoding="utf-8")
        db = PipelineDatabase(db_path=Path(tmp) / "digest.db", topic_bank_file=bank)
        audience.ensure_schema(db)
        with db._conn() as conn:
            for m in data["audience_metrics"]:
                conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                             (m["run_id"], m["platform"], m["snapshot"], m["source"], json.dumps(m["metrics"]),
                              m["fetched_at"]))
        lines = audience.carousel_comparison(db)
    kinds = {"carousels_counted_until_12": "carousels measured; compared from", "carousel_medians": "medians): carousels",
             "facebook_reach_ratio": "Facebook Reel reach is", "facebook_reach_not_measured": "Facebook Reel reach not measured",
             "facebook_views_ratio": "Instead, views (not reach)"}
    seven = [m for m in data["audience_metrics"] if m["platform"] == "facebook" and m["snapshot"] == "7d"]
    return {"line_kinds": {k: sum(v in line for line in lines) for k, v in kinds.items()},
            "facebook_reel_rows_7d": len(seven),
            "facebook_reel_rows_7d_with_reach": sum(1 for m in seven if (m.get("metrics") or {}).get("reach") is not None)}


if __name__ == "__main__":
    out = {"dev": dev(Path(sys.argv[1])), "prod": prod(Path(sys.argv[2]))}
    if len(sys.argv) > 3:
        out["prod_digest"] = digest_kinds(Path(sys.argv[2]), Path(sys.argv[3]))
    print(json.dumps(out, indent=1))
