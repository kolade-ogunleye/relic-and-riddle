"""Ten offline category-fallback fetches; only input is the repository root.

The committed regression fixture supplies namesake metadata and generated JPEGs.
Semantic labels are fixture truth, not a live vision-model assessment.
"""

import contextlib
import io
import json
import runpy
import sys
import tempfile
from pathlib import Path


def main(root):
    sys.path.insert(0, str(root))
    fixture = runpy.run_path(str(root / "tests/test_qf55_category_anchor.py"))
    results = []
    with tempfile.TemporaryDirectory(prefix="qf55-") as temp:
        for subject in fixture["SUBJECTS"]:
            with contextlib.redirect_stdout(io.StringIO()):
                images, queries = fixture["fallback_fixture"](Path(temp), subject)
            results.append({"subject": subject, "selected": len(images),
                            "shows_topic_no_fixture_labels": sum("unrelated film" in im["title"] for im in images),
                            "category_queries": [q for q in queries if q.startswith("incategory:")]})
    specs = json.loads((root / "data/topics/image_queries.json").read_text(encoding="utf-8"))
    result = {"source_commit": "the commit that adds this file",
              "scope": "ten offline fetches with generated images and namesake metadata; fixture truth, no live model",
              "identity_configured_topics": sum(bool(spec.get("exclude") or spec.get("qid"))
                                                for key, spec in specs.items() if not key.startswith("_")),
              "fetches": results,
              "shows_topic_no_fixture_labels": sum(row["shows_topic_no_fixture_labels"] for row in results)}
    output = root / "docs/reviews/quality-fixes-2026-11/QF-55/measurements.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), ensure_ascii=False, indent=2))
