from pathlib import Path
import re


def normalize_model_key(name):
    """Normalize checkpoint names for matching only."""
    value = str(name or "").strip().lower()
    value = value.replace("\\", "/").split("/")[-1]
    for ext in (".safetensors", ".ckpt", ".pt"):
        if value.endswith(ext):
            value = value[:-len(ext)]
    value = re.sub(r"\s*\[[^\]]+\]\s*$", "", value)
    return value.strip()


def preview_subsample_factor(width, height, max_width, max_height):
    """Integer Tk PhotoImage subsample factor that always fits the box."""
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    max_width = max(1, int(max_width or 1))
    max_height = max(1, int(max_height or 1))
    return max(
        1,
        (width + max_width - 1) // max_width,
        (height + max_height - 1) // max_height,
    )


def format_lora_list(loras, limit=None):
    """Return compact 'name:weight' text from dict/list LoRA data."""
    entries = []

    if isinstance(loras, dict):
        source = [
            {"name": name, "weight": weight}
            for name, weight in loras.items()
        ]
    elif isinstance(loras, list):
        source = loras
    else:
        source = []

    for entry in source:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        try:
            weight = float(entry.get("weight", 1.0))
            text = f"{name}:{weight:g}"
        except Exception:
            text = name
        entries.append(text)

    if limit is not None and limit >= 0 and len(entries) > limit:
        hidden = len(entries) - limit
        entries = entries[:limit] + [f"他{hidden}件"]

    return ", ".join(entries)


def extract_seed_from_info(info):
    """Extract a seed from Forge/A1111 info without network/API access."""
    import json

    if isinstance(info, dict):
        for key in ("seed", "Seed"):
            if key in info:
                return info.get(key)
        all_seeds = info.get("all_seeds")
        if isinstance(all_seeds, list) and all_seeds:
            return all_seeds[0]
        return ""

    if isinstance(info, str) and info.strip():
        try:
            parsed = json.loads(info)
            if isinstance(parsed, dict):
                return extract_seed_from_info(parsed)
        except Exception:
            pass

        match = re.search(r'"seed"\s*:\s*(-?\d+)', info)
        if match:
            return match.group(1)

    return ""


def image_name(record):
    """Best-effort display name for a History/Adopted record."""
    if not record:
        return "未設定"
    path = Path(record.get("image_path") or "")
    return record.get("name") or path.name or "未設定"
