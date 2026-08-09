from __future__ import annotations
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .schemas import (
    DEFAULT_DATABASES,
    CHARACTER_TEMPLATE,
    PROJECT_TEMPLATE,
    PRESET_TEMPLATE,
)
from .storage import JsonStore

class StudioRepository:
    """Repository for Selfie AI Studio structured data."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.stores = {
            name: JsonStore(self.data_dir / f"{name}.json", default)
            for name, default in DEFAULT_DATABASES.items()
        }

    def initialize(self) -> None:
        for store in self.stores.values():
            store.ensure()

    def db_paths(self) -> dict[str, Path]:
        return {name: store.path for name, store in self.stores.items()}

    def get_all(self, name: str) -> Any:
        return self.stores[name].load()

    def save_all(self, name: str, data: Any) -> None:
        self.stores[name].save(data)

    def list_items(self, name: str) -> list[dict]:
        data = self.get_all(name)
        items = data.get("items", [])
        return items if isinstance(items, list) else []

    def count(self, name: str) -> int:
        if name == "workspace":
            return 1
        return len(self.list_items(name))

    def delete_item(self, kind: str, item_id: str) -> bool:
        """Delete one item from a collection without touching external files."""
        data = self.get_all(kind)
        items = list(data.get("items") or [])
        kept = [x for x in items if str(x.get("id") or "") != str(item_id)]
        if len(kept) == len(items):
            return False
        data["items"] = kept
        self.save_all(kind, data)
        return True

    def get_item(self, name: str, item_id: str) -> dict | None:
        for item in self.list_items(name):
            if item.get("id") == item_id:
                return item
        return None

    def upsert_item(self, name: str, item: dict) -> dict:
        if name not in {"characters", "projects", "presets", "prompt_library", "history", "adopted"}:
            raise ValueError(f"Unsupported item DB: {name}")

        data = self.get_all(name)
        items = data.setdefault("items", [])
        new_item = deepcopy(item)
        if not new_item.get("id"):
            new_item["id"] = uuid.uuid4().hex

        for i, current in enumerate(items):
            if current.get("id") == new_item["id"]:
                items[i] = new_item
                self.save_all(name, data)
                return new_item

        items.append(new_item)
        self.save_all(name, data)
        return new_item

    def find_adopted_by_history(self, history_id: str) -> dict | None:
        if not history_id:
            return None
        for item in self.list_items("adopted"):
            if item.get("source_history_id") == history_id:
                return item
        return None

    def find_adopted_by_hash(self, image_hash: str) -> dict | None:
        if not image_hash:
            return None
        for item in self.list_items("adopted"):
            if item.get("image_hash") == image_hash:
                return item
        return None

    def adopt_history_item(self, history_id: str, *, adopted_name: str = "",
                           adopted_at: Any = None, extra: dict | None = None) -> tuple[dict, bool]:
        """Create/update an adopted record from History.

        Returns (record, created). Duplicate source History IDs or image hashes
        reuse the existing adopted record instead of creating another item.
        """
        history = self.get_item("history", history_id)
        if history is None:
            raise KeyError(f"History item not found: {history_id}")

        existing = self.find_adopted_by_history(history_id)
        if existing is None:
            existing = self.find_adopted_by_hash(str(history.get("image_hash") or ""))

        item = deepcopy(existing) if existing else {}
        item.update({
            "id": item.get("id", ""),
            "source_history_id": history_id,
            "image_path": history.get("image_path") or "",
            "json_path": history.get("json_path") or "",
            "image_hash": history.get("image_hash") or "",
            "project_id": history.get("project_id") or "",
            "character_id": history.get("character_id") or "",
            "name": adopted_name or item.get("name") or "",
            "status": "採用",
            "adopted_at": adopted_at if adopted_at is not None else item.get("adopted_at"),
            "created_at": history.get("created_at"),
            "tags": deepcopy(history.get("tags") or []),
            "notes": history.get("notes") or "",
            "favorite": bool(history.get("favorite", False)),
            "prompt": history.get("prompt") or "",
            "negative_prompt": history.get("negative_prompt") or "",
            "model": history.get("model") or "",
            "loras": deepcopy(history.get("loras") or []),
            "sampler": history.get("sampler") or "",
            "steps": history.get("steps", ""),
            "cfg": history.get("cfg", ""),
            "width": history.get("width", ""),
            "height": history.get("height", ""),
            "seed": history.get("seed", ""),
            "info": deepcopy(history.get("info")),
        })
        if extra:
            item.update(deepcopy(extra))

        saved = self.upsert_item("adopted", item)
        return saved, existing is None

    def sync_adopted_from_history(self, adopted_id: str) -> dict:
        """Refresh shared generation metadata while preserving adoption-only fields."""
        adopted = self.get_item("adopted", adopted_id)
        if adopted is None:
            raise KeyError(f"Adopted item not found: {adopted_id}")
        history_id = str(adopted.get("source_history_id") or "")
        history = self.get_item("history", history_id) if history_id else None
        if history is None:
            return adopted

        preserved = {
            "id": adopted.get("id", ""),
            "name": adopted.get("name", ""),
            "status": adopted.get("status", "採用"),
            "adopted_at": adopted.get("adopted_at"),
        }
        refreshed, _ = self.adopt_history_item(history_id, extra=preserved)
        return refreshed

    def new_character(self, name: str) -> dict:
        item = deepcopy(CHARACTER_TEMPLATE)
        item["id"] = uuid.uuid4().hex
        item["name"] = name
        item["display_name"] = name
        return item

    def new_project(self, name: str) -> dict:
        item = deepcopy(PROJECT_TEMPLATE)
        item["id"] = uuid.uuid4().hex
        item["name"] = name
        return item

    def new_preset(self, name: str, category: str = "general") -> dict:
        item = deepcopy(PRESET_TEMPLATE)
        item["id"] = uuid.uuid4().hex
        item["name"] = name
        item["category"] = category
        return item

    def workspace(self) -> dict:
        return self.get_all("workspace")

    def save_workspace(self, workspace: dict) -> None:
        self.save_all("workspace", workspace)
