"""Evidence renders for the audience chart (2026-10-29). Every render here is SAMPLE DATA, watermarked on the
chart: the development database has no audience_metrics rows. Run from the repo root:
    .venv/Scripts/python.exe docs/reviews/audience-chart-2026-10-29/render_evidence.py [plex_fonts_dir]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PIL import Image  # noqa: E402

from src import audience_chart as ac  # noqa: E402

OUT = Path(__file__).resolve().parent
MONO = Path(sys.argv[1]) if len(sys.argv) > 1 else None
TODAY = datetime(2026, 10, 29)


def reel(title, ig, yt, fb):
    return {"run_id": title, "title": title, "views": {"instagram": ig, "youtube": yt, "facebook": fb}}


# the critic's round-4 sample values (newest first), with titles from the topic bank
SIX = [reel("Derinkuyu: the city under Cappadocia", 3120, 1150, 1010),
       reel("Gol Gumbaz: the whispering gallery", 2410, 1260, 940),
       reel("The Richat Structure, the Eye of the Sahara", 2630, 1080, 880),
       reel("Roman concrete that heals itself", 1960, 1450, 1020),
       reel("Zhang Heng's seismoscope", 2210, 1190, 760),
       reel("The Piri Reis map of 1513", 1840, 1320, 910)]
CASES = {
    "empty": [],
    "one_reel": SIX[:1],
    "six_reels": SIX,
    "missing_facebook": [{**r, "views": {**r["views"], "facebook": None}} for r in SIX],
    "mixed_leaders_gaps": [reel("Derinkuyu: the city under Cappadocia", 3120, 4100, None),
                           reel("Gol Gumbaz: the whispering gallery", 2410, 1260, 940),
                           reel("Dyatlov Pass", None, 1080, 0),
                           reel("Roman concrete that heals itself", 1960, 1960, 1020),
                           reel("Zhang Heng's seismoscope", 0, 0, 0),
                           reel("The Piri Reis map of 1513", None, 1320, None)],
    "large_numbers_long_titles": [
        reel("The Sunken City of Heracleion, lost beneath the Bay of Abu Qir for twelve centuries", 1_284_500, 96_400, 12_480),
        reel("Camp Century: the nuclear city the US Army built inside the Greenland ice sheet", 845_000, 1_020_000, 9_999),
        reel("Supercalifragilisticexpialidociousandthensomemorewithoutspaces", 412_000, 38_000, 150_000),
        reel("Flannan Isles lighthouse 🌊 the keepers who vanished", 999_600, 500_000, 120)],
}
summary = {}
for name, reels in CASES.items():
    # the empty state is what production draws with no rows, so it carries no watermark
    im, info = ac.render(reels, OUT / f"{name}.png", sample=bool(reels), today=TODAY, mono_dir=MONO)
    im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(OUT / f"{name}_half.jpg", quality=88)
    im.resize((400, round(im.height * 400 / im.width)), Image.LANCZOS).save(OUT / f"{name}_preview400.png")
    (OUT / f"{name}.png").unlink()  # the half-size JPEG and the 400 px preview are the evidence kept
    summary[name] = {k: info[k] for k in ("headline", "subtitle", "mono_font", "size") if k in info}
    summary[name]["titles"] = info.get("titles")
    summary[name]["smallest_text_px_at_400"] = round(min(t["size"] for t in info["texts"]) * 400 / ac.W, 1)
    summary[name]["lowest_contrast"] = round(min(ac.contrast(t["fill"], ac.INK) for t in info["texts"]), 2)
# the repo as it is today (no Plex Mono in fonts/ yet): figures fall back to Montserrat SemiBold
im, info = ac.render(SIX, sample=True, today=TODAY, mono_dir=OUT / "_no_plex_here")
im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(OUT / "six_reels_fallback_font_half.jpg", quality=88)
summary["six_reels_fallback_font"] = {"mono_font": info["mono_font"], "headline": info["headline"]}

# the real path: the development database through chart_for(), as a call site would send it
from src.db import PipelineDatabase  # noqa: E402
dev = PipelineDatabase()
with dev._conn() as conn:
    n_rows = conn.execute("SELECT COUNT(*) FROM audience_metrics").fetchone()[0]
info = ac.chart_for(dev, OUT / "dev_db.png", today=TODAY)
im = Image.open(OUT / "dev_db.png")
im.convert("RGB").resize((im.width // 2, im.height // 2), Image.LANCZOS).save(OUT / "dev_db_half.jpg", quality=88)
(OUT / "dev_db.png").unlink()
summary["dev_db"] = {"audience_metrics_rows": n_rows, "headline": info["headline"], "caption": info["caption"],
                     "rows_drawn": info["rows"], "mono_font": info["mono_font"]}
summary["cvd"] = ac.cvd_separation()
(OUT / "measurements.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
print(json.dumps(summary, indent=1, ensure_ascii=False))
