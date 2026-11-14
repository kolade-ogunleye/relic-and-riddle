"""QF-83: the 16:9 YouTube end screen is untouched by the FOLLOW (only the 9:16 loop bridge draws it).

    python render_endscreen.py <base root> <build root> <16:9 master .mp4> <out .jpg>

Reads the master's frame 10.5 s before its end (the narration's last frame: a development 16:9 master, read-only),
renders end_card.frames() on it with each root's code (a fresh interpreter per root) and prints JSON: per root the
SHA-256 of every frame's raw RGB bytes, and whether the two roots' frames are identical. Writes <out .jpg>: frames 0,
the middle and the last of each root at half size (base above, build below) and nothing else outside the system temp
folder.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CHILD = r"""
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from PIL import Image
from src import end_card
last = Image.open(sys.argv[2]).convert("RGB")
hashes, keep = [], []
frames = list(end_card.frames(last, "Send this to a friend who loves geology."))
for i, f in enumerate(frames):
    hashes.append(hashlib.sha256(f.tobytes()).hexdigest())
for j, i in enumerate((0, len(frames) // 2, len(frames) - 1)):
    frames[i].resize((960, 540), Image.LANCZOS).save(f"{sys.argv[3]}_{j}.png")
print(json.dumps({"frames": len(frames), "sha256": hashlib.sha256("".join(hashes).encode()).hexdigest()}))
"""


def main():
    base, build, video, out = (Path(a) for a in sys.argv[1:5])
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="qf83-es-") as tmp:
        t = Path(tmp)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(video)], capture_output=True, text=True, check=True).stdout.strip())
        last = t / "last.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, dur - 10.5):.3f}", "-i", str(video),
                        "-frames:v", "1", str(last)], check=True)
        result = {}
        for name, root in (("base", base), ("build", build)):
            run = subprocess.run([sys.executable, "-c", CHILD, str(root.resolve()), str(last), str(t / name)],
                                 cwd=root, capture_output=True, text=True, check=True)
            result[name] = json.loads(run.stdout.strip().splitlines()[-1])
        sheet = Image.new("RGB", (960 * 3, 540 * 2))
        for row, name in enumerate(("base", "build")):
            for j in range(3):
                with Image.open(t / f"{name}_{j}.png") as im:
                    sheet.paste(im, (960 * j, 540 * row))
        sheet.save(out, quality=74)
    result["identical"] = result["base"] == result["build"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
