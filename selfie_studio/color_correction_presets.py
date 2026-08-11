from __future__ import annotations

import json
import os
from pathlib import Path


def load_color_correction_presets(path):
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    items = data.get("presets", []) if isinstance(data, dict) else []
    presets = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            presets.append({
                "name": name,
                "contrast": float(item["contrast"]),
                "saturation": float(item["saturation"]),
                "brightness": float(item["brightness"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return presets


def save_color_correction_presets(path, presets):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "presets": list(presets),
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)
