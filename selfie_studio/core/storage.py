from __future__ import annotations
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

class JsonStoreError(RuntimeError):
    pass

class JsonStore:
    """Small JSON store with atomic writes and one-file backup.

    - Writes go to a temporary file in the same directory.
    - Existing file is copied to *.bak before replacement.
    - No automatic deletion of user data.
    """

    def __init__(self, path: Path, default_data: Any):
        self.path = Path(path)
        self.default_data = deepcopy(default_data)
        self.backup_path = self.path.with_suffix(self.path.suffix + ".bak")

    def ensure(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._atomic_write(self.default_data, make_backup=False)

    def load(self) -> Any:
        self.ensure()
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise JsonStoreError(f"JSON DBの読み込みに失敗しました: {self.path}\n{exc}") from exc

    def save(self, data: Any) -> None:
        self.ensure()
        self._atomic_write(data, make_backup=True)

    def _atomic_write(self, data: Any, make_backup: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)

        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent)
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())

            # Validate before replacing the current DB.
            json.loads(temp_path.read_text(encoding="utf-8"))

            if make_backup and self.path.exists():
                shutil.copy2(self.path, self.backup_path)

            os.replace(temp_path, self.path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def restore_backup(self) -> None:
        if not self.backup_path.exists():
            raise JsonStoreError(f"バックアップがありません: {self.backup_path}")
        # Validate backup before restore.
        json.loads(self.backup_path.read_text(encoding="utf-8"))
        shutil.copy2(self.backup_path, self.path)
