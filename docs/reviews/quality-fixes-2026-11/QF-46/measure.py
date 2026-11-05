"""QF-46 acceptance metric: illustration spend is capped and ledgered.

    python docs/reviews/quality-fixes-2026-11/QF-46/measure.py <repo root>

Imports <repo root>'s own `src` and drives its real illustration path (image_gen.illustrate_beat and
generate_image, media_budget, the pipeline database's media_usage table, control.spend_lines) with a fake
`requests.post` that counts requests and answers HTTP 200 with a tiny image; every socket is blocked, so nothing
leaves the machine, and the database is a scratch file in a temporary folder. Two scenarios, as the row states them:

  A. MEDIA_MONTHLY_BUDGET_USD = 0: a run's illustration work (two beats, four candidates each, the per-run cap of 8,
     plus one legacy linework call) - how many image requests are sent.
  B. MEDIA_MONTHLY_BUDGET_USD = 5: one request answered 200 - how many media_usage rows it inserts and whether the
     dashboard's spend lines show it as "AI images".

Prints the metric as JSON and writes measurements.json beside this script. Paths printed are repo-relative.
"""
import base64
import io
import json
import socket
import sys
import tempfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
MODEL = "gemini-3-pro-image"


class _Resp:
    def __init__(self, data, status=200):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (64, 112), 200).save(buf, "PNG")
    return buf.getvalue()


def measure(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from src import config, control, image_gen
    from src.db import PipelineDatabase

    sent: list[str] = []
    body = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png",
                                                                  "data": base64.b64encode(_png()).decode()}}]}}]}

    def post(url, headers=None, json=None, timeout=None):
        sent.append(url.rsplit("/", 1)[-1])
        return _Resp(body)

    def no_network(*a, **k):
        raise OSError("measure.py: no network")

    result: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        db = PipelineDatabase(tmp / "pipeline.db")
        patches = [
            mock.patch.object(socket.socket, "connect", no_network),
            mock.patch.object(config, "GEMINI_API_KEY", "k-measure", create=True),
            mock.patch.object(config, "GENERATIVE_GAP_FILLING", True, create=True),
            mock.patch.object(config, "IMAGE_GEN_MODEL", MODEL, create=True),
            mock.patch.object(config, "IMAGE_GEN_API", "auto", create=True),
            mock.patch.object(image_gen, "is_configured", lambda v: bool(v)),
            mock.patch.object(image_gen, "list_models", lambda timeout=15.0: [MODEL]),
            mock.patch.object(image_gen, "checkers_ready", lambda: (True, "")),
            mock.patch.object(image_gen.requests, "post", post),
            mock.patch("src.db.PipelineDatabase", lambda *a, **k: db),
        ]
        for p in patches:
            p.start()
        try:
            image_gen._STATUS.clear()
            # A: a zero media budget
            with mock.patch.object(config, "MEDIA_MONTHLY_BUDGET_USD", 0.0, create=True):
                run = tmp / "run_a"
                budget = image_gen.Budget(run, limit=8)
                for line in ("The fort was rebuilt in stone.", "Merchants crossed the pass in winter."):
                    image_gen.illustrate_beat(line, {"era": "medieval", "required": []}, run, budget=budget,
                                              candidates=4)
                image_gen.generate_raw_linework("an engraving of a harbour")
            result["zero_budget_image_requests"] = len(sent)
            with db._conn() as conn:
                result["zero_budget_media_usage_rows"] = conn.execute("SELECT COUNT(*) FROM media_usage").fetchone()[0]
            # B: a funded budget, one fake 200
            sent.clear()
            with mock.patch.object(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0, create=True):
                got = image_gen.generate_image("draw")
            result["funded_requests"] = len(sent)
            result["funded_image_returned"] = bool(got)
            with db._conn() as conn:
                rows = [dict(r) for r in conn.execute("SELECT provider, item, cost_usd FROM media_usage")]
            result["fake_200_media_usage_rows"] = len(rows)
            result["fake_200_rows"] = rows
            lines = control.spend_lines(db)
            result["dashboard_spend_lines"] = lines
            result["dashboard_shows_ai_images"] = any(line.startswith("AI images this month:") and "$0.00" not in
                                                      line.split("(")[0] for line in lines)
        finally:
            for p in reversed(patches):
                p.stop()
            image_gen._STATUS.clear()
    result["acceptance_met"] = (result["zero_budget_image_requests"] == 0 and result["fake_200_media_usage_rows"] == 1
                                and result["dashboard_shows_ai_images"])
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: measure.py <repo root>")
    out = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(out, indent=2)
    print(text)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
