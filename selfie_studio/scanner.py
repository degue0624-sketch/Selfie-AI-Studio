from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime

MODEL_EXTS = {".safetensors", ".ckpt"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

@dataclass(slots=True)
class Asset:
    path: Path
    size_bytes: int

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024**3)

def scan_assets(root: Path, extensions: set[str]) -> list[Asset]:
    if not root.exists():
        return []
    out = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in extensions:
                out.append(Asset(p, p.stat().st_size))
        except OSError:
            pass
    return sorted(out, key=lambda x: x.name.lower())

def count_files(root: Path, extensions: set[str]) -> int:
    return len(scan_assets(root, extensions))

def recent_images(root: Path, limit: int = 100) -> list[Path]:
    if not root.exists():
        return []
    items = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                items.append((p.stat().st_mtime, p))
        except OSError:
            pass
    items.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in items[:limit]]

def format_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return ""
