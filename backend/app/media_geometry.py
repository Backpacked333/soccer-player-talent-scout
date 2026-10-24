"""Map raw decoder pixels into a probed source's displayed orientation."""
import math
from fractions import Fraction

from app.schemas import DisplayGeometry


def display_geometry(stream: dict) -> DisplayGeometry | None:
    # FFmpeg's display matrix uses fixed-point columns (a,b,u,c,d,v,x,y,w).
    transforms: dict[tuple[float, ...], str] = {
        (1, 0, 0, 1): "identity",
        (0, -1, 1, 0): "rotate_90_ccw",
        (-1, 0, 0, -1): "rotate_180",
        (0, 1, -1, 0): "rotate_270_ccw",
        (-1, 0, 0, 1): "flip_x",
        (1, 0, 0, -1): "flip_y",
        (0, 1, 1, 0): "transpose",
        (0, -1, -1, 0): "transverse",
    }
    try:
        matrices = [item for item in stream.get("side_data_list", []) if item.get("side_data_type") == "Display Matrix"]
        if len(matrices) > 1:
            return None
        transform = "identity"
        if matrices:
            lines = matrices[0]["displaymatrix"].strip().splitlines()
            if len(lines) != 3:
                return None
            matrix = [int(value) for line in lines for value in line.split(":", 1)[1].split()]
            if len(matrix) != 9 or any(matrix[i] != 0 for i in (2, 5, 6, 7)) or matrix[8] != 1073741824:
                return None
            transform = transforms.get(tuple(matrix[i] / 65536 for i in (0, 1, 3, 4)), "unsupported")
        elif stream.get("tags", {}).get("rotate") is not None:
            # Legacy tags without a canonical display matrix are not enough to
            # establish the browser/decoder coordinate relationship safely.
            if float(stream["tags"]["rotate"]) % 360 != 0:
                return None
        if transform == "unsupported":
            return None
        ratio = stream.get("sample_aspect_ratio", "1:1")
        pixel_aspect = Fraction(1) if ratio in {"N/A", "0:1"} else Fraction(ratio.replace(":", "/"))
        aspect = int(stream["width"]) * float(pixel_aspect) / int(stream["height"])
        if not math.isfinite(aspect) or aspect <= 0:
            return None
        if transform in {"rotate_90_ccw", "rotate_270_ccw", "transpose", "transverse"}:
            aspect = 1 / aspect
        return DisplayGeometry.model_validate({"transform": transform, "aspect_ratio": aspect})
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, AttributeError):
        return None
