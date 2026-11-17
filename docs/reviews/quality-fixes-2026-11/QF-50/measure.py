"""QF-50 acceptance metric: with both cloud vision models configured and FACE_VERIFY_CLOUD left at its
default, how many requests does detect_human_faces send off the machine? Does docs/privacy.md exist with
the four data-map tables (purpose, retention, processors, deletion) covering audit_log, audience_*,
cache/privacy and assets/**? Do the audience_* tables have a prune rule? Operator decision 2026-11-05 (option C):
with FACE_VERIFY_CLOUD_AMBIGUOUS off (the default) no crop is sent; on, only the face crop the local check keeps in
an art-like photo goes to Gemini (billed key), each request recorded in llm_usage; a failed call blurs the face;
docs/privacy.md names Google as the processor and the data-processing agreement as the operator's responsibility.
Usage: python measure.py <repo root>. Prints JSON and writes measurements.json here.

Every outbound request is recorded, never sent: requests.post and the Claude call are replaced by
recorders, and the YuNet model (not in the repository) by a stand-in that proposes one face box on a
deterministic synthetic photo. The rest is the real code path of src/privacy.py and src/db.py.

Development photos (dev evidence only): where the photos named in dev_faces.json are present (under the repo
root, or under RR_FIXTURE_REPO), each is passed through the real detect_human_faces with both cloud models
unconfigured; YuNet (tools/yunet.onnx, not in the repository) is replayed from the candidates recorded in
dev_faces.json, each labelled by viewing it. Photos whose source record marks them as artwork are skipped, as
blur_human_faces skips them. A candidate counts as blurred when its centre falls inside a returned box."""

import concurrent.futures
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

STORES = ("audit_log", "audience_", "cache/privacy", "assets/")
TABLES = ("Purpose", "Retention", "Processors", "Deletion")


class FakeYuNet:
    def detect(self, img):
        import numpy as np
        x, y, w, h = 160, 145, 80, 110  # a frontal face's eyes, nose tip and mouth corners, as YuNet places them
        marks = [x + .32 * w, y + .4 * h, x + .68 * w, y + .4 * h, x + .5 * w, y + .6 * h, x + .36 * w, y + .78 * h,
                 x + .64 * w, y + .78 * h]
        return 1, np.array([[x, y, w, h] + marks + [0.9]], dtype=np.float32)


def photo(colour=True):
    import cv2
    import numpy as np
    rng = np.random.default_rng(5)
    img = np.full((400, 400, 3), (70, 100, 130), np.uint8)
    img = cv2.add(img, rng.integers(0, 20, img.shape, dtype=np.uint8))
    cv2.ellipse(img, (200, 200), (45, 60), 0, 0, 360, (140, 165, 215), -1)
    if not colour:
        img = cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
    return img


