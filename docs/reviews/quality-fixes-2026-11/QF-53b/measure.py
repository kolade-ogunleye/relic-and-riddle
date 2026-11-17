"""QF-53b acceptance: python measure.py <repo root> [<outside pairs file>]. Prints JSON to stdout; writes nothing.

1. Scores the duplicate rules on the committed, labelled development pairs (real_pairs.json: hashes only). An
   optional <outside pairs file> (the same format, built from another environment's pools, e.g. the production
   sample, and kept outside the repository) is scored as well and reported as counts only: no pair names.
2. Replays every real pool (the tracked assets/*/credits.json at <repo root>) through the code at <repo root>
   on temporary copies: assess(), approve_set(), and review_pool() unforced and forced. The model is mocked to
   return each photo's recorded vision verdict (no model call), and the photos' hashes come from the committed
   real_signatures.json (the photos themselves are not in the repository; empty placeholder files stand in).
   Run it with another commit's worktree as <repo root> to measure that commit's code on the same pools.
3. Replays the fetcher's refill path (fetch_topic) on a copy of every pool with every search answering nothing, so
   only the fetcher's own selection of the pool's existing photos acts. The essential-image check and the storyboard
   are stubbed (the placeholders hold no pixels), and the fetcher's printed progress is captured.
For every path it reports how many photos each pool keeps, not only whether the pool stays ready.
"""
import contextlib
import io
import json
import logging
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

RULES = {
    "qf53_landed (either hash <= 10)": lambda p, d: p <= 10 or d <= 10,
    "qf53b_round1 (p <= 6, or both <= 10)": lambda p, d: p <= 6 or (p <= 10 and d <= 10),
    "qf53b (p + d <= 15)": lambda p, d: p + d <= 15,
}


def _name(pair: dict) -> str | None:
    """A pair's name, for development pairs only: another environment's pairs are never named."""
    if pair.get("env") != "dev":
        return None
    return f'{pair["env"]} {pair["topic"]} {pair["a"]}/{pair["b"]}'


def _names(pairs: list[dict], named: bool) -> list[str] | int:
    names = [n for n in map(_name, pairs) if n is not None] if named else []
    return names if named and len(names) == len(pairs) else len(pairs)


def score_rules(pairs: list[dict], named: bool = True) -> dict:
    out = {"pairs": len(pairs), "labels": dict(Counter(p["label"] for p in pairs))}
    for name, rule in RULES.items():
        flagged = [p for p in pairs if rule(p["phash_bits"], p["dhash_bits"])]
        out[name] = {
            "duplicates_caught": sum(p["label"] == "duplicate" for p in flagged),
            "duplicates_missed": _names([p for p in pairs if p["label"] == "duplicate" and p not in flagged], named),
            "distinct_wrongly_flagged": _names([p for p in flagged if p["label"] == "distinct"], named),
            "variants_flagged": sum(p["label"] == "variant" for p in flagged),
        }
    return out


def _outside_pairs(path: Path) -> list[dict]:
    if path.is_relative_to(ROOT) or path.is_relative_to(HERE):
        sys.exit(f"{path}: the outside pairs file must be kept outside the repository")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["pairs"] if isinstance(data, dict) else data


SIGNATURES = json.loads((HERE / "real_signatures.json").read_text(encoding="utf-8"))["signatures"]


_TOPIC_OF: dict[str, str] = {}  # copy directory -> the real pool it copies (the signature key's topic)
SIGNATURE_MISSES: list[str] = []  # any miss would make the replayed code record hash_error: reported, never hidden


def _copy_pool(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True)
    _TOPIC_OF[str(dst.resolve())] = _TOPIC_OF.get(str(src.resolve()), src.name)
    shutil.copy2(src / "credits.json", dst / "credits.json")
    for im in json.loads((src / "credits.json").read_text(encoding="utf-8")).get("images", []):
        (dst / im["file"]).write_bytes(b"")  # placeholder: load_credits keeps only records whose file exists


def _recorded_signature(path) -> dict:
    parent = str(Path(path).parent.resolve())
    key = f"{_TOPIC_OF.get(parent, Path(parent).name)}/{Path(path).name}"
    if key not in SIGNATURES:
        SIGNATURE_MISSES.append(key)
        raise FileNotFoundError(key)
    return dict(SIGNATURES[key])


