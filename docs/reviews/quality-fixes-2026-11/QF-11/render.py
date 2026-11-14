"""QF-11 evidence: the gate's loudness meters over a real development Reel and over the generated 50 Hz drone.

    python render.py <repo root> <a development master .mp4> <out.jpg>

Draws, from <repo root>'s src.audio_qa decode (its ebur128 frame log), the short-term loudness of the full band and
of the phone-speaker simulation, and the windows the phone note marks. Top: the real master; bottom: the
"subbass" fixture of tests/test_qf11_audio_qa.py. Writes only <out.jpg> (half size, JPEG) and a temporary folder.
"""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

root, real, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(root))
from src import audio_qa

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qf11_fixtures", here.parents[3] / "tests/test_qf11_audio_qa.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
FONT = root / "fonts"


def series(path: Path):
    res = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-filter_complex",
                          audio_qa.graph(), "-map", "[out]", "-f", "null", "-"], capture_output=True, text=True,
                         check=True, timeout=600)
    frames = audio_qa._frames(res.stderr)
    return frames["full"], frames["phone"], audio_qa.measure(path)


def font(size):
    for name in ("Montserrat-Variable.ttf",):
        if (FONT / name).is_file():
            return ImageFont.truetype(str(FONT / name), size)
    return ImageFont.load_default()


def panel(draw, box, title, full, phone, measured):
    x0, y0, x1, y1 = box
    lo, hi = -40.0, -5.0
    end = max(f[0] for f in full)
    sx = lambda t: x0 + 90 + (x1 - x0 - 110) * t / end
    sy = lambda v: y1 - 50 - (y1 - y0 - 150) * (min(hi, max(lo, v)) - lo) / (hi - lo)
    draw.text((x0 + 20, y0 + 15), title, font=font(30), fill=(20, 20, 20))
    phone_check = next(c for c in measured["checks"] if c["name"] == "audio_phone")
    for span in phone_check["intervals"]:
        draw.rectangle([sx(span["start"]), sy(hi), sx(span["end"]), sy(lo)], fill=(255, 226, 200))
    for v in range(int(lo), int(hi) + 1, 5):
        draw.line([sx(0), sy(v), sx(end), sy(v)], fill=(225, 225, 225))
        draw.text((x0 + 15, sy(v) - 12), f"{v} LUFS", font=font(18), fill=(90, 90, 90))
    for t in range(0, int(end) + 1, 5 if end < 60 else 30):
        draw.text((sx(t) - 8, y1 - 40), f"{t}s", font=font(18), fill=(90, 90, 90))
    for data, colour in ((full, (30, 60, 160)), (phone, (200, 80, 20))):
        pts = [(sx(f[0]), sy(f[2])) for f in data if f[0] >= 3 and f[2] is not None]
        draw.line(pts, fill=colour, width=3)
    p = measured["phone"]
    note = (f"full band (blue) {measured['integrated_lufs']} LUFS; phone {p['band_hz'][0]}-{p['band_hz'][1]} Hz "
            f"(orange) {p['integrated_lufs']} LUFS; worst 3 s window {p['worst_window_loss_lu']} LU below; "
            f"note above {audio_qa.config.AUDIO_QA_PHONE_LOSS_LU:.0f} LU: "
            + ("shown (shaded)" if phone_check.get("minor") else "none"))
    draw.text((x0 + 20, y0 + 55), note, font=font(20), fill=(40, 40, 40))


with tempfile.TemporaryDirectory(prefix="qf11-render-") as temp:
    drone = fixtures.master(Path(temp) / "subbass.mp4", "subbass", seconds=12.0)
    rows = [(f"Real Reel: {real.parent.name}", *series(real)),
            ("Generated: voice over a bed for 12 s, a 50 Hz drone from 2 s to 6 s", *series(drone))]
w, h = 2000, 1300
img = Image.new("RGB", (w, h), "white")
d = ImageDraw.Draw(img)
for i, (title, full, phone, measured) in enumerate(rows):
    panel(d, (0, i * h // 2, w, (i + 1) * h // 2), title, full, phone, measured)
img = img.resize((w // 2, h // 2), Image.LANCZOS)
img.save(out, quality=88)
print(out, out.stat().st_size)
