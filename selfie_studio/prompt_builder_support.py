CATEGORIES = (
    ("outfit", "衣装"),
    ("background", "背景"),
    ("expression", "表情"),
    ("pose", "ポーズ"),
    ("camera", "カメラ"),
    ("quality", "品質タグ"),
)


DEFAULT_PARTS = {
    "outfit": [],
    "background": [],
    "expression": [],
    "pose": [],
    "camera": [],
    "quality": [],
}


def normalize_parts(data):
    result = {key: [] for key, _ in CATEGORIES}
    if not isinstance(data, dict):
        return result

    for key in result:
        values = data.get(key) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if not name or not prompt:
                continue
            result[key].append({
                "id": str(item.get("id") or ""),
                "name": name,
                "prompt": prompt,
                "favorite": bool(item.get("favorite", False)),
                "characters": [
                    str(x).strip()
                    for x in (item.get("characters") or [])
                    if str(x).strip()
                ],
            })
    return result


def build_prompt(base_prompt, selected_parts):
    pieces = []
    base = str(base_prompt or "").strip()
    if base:
        pieces.append(base)

    for part in selected_parts or []:
        text = str(part or "").strip(" ,")
        if text:
            pieces.append(text)

    return ", ".join(pieces)
