from __future__ import annotations
import json
from .persistence import LOCAL_FAVORITES_PATH

LORA_FAVORITES_PATH = LOCAL_FAVORITES_PATH

def load_lora_favorites() -> set[str]:
    if not LORA_FAVORITES_PATH.exists():
        return set()
    try:
        data = json.loads(LORA_FAVORITES_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(x) for x in data}
    except Exception:
        pass
    return set()

def save_lora_favorites(items: set[str]) -> None:
    LORA_FAVORITES_PATH.parent.mkdir(parents=True, exist_ok=True)
    LORA_FAVORITES_PATH.write_text(
        json.dumps(sorted(items), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
