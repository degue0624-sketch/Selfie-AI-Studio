from __future__ import annotations

from pathlib import Path


def _next_output_path(source: Path) -> Path:
    candidate = source.with_name(f"{source.stem}_color{source.suffix}")
    number = 2
    while candidate.exists():
        candidate = source.with_name(
            f"{source.stem}_color_{number}{source.suffix}"
        )
        number += 1
    return candidate


def save_color_corrected_copy(
    source_path: Path,
    *,
    contrast: float = 1.0,
    saturation: float = 1.0,
    brightness: float = 1.0,
) -> Path:
    """Save an RGB-only color correction beside source without altering it."""
    from PIL import Image, ImageEnhance
    from PIL.PngImagePlugin import PngInfo

    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    factors = (float(contrast), float(saturation), float(brightness))
    if any(value < 0 for value in factors):
        raise ValueError("色補正値は0以上で指定してください。")

    destination = _next_output_path(source)
    if destination == source or destination.exists():
        raise FileExistsError(destination)

    with Image.open(source) as original:
        original.load()
        has_alpha = "A" in original.getbands() or "transparency" in original.info
        rgba = original.convert("RGBA") if has_alpha else None
        alpha = rgba.getchannel("A") if rgba is not None else None
        rgb = (rgba or original).convert("RGB")

        corrected = ImageEnhance.Contrast(rgb).enhance(factors[0])
        corrected = ImageEnhance.Color(corrected).enhance(factors[1])
        corrected = ImageEnhance.Brightness(corrected).enhance(factors[2])

        if alpha is not None:
            corrected = corrected.convert("RGBA")
            corrected.putalpha(alpha)

        pnginfo = PngInfo()
        for key, value in original.info.items():
            if isinstance(value, str):
                pnginfo.add_text(str(key), value)

        corrected.save(destination, format="PNG", pnginfo=pnginfo)

    return destination