def _refill(pool: Path, tmp: Path, spec: dict | None) -> dict:
    """The fetcher's refill path with every search answering nothing: what does it do to the pool's own photos?"""
    if spec is None:
        return {"refill_error": "no search spec"}
    from unittest.mock import patch

    from scripts import fetch_commons_images as fetch
    from src import image_review
    from src.credits import load_credits
    root = tmp / "refill"
    target = root / pool.name
    root.mkdir(exist_ok=True)
    _copy_pool(pool, target)
    before = len(load_credits(target).get("images", []))
    def nothing(*args, **kwargs):
        return []

    stubs = [patch.object(fetch, name, nothing) for name in ("search", "search_graded", "find_categories",
                                                              "search_openverse") if hasattr(fetch, name)]
    stubs += [patch("src.museum_sources.search_all", nothing),
              patch("src.storyboard.generate_storyboard", lambda *a, **k: None),
              patch("src.image_presentation.full_image", lambda *a, **k: True)]
    try:
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for stub in stubs:
                stack.enter_context(stub)
            fetch.fetch_topic({"id": pool.name, "title": pool.name}, spec, False, assets_dir=root)
    except Exception as exc:  # noqa: BLE001 - any failure of the replayed code is reported, never hidden
        return {"refill_error": f"{type(exc).__name__}: {exc}"[:160]}
    after = load_credits(target)
    images = after.get("images", [])
    return {"photos_after_refill": len(images), "refill_moved_to_reserve": max(0, before - len(images)),
            "ready_after_refill": image_review.video_ready(images, bool(after.get("reviewed")))}