def faces_metric(config, privacy):
    import requests
    calls = []
    config.GEMINI_API_KEY = "AIza-measure-only"
    privacy.claude_available = lambda: True
    requests.post = lambda *a, **k: calls.append("gemini") or SimpleNamespace(
        status_code=200, headers={}, json=lambda: {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
    privacy.structured_call = lambda *a, **k: calls.append("claude") or SimpleNamespace(is_living_human=True)
    privacy._get_yunet = lambda w, h: FakeYuNet()
    colour = privacy.detect_human_faces(photo())
    sent_colour = list(calls)
    calls.clear()
    mono = privacy.detect_human_faces(photo(colour=False))
    sent_mono = list(calls)
    return {
        "face_verify_cloud_default": getattr(config, "FACE_VERIFY_CLOUD", None),
        "outbound_requests_colour_photo": len(sent_colour),
        "outbound_requests_monochrome_photo": len(sent_mono),
        "outbound_by_processor": sorted(set(sent_colour + sent_mono)),
        "colour_face_blurred": len(colour),
        "archival_monochrome_face_blurred": len(mono),
    }


def ambiguous_metric(config, privacy, root):
    """Operator decision 2026-11-05 (option C): faces the local check keeps in an art-like photo go to Gemini's paid
    tier only with FACE_VERIFY_CLOUD_AMBIGUOUS on, as crops, each recorded in llm_usage; off or failed, blurred."""
    import base64

    import cv2
    import numpy as np
    import requests

    from src.db import PipelineDatabase
    if not hasattr(privacy, "ambiguous_cloud"):
        return {"ambiguous_setting_default": None, "ambiguous_check": "absent"}
    sent, total, answer = [], [], {"code": 200}

    def post(url, json=None, timeout=None, headers=None):
        sent.append(json)
        total.append(url)
        body = {"candidates": [{"content": {"parts": [{"text": '{"is_living_human": false}'}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 20}}
        return SimpleNamespace(status_code=answer["code"], headers={}, json=lambda: body)
    requests.post = post
    config.GEMINI_API_KEY = "AIza-measure-only"
    privacy.claude_available = lambda: True
    privacy._get_yunet = lambda w, h: FakeYuNet()
    out = {"ambiguous_setting_default": config.FACE_VERIFY_CLOUD_AMBIGUOUS}
    with tempfile.TemporaryDirectory() as tmp:
        bank = Path(tmp) / "topic_bank.json"
        bank.write_text(json.dumps({"topics": []}), encoding="utf-8")
        db = PipelineDatabase(db_path=Path(tmp) / "m.db", topic_bank_file=bank)
        privacy._usage_ledger = lambda: db
        off = privacy.detect_human_faces(photo(), art_like=True)
        out["ambiguous_off_requests"], out["ambiguous_off_face_blurred"] = len(sent), len(off)
        config.FACE_VERIFY_CLOUD_AMBIGUOUS = True
        sent.clear()
        privacy.detect_human_faces(photo(), art_like=False)
        out["ambiguous_on_requests_plain_photo"] = len(sent)
        sent.clear()
        on = privacy.detect_human_faces(photo(), art_like=True)
        out["ambiguous_on_requests_art_like_photo"] = len(sent)
        out["ambiguous_on_carving_answer_blurred"] = len(on)
        data = sent[0]["contents"][0]["parts"][1]["inline_data"]["data"] if sent else ""
        crop = cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR) if data else None
        out["ambiguous_sent_crop_px"] = list(crop.shape[:2]) if crop is not None else None
        answer["code"] = 400  # a failed call
        failed = privacy.detect_human_faces(photo(), art_like=True)
        out["ambiguous_failed_call_face_blurred"] = len(failed)
        with db._conn() as conn:
            out["ambiguous_ledger_rows"] = conn.execute("SELECT COUNT(*) FROM llm_usage WHERE task = ?",
                                                        (privacy.AMBIGUOUS_TASK,)).fetchone()[0]
        out["ambiguous_requests_total"] = len(total)
        config.FACE_VERIFY_CLOUD_AMBIGUOUS = False
    text = (root / "docs" / "privacy.md").read_text(encoding="utf-8") if (root / "docs" / "privacy.md").exists() else ""
    out["privacy_md_names_google_dpa_operator"] = all(s in text for s in (
        "FACE_VERIFY_CLOUD_AMBIGUOUS", "Google (Gemini API, paid tier", "Data Processing Addendum",
        "operator's responsibility"))
    return out


class ReplayYuNet:
    """Hands detect_human_faces the YuNet output recorded for the photo it is given."""
    def __init__(self):
        self.rows = {}

    def detect(self, img):
        import numpy as np
        rows = self.rows.get(id(img))
        return 1, (np.array(rows, dtype=np.float32) if rows else None)


NAMED = ("dyatlov_pass_incident/02", "dyatlov_pass_incident/07", "dyatlov_pass_incident/08",
         "kailasa_temple_monolith/03", "kailasa_temple_monolith/05", "kailasa_temple_monolith/16",
         "kailasa_temple_monolith/20")


def dev_metric(root, config, privacy):
    import cv2
    data = json.loads((Path(__file__).resolve().parent / "dev_faces.json").read_text(encoding="utf-8"))
    base = Path(os.environ.get("RR_FIXTURE_REPO") or root)
    present = [p for p in data["photos"] if (base / p["photo"]).is_file()
               and hashlib.sha256((base / p["photo"]).read_bytes()).hexdigest()[:16] == p["sha256_16"]]
    if not present:
        return {"dev_photos": "not available (the development photos are not here)"}
    config.GEMINI_API_KEY = ""
    privacy.claude_available = lambda: False
    replay = ReplayYuNet()
    privacy._get_yunet = lambda w, h: replay

    def run(p):
        path = base / p["photo"]
        if privacy.is_artwork(path):
            return p, False, None
        art = bool(getattr(privacy, "looks_like_art", lambda _: False)(path))
        img = cv2.imread(str(path))
        if p["detector"] == "yunet":
            replay.rows[id(img)] = [c["box"] + c["landmarks"] + [c["score"]] for c in p["candidates"]]
        faces = privacy.detect_human_faces(img)
        replay.rows.pop(id(img), None)
        return p, art, [any(fx <= x + w // 2 <= fx + fw and fy <= y + h // 2 <= fy + fh for (fx, fy, fw, fh) in faces)
                   for (x, y, w, h) in (c["box"] for c in p["candidates"])]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, present))
    count = {"face": [0, 0], "not": [0, 0]}
    ambiguous = {"face": 0, "not": 0}  # kept locally in an art-like photo: what the setting would send, by label
    named = {}
    for p, art, blurred in results:
        if blurred is None:
            continue
        for c, b in zip(p["candidates"], blurred):
            if b and art and c["label"] in ambiguous:
                ambiguous[c["label"]] += 1
        key = p["photo"].split("/", 1)[1][:-len("_commons.jpg")]
        tally = {"face": [0, 0], "not": [0, 0]}
        for c, b in zip(p["candidates"], blurred):
            if c["label"] in tally:
                tally[c["label"]][0] += b
                tally[c["label"]][1] += 1
                count[c["label"]][0] += b
                count[c["label"]][1] += 1
        if key in NAMED:
            named[key] = {"faces_blurred": "{}/{}".format(*tally["face"]), "non_faces_blurred": "{}/{}".format(*tally["not"])}
    return {"dev_photos_checked": sum(1 for _, _a, b in results if b is not None),
            "dev_art_like_photos_checked": sum(1 for _, a, b in results if b is not None and a),
            "dev_ambiguous_crops_faces_vs_non_faces": "{} faces / {} non-faces".format(ambiguous["face"], ambiguous["not"]),
            "dev_faces_blurred": "{}/{}".format(*count["face"]),
            "dev_non_faces_blurred": "{}/{}".format(*count["not"]),
            "dev_named_photos": named}


def doc_metric(root):
    path = root / "docs" / "privacy.md"
    if not path.exists():
        return {"privacy_md_exists": False, "data_map_tables": 0, "tables_covering_all_four_stores": 0}
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    found = {}
    for s in sections:
        m = re.match(r"\d\. (\w+)", s)
        if m and m.group(1) in TABLES and "|---|" in s:
            found[m.group(1)] = all(store in s for store in STORES)
    return {"privacy_md_exists": True, "data_map_tables": len(found),
            "tables_covering_all_four_stores": sum(found.values())}


def prune_metric(root):
    from src import audience
    from src.db import PipelineDatabase
    with tempfile.TemporaryDirectory() as tmp:
        bank = Path(tmp) / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "T", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        db = PipelineDatabase(db_path=Path(tmp) / "m.db", topic_bank_file=bank)
        audience.ensure_schema(db)
        old = (datetime.now(UTC) - timedelta(days=800)).isoformat()
        with db._conn() as conn:
            conn.execute("INSERT INTO audience_metrics VALUES (?,?,?,?,?,?)", ("r", "instagram", "28d", "x", "{}", old))
            conn.execute("INSERT INTO audience_media_ids VALUES (?,?,?,?,?)", ("r", "instagram", "1", "x", old))
            conn.execute("INSERT INTO audience_sources VALUES (?,?,?,?,?,?,?)", (old, "r", "28d", "instagram", "x", "ok", ""))
        pruned = db.prune_history()
        rules = {t: d for t, d in PipelineDatabase.RETENTION_DAYS.items() if t.startswith("audience_")}
        return {"audience_tables_with_prune_rule": len(rules), "audience_retention_days": rules,
                "audience_rows_pruned_at_800_days": sum(v for k, v in pruned.items() if k.startswith("audience_"))}


def main(root: Path) -> dict:
    os.environ.pop("FACE_VERIFY_CLOUD", None)
    sys.path.insert(0, str(root))
    os.chdir(root)
    from src import config, privacy
    out = {**faces_metric(config, privacy), **doc_metric(root), **prune_metric(root),
           **ambiguous_metric(config, privacy, root)}
    out.update(dev_metric(root, config, privacy))  # after: it replaces the stand-ins faces_metric set
    out["acceptance_met"] = (out["outbound_requests_colour_photo"] == 0 and out["outbound_requests_monochrome_photo"] == 0
                             and out["archival_monochrome_face_blurred"] == 1 and out["data_map_tables"] == 4
                             and out["tables_covering_all_four_stores"] == 4
                             and out["audience_tables_with_prune_rule"] == 3
                             and out.get("ambiguous_setting_default") is False
                             and out.get("ambiguous_off_requests") == 0 and out.get("ambiguous_off_face_blurred") == 1
                             and out.get("ambiguous_on_requests_plain_photo") == 0
                             and out.get("ambiguous_on_requests_art_like_photo") == 1
                             and out.get("ambiguous_failed_call_face_blurred") == 1
                             and out.get("ambiguous_ledger_rows") == out.get("ambiguous_requests_total")
                             and out.get("privacy_md_names_google_dpa_operator") is True)
    return out


if __name__ == "__main__":
    result = main(Path(sys.argv[1]).resolve())
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    (Path(__file__).resolve().parent / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
