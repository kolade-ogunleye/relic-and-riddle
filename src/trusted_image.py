"""
src/trusted_image.py
====================
Decoding our own large rasters (the Natural Earth relief, a 4x upscale) past Pillow's decompression-bomb limit
without touching ``Image.MAX_IMAGE_PIXELS``. That limit is process-wide: setting it to None, even inside a
try/finally, left every Commons photo decoded on another thread in that window unchecked.
"""

from collections.abc import Iterable
from pathlib import Path

from PIL import Image, PngImagePlugin, TiffImagePlugin

# The formats our own large rasters come in; each is opened by its own plugin, not by Image.open (which applies the
# global limit).
_PLUGINS = {"PNG": PngImagePlugin.PngImageFile, "TIFF": TiffImagePlugin.TiffImageFile}


def open_trusted(path: Path, max_pixels: int, formats: Iterable[str] = ("PNG", "TIFF")) -> Image.Image:
    """The decoded image at ``path``, a file this pipeline made or checked itself, refused above ``max_pixels``
    (this call's own limit). The pixels are those Image.open + load would give."""
    last: Exception = Image.UnidentifiedImageError(f"cannot identify image file {str(path)!r}")
    for fmt in formats:
        try:
            im = _PLUGINS[fmt](str(path))
        except (SyntaxError, IndexError, TypeError) as e:  # not this format (Pillow plugins raise SyntaxError)
            last = Image.UnidentifiedImageError(f"{path}: not {fmt} ({e})")
            continue
        try:
            w, h = im.size
            if w * h > max_pixels:
                raise Image.DecompressionBombError(f"{path}: {w}x{h} ({w * h:,} px) exceeds {max_pixels:,} px")
            # The image memory is allocated here, under this call's limit, so the plugin's own allocation (TIFF's
            # checks the global limit) is skipped; the decoder then fills it as it would its own. A TIFF decodes at its
            # stored size (transposed later for an orientation tag), which Pillow keeps as _tile_size.
            im.im = Image.new(im.mode, getattr(im, "_tile_size", im.size)).im
            im.load()
        except BaseException:
            im.close()
            raise
        return im
    raise last
