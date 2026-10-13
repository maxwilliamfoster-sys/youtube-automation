"""
Size guard for every downloaded image, checked BEFORE it is decoded.

On 2026-10-10 a Wikimedia PNG that was only 14MB on disk decoded to 282 megapixels.
Pillow raised its decompression-bomb error inside the Ken Burns renderer and the
whole run was lost. Pillow's MAX_IMAGE_PIXELS limit is a real protection and stays
switched on; instead, oversized images are detected from the header (which reads
the dimensions without decoding the pixels) and skipped.
"""

import warnings

from PIL import Image

# A 1080x1920 frame zoomed 1.45x needs ~4.4MP. 40MP is generous for any real photo
# and far below Pillow's ~179MP bomb threshold.
MAX_SOURCE_PIXELS = 40_000_000


def oversized_reason(path: str, max_pixels: int = MAX_SOURCE_PIXELS) -> str:
    """'' if the image is safe to decode, otherwise a short reason to skip it."""
    try:
        with warnings.catch_warnings():
            # Pillow WARNS between 1x and 2x its limit and only raises above 2x.
            # Treat the warning as a refusal too.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # Own the file handle so it is closed even when Pillow raises mid-open.
            with open(path, "rb") as fh, Image.open(fh) as im:   # header only, no decode
                w, h = im.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        return f"decompression-bomb guard: {str(e)[:80]}"
    except Exception as e:
        return f"unreadable image: {str(e)[:80]}"
    if w * h > max_pixels:
        return f"{w}x{h} = {w * h / 1e6:.0f}MP (limit {max_pixels / 1e6:.0f}MP)"
    return ""
