"""Resume a bounded, non-publishing validation download and retain partial evidence."""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import commons
from src.credits import sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    credits, outcomes = [], []
    for index, candidate in enumerate(candidates, 1):
        path = args.output / f"{index:02d}_commons.jpg"
        try:
            if path.is_file():
                with Image.open(path) as image:
                    image.verify()
            else:
                commons.download_jpeg(candidate["download_url"], path, max_wait=0)
            credits.append({**candidate, "file": path.name, "sha256": sha256_file(path)})
            outcome = {"file": path.name, "status": "downloaded"}
        except Exception as exc:
            outcome = {"file": path.name, "status": "failed", "error": str(exc)}
        outcomes.append(outcome)
        (args.output / "credits.json").write_text(json.dumps({"reviewed": False, "images": credits}, indent=2), encoding="utf-8")
        (args.output / "downloads.json").write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
        print(json.dumps(outcome), flush=True)
    return 0 if all(o["status"] == "downloaded" for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