def replay_pools(assets: Path) -> dict:
    from src import image_review
    from src.config import MIN_SCENE_IMAGES
    from src.credits import approve_set, load_credits
    try:
        from src import photo_pool
        photo_pool.signature = _recorded_signature
    except ImportError:  # code before QF-53
        photo_pool = None

    def recorded(topic, topic_dir, images, **kwargs):
        verdicts = {}
        for i, im in enumerate(images, 1):
            v = im.get("vision_review") or {}
            if "shows_topic" not in v:
                continue  # never reviewed: no verdict, so the photo is dropped (no model call)
            fields = {k: v[k] for k in ("shows_topic", "usable", "safe", "duplicate_of", "people_prominent",
                                         "can_move", "shot_class") if v.get(k) is not None}
            if fields.get("shot_class") not in ("wide", "interior", "detail", "people-scale", "archival", "unknown"):
                fields.pop("shot_class", None)
            if "quality" in image_review.PhotoVerdict.model_fields:  # QF-56 requires merit fields; a recorded
                fields["quality"] = v.get("quality") or 3            # verdict without them gets neutral ones
                fields["depiction"] = v.get("depiction") or "original"
            verdicts[i] = image_review.PhotoVerdict(index=i, reason=v.get("reason") or "r", **fields)
        return verdicts

    pools, flat = {}, sum(bool(s.get("hash_flat")) for s in SIGNATURES.values())
    specs = json.loads((ROOT / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    original = image_review._verdicts
    with tempfile.TemporaryDirectory(prefix="qf53b-") as tmp:
        tmp = Path(tmp)
        for d in sorted(p for p in assets.iterdir() if (p / "credits.json").is_file()):
            src = tmp / f"src_{d.name}"
            _copy_pool(d, src)  # placeholders, so load_credits keeps every record as on the real checkout
            cr = load_credits(src)
            imgs = cr.get("images", [])
            r = {"photos": len(imgs), "ready": image_review.video_ready(imgs, bool(cr.get("reviewed"))),
                 "reviewed_by": cr.get("reviewed_by")}
            if not imgs:
                pools[d.name] = r
                continue
            if photo_pool:
                a = tmp / f"a_{d.name}"
                _copy_pool(d, a)
                c = load_credits(a)
                photo_pool.refresh(a, c["images"])
                result = photo_pool.assess(c["images"])
                r["problems"] = result["problems"]
                r["max_author_share"] = round(result["max_author_share"], 3)
            b = tmp / f"b_{d.name}"
            _copy_pool(d, b)
            try:
                message = approve_set(b, MIN_SCENE_IMAGES)
                r["approve"] = "accepted" + (" with notice" if "Diversity notice" in message else "")
            except ValueError as exc:
                r["approve"] = "refused: " + str(exc).split(";")[0][:80]
            if cr.get("reviewed_by") != "operator":
                calls = []

                def fake(topic, topic_dir, images, _calls=calls, **kwargs):
                    _calls.append(len(images))
                    return recorded(topic, topic_dir, images)

                image_review._verdicts = fake
                try:
                    for mode, force in (("unforced", False), ("forced", True)):
                        e = tmp / f"{mode}_{d.name}"
                        _copy_pool(d, e)
                        before = len(calls)
                        s = image_review.review_pool({"id": d.name, "title": d.name}, e, force=force)
                        after = load_credits(e)
                        r[f"review_{mode}"] = s["status"]
                        r[f"ready_after_{mode}"] = image_review.video_ready(after.get("images", []),
                                                                            bool(after.get("reviewed")))
                        r[f"model_call_{mode}"] = len(calls) > before
                        r[f"photos_after_{mode}"] = len(after.get("images", []))
                        if mode == "forced":
                            r["forced_problems"] = (s.get("diversity") or {}).get("problems")
                finally:
                    image_review._verdicts = original
            r.update(_refill(d, tmp, specs.get(d.name)))
            pools[d.name] = r
    ready = [n for n, r in pools.items() if r["ready"]]

    def photos(mode: str) -> int:
        return sum(pools[n].get(f"photos_after_{mode}", pools[n]["photos"]) for n in ready)

    def kept(name: str, mode: str) -> bool:
        pool = pools[name]
        return pool.get("reviewed_by") == "operator" or bool(pool.get(f"ready_after_{mode}"))

    return {
        "pools": len(pools), "ready_stored": len(ready), "flat_photos": flat,
        "approve": dict(Counter(r.get("approve", "no photos").split(":")[0] for r in pools.values())),
        "problems": dict(Counter(p for r in pools.values() for p in r.get("problems", []))),
        "ready_operator_decided": sum(pools[n].get("reviewed_by") == "operator" for n in ready),
        "ready_after_unforced": sum(kept(n, "unforced") for n in ready),
        "ready_after_forced": sum(kept(n, "forced") for n in ready),
        "forced_drops": {n: {"max_author_share": pools[n].get("max_author_share"),
                             "problems": pools[n].get("forced_problems")}
                         for n in ready if not kept(n, "forced")},
        "unforced_model_calls": sum(bool(r.get("model_call_unforced")) for r in pools.values()),
        "photos_in_ready_pools": {"stored": sum(pools[n]["photos"] for n in ready),
                                  "after_unforced": photos("unforced"), "after_forced": photos("forced"),
                                  "after_refill": sum(pools[n].get("photos_after_refill", 0) for n in ready)},
        "ready_after_refill": sum(bool(pools[n].get("ready_after_refill")) for n in ready),
        "refill_moved_to_reserve": sum(pools[n].get("refill_moved_to_reserve", 0) for n in pools),
        "refill_errors": {n: r["refill_error"] for n, r in pools.items() if r.get("refill_error")},
        "signature_misses": len(SIGNATURE_MISSES),
        "per_pool_photos": {n: {"stored": pools[n]["photos"],
                                "forced": pools[n].get("photos_after_forced", pools[n]["photos"]),
                                "refill": pools[n].get("photos_after_refill")} for n in ready},
        "per_pool": pools,
    }


result = {"duplicate_rules_on_real_pairs": score_rules(
    json.loads((HERE / "real_pairs.json").read_text(encoding="utf-8"))["pairs"])}
if len(sys.argv) > 2:  # another environment's pairs: counts only
    result["duplicate_rules_on_outside_pairs"] = score_rules(_outside_pairs(Path(sys.argv[2]).resolve()), named=False)
assets = ROOT / "assets"
result["real_pools"] = replay_pools(assets) if any(assets.glob("*/credits.json")) else "skipped: no assets/ at this root"
print(json.dumps(result, indent=1, ensure_ascii=False))
