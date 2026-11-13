"""Caption faces derived locally from the bundled, licensed sources (QF-87).

libass applies no OpenType feature a subtitle asks for, and selects no instance of a variable font. Two faces are
therefore derived with fontTools from the files in fonts/, never downloaded and never committed:

- CAPTION_FAMILY ("RR Caption"): Anton with its own `smcp` glyphs as the default forms of its lower case (each
  code point is re-mapped to the glyph HarfBuzz chooses for it with smcp on), so a 9:16 caption keeps the
  narration's case and accents in true small capitals; no outline or advance is changed and no capital is scaled.
- WIDE_FAMILY ("RR Sans"): the static weight-700 instance of the one Montserrat source (Montserrat-Variable.ttf),
  the 16:9 caption face (the second Montserrat file, Montserrat-Bold.ttf, is gone).

Each is built once per source/builder identity into BASE_DIR/cache/font_instances/<key>/, beside a copy of the
source's OFL notice and a licence record naming its source and hashes (font_evidence.font_rights checks both), and
rebuilt when missing or altered. The bytes are reproducible (no timestamps). build_subtitles embeds the face in the
ASS file (ass_embedded_font), so libass draws it without an installed font.
"""
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path

import fontTools
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from .config import FONTS_DIR
from .credits import sha256_file
from .font_evidence import font_rights

CAPTION_FAMILY = "RR Caption"
WIDE_FAMILY = "RR Sans"
DERIVED_FAMILIES = {CAPTION_FAMILY: ("Anton-Regular.ttf", "smcp"),
                    WIDE_FAMILY: ("Montserrat-Variable.ttf", "wght700")}


def _same(path: Path, data: bytes) -> bool:
    try:
        return path.is_file() and path.read_bytes() == data
    except OSError:
        return False


def _replace_file(path: Path, data: bytes) -> None:
    """Writes `data` to `path` through a temporary file in the same folder (atomic replace). Several processes may
    build the same face at once (test workers, a render pool): the bytes are reproducible, so a file that already
    holds them is left alone, and a replace Windows refuses while another process has the file open is retried
    until that file holds the same bytes (or the retries run out)."""
    if _same(path, data):
        return
    handle, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        for attempt in range(40):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if _same(path, data):
                    return
                time.sleep(0.05 * (attempt + 1))
        raise PermissionError(f"Could not replace {path}")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _rename(font, family: str, bold: bool) -> None:
    """The derived face's own names (family, style, full and PostScript names), so libass matches it by family
    and never takes it for its source."""
    style = "Bold" if bold else "Regular"
    values = {1: family, 2: style, 3: family + " " + style + " derived-1",
              4: family + " " + style, 6: family.replace(" ", "") + "-" + style,
              16: family, 17: style}
    for name_id, value in values.items():
        font["name"].removeNames(nameID=name_id)
        font["name"].setName(value, name_id, 3, 1, 0x409)
        font["name"].setName(value, name_id, 1, 0, 0)


def _small_caps_cmap(font, source: Path) -> None:
    """Re-maps every code point to the glyph HarfBuzz shapes it to with `smcp` on (a single glyph only; a code
    point smcp leaves alone, or splits, keeps its own glyph)."""
    shaping = hb.Font(hb.Face(source.read_bytes()))
    mapped = {}
    for codepoint, original in (font.getBestCmap() or {}).items():
        buffer = hb.Buffer()
        buffer.add_str(chr(codepoint))
        buffer.guess_segment_properties()
        hb.shape(shaping, buffer, {"smcp": True, "liga": False})
        infos = buffer.glyph_infos
        mapped[codepoint] = font.getGlyphName(infos[0].codepoint) if len(infos) == 1 and infos[0].codepoint \
            else original
    for table in font["cmap"].tables:
        if table.isUnicode() and hasattr(table, "cmap"):
            table.cmap = {cp: mapped.get(cp, name) for cp, name in table.cmap.items()}


def derived_font(family: str, fonts_dir: Path = FONTS_DIR) -> Path:
    """The derived face for `family` (a DERIVED_FAMILIES key), built from its bundled source when the cache has no
    intact copy. Raises ValueError when the source's licence evidence does not hold."""
    filename, operation = DERIVED_FAMILIES[family]
    source = Path(fonts_dir) / filename
    valid, source_rights = font_rights(source)
    if not valid:
        raise ValueError(f"Derived font requires intact source rights: {filename}")
    identity = {"source_sha256": sha256_file(source), "operation": operation,
                "builder_sha256": sha256_file(Path(__file__)), "fonttools": fontTools.__version__,
                "harfbuzz": hb.__version__}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    directory = Path(fonts_dir).parent / "cache" / "font_instances" / key
    path = directory / (family.replace(" ", "") + ".ttf")
    if path.is_file() and font_rights(path)[0]:
        return path
    directory.mkdir(parents=True, exist_ok=True)
    with TTFont(source, recalcTimestamp=False) as font:
        if operation == "wght700":
            font = instantiateVariableFont(font, {"wght": 700}, inplace=True)
            font.recalcTimestamp = False
        else:
            _small_caps_cmap(font, source)
        _rename(font, family, operation == "wght700")
        data = io.BytesIO()
        font.save(data)
        _replace_file(path, data.getvalue())
    licence = Path(fonts_dir) / source_rights["license_file"]
    _replace_file(directory / "OFL.txt", licence.read_bytes())
    record = {**source_rights, "file": path.name, "sha256": sha256_file(path),
              "license_file": "OFL.txt", "derived_from": str(source.resolve()), **identity,
              "derivation": "real small-cap glyphs as default lowercase forms" if operation == "smcp"
                            else "static weight700 instance of the sole shipped variable source"}
    _replace_file(directory / "license_evidence.json",
                  (json.dumps({"fonts": [record]}, indent=2) + "\n").encode("utf-8"))
    return path


def ass_embedded_font(path: Path) -> str:
    """The ASS [Fonts] section embedding the face at `path` (the format's documented 6-bit encoding: three bytes
    to four characters from 33, 80 per line), so libass draws it with no font installed."""
    data = Path(path).read_bytes()
    encoded = []
    for at in range(0, len(data), 3):
        chunk = data[at:at + 3]
        value = int.from_bytes(chunk.ljust(3, b"\0"), "big")
        encoded.extend(chr(((value >> shift) & 63) + 33) for shift in (18, 12, 6, 0)[:len(chunk) + 1])
    text = "".join(encoded)
    return "[Fonts]\nfontname: " + Path(path).name + "\n" + "\n".join(
        text[at:at + 80] for at in range(0, len(text), 80)) + "\n"
