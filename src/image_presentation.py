"""Explicit essential-photo exceptions, bound to bytes and inherited by derivatives.

The automatic pipeline never enlarges these images. A separately prepared,
whole-image enlargement may be selected only after an operator reviews it.
"""

import json
from pathlib import Path

from PIL import Image, ImageOps

from .credits import sha256_file


def sidecar(path: Path) -> Path:
    return Path(str(path) + ".presentation.json")


def designate(path: Path, reason: str) -> None:
    """Designate an essential source explicitly; ordinary Keep is not an exception."""
    path = Path(path)
    if not reason.strip() or not path.is_file():
        raise ValueError("An existing essential image and a reason are required")
    with Image.open(path) as image:
        image.verify()
    sidecar(path).write_text(json.dumps({"sha256": sha256_file(path),
        "presentation": "full_image", "reason": reason.strip()}, indent=2), encoding="utf-8")


def _record(path: Path) -> dict:
    marker = sidecar(path)
    if not marker.exists():
        return {}
    data = json.loads(marker.read_text(encoding="utf-8"))
    if data.get("sha256") != sha256_file(path) or data.get("presentation") != "full_image":
        raise ValueError(f"Stale essential-image designation: {path.name}")
    return data


def full_image(path: Path) -> bool:
    """Follow checked lineage, so colour/privacy copies retain the constraint."""
    from .lineage import sidecar as lineage_sidecar
    from .lineage import source_chain

    path = Path(path)
    if _record(path):
        return True
    if not lineage_sidecar(path).exists():
        return False
    sources = source_chain(path)
    essential = any(_record(Path(source["path"])) for source in sources if source.get("kind") != "lineage")
    if essential:
        allowed = {"colour_balance", "face_blur", "reviewed_full_image_enlargement"}
        for source in sources:
            if source.get("kind") == "lineage":
                data = json.loads(Path(source["path"]).read_text(encoding="utf-8"))
                if data.get("operation") not in allowed:
                    raise ValueError("Essential image has a cached crop or unreviewed enlargement; prepare from its source")
    return essential


def reviewed_enlargement(source: Path, derivative: Path, review: str) -> None:
    """Record explicit visual review at delivery size, never inferred from a model."""
    from .lineage import record_derived

    source, derivative = Path(source).resolve(), Path(derivative).resolve()
    data = _record(source)
    if not data or not review.strip() or source == derivative:
        raise ValueError("An essential source, separate derivative and visual review are required")
    with Image.open(source) as original, Image.open(derivative) as enlarged:
        w, h = ImageOps.exif_transpose(original).size
        dw, dh = ImageOps.exif_transpose(enlarged).size
    if dw < w or dh < h or abs(dw / w - dh / h) > 2 / min(w, h):
        raise ValueError("Reviewed enlargement must retain the whole image's aspect ratio")
    record_derived(derivative, [source], "reviewed_full_image_enlargement", {"visual_review": review.strip()})
    data["reviewed_enlargement"] = {"path": str(derivative), "sha256": sha256_file(derivative),
                                     "visual_review": review.strip()}
    sidecar(source).write_text(json.dumps(data, indent=2), encoding="utf-8")


def prepared_source(path: Path) -> Path:
    """Only a direct source selects an approved derivative; never undo privacy edits."""
    path = Path(path)
    approved = _record(path).get("reviewed_enlargement")
    if not approved:
        return path
    derivative = Path(approved["path"])
    if sha256_file(derivative) != approved["sha256"]:
        raise ValueError("Reviewed enlargement changed; visual review is required again")
    full_image(derivative)  # validate the original and derivative lineage too
    return derivative


def fit_filter(path: Path, width: int, height: int) -> str:
    """Whole image at native size or smaller, centred without a moving crop."""
    with Image.open(path) as image:
        w, h = ImageOps.exif_transpose(image).size
    scale = min(1.0, width / w, height / h)
    fw, fh = max(1, int(w * scale)), max(1, int(h * scale))
    return f"scale={fw}:{fh}:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"


def full_image_spans(items, starts, ends, bridge: tuple[float, float] | None = None) -> list:
    """The master's intervals (start, end in seconds) that show an essential image whole, padded by fit_filter, for
    the composition record: the release gate's bar check reads them instead of guessing from pixels (QF-10). A
    loop bridge (start, end) that leaves or lands on an essential first or last scene shows its padding too."""
    from .typography import is_video
    whole = [not is_video(Path(item)) and full_image(Path(item)) for item in items]
    spans = [[round(a, 3), round(b, 3)] for keep, a, b in zip(whole, starts, ends) if keep]
    if bridge and whole and (whole[0] or whole[-1]):
        spans.append([round(bridge[0], 3), round(bridge[1], 3)])
    return spans


def fit_image(source: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Still-format counterpart of fit_filter, including the native-size ceiling."""
    photo = ImageOps.exif_transpose(source).convert("RGBA")
    photo.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    canvas.alpha_composite(photo, ((size[0] - photo.width) // 2, (size[1] - photo.height) // 2))
    return canvas


def policy_inputs(paths) -> dict:
    """Changing a designation/review invalidates cached presentations too."""
    from .lineage import source_chain

    result = {}
    for path in paths:
        sources = [Path(path), *[Path(s["path"]) for s in source_chain(path) if s.get("kind") != "lineage"]]
        for source in sources:
            marker = sidecar(source)
            result[str(marker)] = sha256_file(marker)
    return result


def main() -> None:
    """Operator entry point: designate source; optionally record a reviewed derivative."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--reviewed-derivative", type=Path)
    parser.add_argument("--visual-review")
    args = parser.parse_args()
    if args.reviewed_derivative:
        reviewed_enlargement(args.source, args.reviewed_derivative, args.visual_review or "")
    else:
        designate(args.source, args.reason or "")


if __name__ == "__main__":
    main()
