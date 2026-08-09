from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

APP_NAME = "Selfie AI Studio"
LOCAL_STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
BOOTSTRAP_PATH = LOCAL_STATE_DIR / "bootstrap.json"
LOCAL_CONFIG_PATH = LOCAL_STATE_DIR / "config.json"
LOCAL_FAVORITES_PATH = LOCAL_STATE_DIR / "lora_favorites.json"

_runtime_root: Path | None = None


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _default_root() -> Path:
    return Path.home() / "Documents" / APP_NAME


def _pick_root(parent=None, initialdir=None) -> Path | None:
    chosen = filedialog.askdirectory(
        parent=parent,
        title="Selfie AI Studio 共通データ保存先を選択（NAS推奨）",
        initialdir=str(initialdir or Path.home()),
    )
    if not chosen:
        return None
    return Path(chosen)


def _ensure_structure(root: Path) -> None:
    (root / "Data").mkdir(parents=True, exist_ok=True)
    (root / "Backups").mkdir(parents=True, exist_ok=True)
    (root / "Logs").mkdir(parents=True, exist_ok=True)


def _data_has_user_content(data_dir: Path) -> bool:
    for path in data_dir.glob("*.json"):
        data = _read_json(path, {})
        if isinstance(data, dict):
            if isinstance(data.get("items"), list) and data["items"]:
                return True
            if path.name == "workspace.json":
                if data.get("current_project_id") or data.get("current_character_id"):
                    return True
    return False


def _copy_legacy_data(src: Path, dst: Path) -> int:
    """Copy legacy JSON DBs only. Never delete or overwrite non-empty destination data."""
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        return 0

    copied = 0
    for source in src.glob("*.json"):
        target = dst / source.name

        # If target already has real user content, do not overwrite it.
        if target.exists():
            existing = _read_json(target, {})
            if isinstance(existing, dict):
                if isinstance(existing.get("items"), list) and existing["items"]:
                    continue
                if target.name == "workspace.json" and (
                    existing.get("current_project_id") or existing.get("current_character_id")
                ):
                    continue

        try:
            json.loads(source.read_text(encoding="utf-8"))
            shutil.copy2(source, target)
            copied += 1
        except Exception:
            continue
    return copied


def _create_backup(root: Path, version_from: str, version_to: str) -> Path | None:
    data_dir = root / "Data"
    if not data_dir.exists() or not any(data_dir.glob("*.json")):
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / "Backups" / f"StudioData_{version_from or 'unknown'}_to_{version_to}_{stamp}.zip"
    try:
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
            for p in data_dir.glob("*"):
                if p.is_file():
                    z.write(p, Path("Data") / p.name)
        return backup
    except Exception:
        return None


def ensure_persistence(parent=None, current_version="") -> Path:
    """Resolve the shared root. First run asks the user; later versions reuse it."""
    global _runtime_root
    LOCAL_STATE_DIR.mkdir(parents=True, exist_ok=True)

    bootstrap = _read_json(BOOTSTRAP_PATH, {})
    saved = str(bootstrap.get("data_root") or "").strip()
    root = Path(saved) if saved else None

    if root is None:
        messagebox.showinfo(
            "Selfie AI Studio 初期設定",
            "これからCharacter / Project / Prompt Libraryなどの共通データ保存先を設定します。\n\n"
            "NASを使う場合は、NAS上の『Selfie AI Studio』用フォルダを選んでください。\n"
            "この場所は今後のStudioバージョンでも共通で使用します。",
            parent=parent,
        )
        root = _pick_root(parent=parent)
        if root is None:
            root = _default_root()
            messagebox.showinfo(
                "保存先",
                f"選択がキャンセルされたため、今回は次の場所を使用します。\n\n{root}\n\n"
                "後から変更できます。",
                parent=parent,
            )

        bootstrap = {
            "data_root": str(root),
            "last_version": "",
        }
        _write_json(BOOTSTRAP_PATH, bootstrap)

    # Saved NAS path can temporarily disappear. Never silently switch storage.
    try:
        _ensure_structure(root)
    except Exception as exc:
        messagebox.showwarning(
            "共通データ保存先にアクセスできません",
            f"保存先へアクセスできません。\n\n{root}\n\n{exc}\n\n"
            "別の保存先を選択してください。",
            parent=parent,
        )
        alternate = _pick_root(parent=parent)
        if alternate is None:
            raise RuntimeError("共通データ保存先を利用できません。")
        root = alternate
        _ensure_structure(root)
        bootstrap["data_root"] = str(root)
        _write_json(BOOTSTRAP_PATH, bootstrap)

    last_version = str(bootstrap.get("last_version") or "")
    if current_version and last_version and last_version != current_version:
        _create_backup(root, last_version, current_version)

    # First shared-data setup: optionally import the old app-local studio_data.
    data_dir = root / "Data"
    migration_done = bool(bootstrap.get("legacy_migration_checked", False))
    if not migration_done and not _data_has_user_content(data_dir):
        if messagebox.askyesno(
            "旧版データの移行",
            "旧バージョンで保存したCharacter / Project / Prompt Libraryを移行しますか？\n\n"
            "「はい」を押した場合、旧版の studio_data フォルダを選択してください。\n"
            "元データは削除・移動せず、共通Dataへコピーだけ行います。",
            parent=parent,
        ):
            legacy = filedialog.askdirectory(
                parent=parent,
                title="旧版の studio_data フォルダを選択",
            )
            if legacy:
                copied = _copy_legacy_data(Path(legacy), data_dir)
                messagebox.showinfo(
                    "旧版データ移行",
                    f"{copied} 個のデータファイルを共通Dataへコピーしました。\n\n"
                    "旧版側のファイルは変更していません。",
                    parent=parent,
                )
        bootstrap["legacy_migration_checked"] = True

    if current_version:
        bootstrap["last_version"] = current_version
    bootstrap["data_root"] = str(root)
    _write_json(BOOTSTRAP_PATH, bootstrap)

    _runtime_root = root
    return root


def get_shared_root() -> Path:
    global _runtime_root
    if _runtime_root is not None:
        return _runtime_root

    bootstrap = _read_json(BOOTSTRAP_PATH, {})
    saved = str(bootstrap.get("data_root") or "").strip()
    if saved:
        _runtime_root = Path(saved)
        _ensure_structure(_runtime_root)
        return _runtime_root

    # Fallback for non-GUI calls. App normally calls ensure_persistence first.
    _runtime_root = _default_root()
    _ensure_structure(_runtime_root)
    return _runtime_root


def get_data_dir() -> Path:
    return get_shared_root() / "Data"


def get_backups_dir() -> Path:
    return get_shared_root() / "Backups"


def get_logs_dir() -> Path:
    return get_shared_root() / "Logs"
