from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile


REQUIRED_TABLES = {
    "prompt_items",
    "categories",
    "prompt_categories",
    "aliases",
    "sources",
    "item_sources",
    "relations",
}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    # SQLite URI mode rejects Windows UNC authorities (for example
    # ``\\server\share``) on some Python/SQLite builds.  Studio commonly keeps
    # its shared Data on a NAS, so open the native path and enforce read-only
    # behavior at the connection level instead.
    con = sqlite3.connect(str(Path(path)))
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def validate_catalog(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    con = _connect_readonly(path)
    try:
        quick = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise ValueError(f"SQLite quick_check failed: {quick}")
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError(
                "Prompt Catalogに必要なテーブルがありません: "
                + ", ".join(sorted(missing))
            )
        count = int(con.execute("SELECT COUNT(*) FROM prompt_items").fetchone()[0])
        return {"prompt_items": count, "tables": sorted(tables)}
    finally:
        con.close()


def install_catalog(source: Path, target_dir: Path) -> dict:
    """Validate and atomically install a catalog selected by the user.

    ZIP input must contain exactly one file named prompt_db_v2.sqlite.
    Existing catalog bytes are backed up before replacement.
    """
    source = Path(source)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "prompt_db_v2.sqlite"

    with tempfile.TemporaryDirectory(prefix="selfie_prompt_catalog_") as tmp:
        tmp_dir = Path(tmp)
        candidate = tmp_dir / "prompt_db_v2.sqlite"
        if source.suffix.lower() == ".sqlite":
            shutil.copy2(source, candidate)
        elif source.suffix.lower() == ".zip":
            try:
                with ZipFile(source) as archive:
                    matches = [
                        name for name in archive.namelist()
                        if Path(name).name == "prompt_db_v2.sqlite"
                        and not name.endswith("/")
                    ]
                    if len(matches) != 1:
                        raise ValueError(
                            "ZIP内の prompt_db_v2.sqlite を1件に特定できません。"
                        )
                    with archive.open(matches[0]) as src, candidate.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            except BadZipFile as exc:
                raise ValueError("有効なZIPファイルではありません。") from exc
        else:
            raise ValueError(".zip または .sqlite を選択してください。")

        info = validate_catalog(candidate)
        backup = None
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = target_dir / f"prompt_db_v2_before_{stamp}.sqlite"
            shutil.copy2(target, backup)
        staged = target_dir / "prompt_db_v2.sqlite.new"
        shutil.copy2(candidate, staged)
        os.replace(staged, target)
        info.update({"path": str(target), "backup": str(backup) if backup else ""})
        return info


class PromptCatalog:
    def __init__(self, db_path: Path, user_path: Path):
        self.db_path = Path(db_path)
        self.user_path = Path(user_path)

    @property
    def available(self) -> bool:
        return self.db_path.is_file()

    def validate(self) -> dict:
        return validate_catalog(self.db_path)

    def _load_user(self) -> dict:
        default = {"schema_version": 1, "items": {}}
        if not self.user_path.is_file():
            return default
        try:
            data = json.loads(self.user_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            return default
        return data

    def _save_user(self, data: dict) -> None:
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.user_path.with_suffix(self.user_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.user_path)

    def toggle_favorite(self, item_id: str) -> bool:
        data = self._load_user()
        item = data["items"].setdefault(item_id, {})
        item["favorite"] = not bool(item.get("favorite", False))
        self._save_user(data)
        return item["favorite"]

    def record_use(self, item_id: str) -> None:
        data = self._load_user()
        item = data["items"].setdefault(item_id, {})
        item["use_count"] = int(item.get("use_count") or 0) + 1
        item["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        self._save_user(data)

    def filter_values(self) -> dict:
        if not self.available:
            return {"categories": [], "kinds": [], "adult_levels": [], "models": []}
        con = _connect_readonly(self.db_path)
        try:
            categories = [r[0] for r in con.execute("SELECT name_ja FROM categories ORDER BY name_ja")]
            kinds = [r[0] for r in con.execute("SELECT DISTINCT kind FROM prompt_items ORDER BY kind")]
            adult = [r[0] for r in con.execute("SELECT DISTINCT adult_level FROM prompt_items ORDER BY adult_level")]
            models = set()
            for row in con.execute("SELECT model_compatibility_json FROM prompt_items"):
                try:
                    models.update(json.loads(row[0] or "[]"))
                except (TypeError, ValueError):
                    pass
            return {"categories": categories, "kinds": kinds, "adult_levels": adult, "models": sorted(models)}
        finally:
            con.close()

    def search(
        self, query="", category="", kind="", adult_level="", model="",
        favorite_only=False, limit=500,
    ) -> list[dict]:
        if not self.available:
            return []
        clauses, params = [], []
        terms = [x for x in str(query).strip().split() if x]
        for term in terms:
            like = f"%{term}%"
            clauses.append("""(
                p.display_name_ja LIKE ? OR p.prompt_text LIKE ? OR
                EXISTS (SELECT 1 FROM aliases a WHERE a.prompt_item_id=p.id AND a.alias LIKE ?)
            )""")
            params.extend([like, like, like])
        if category:
            clauses.append("EXISTS (SELECT 1 FROM prompt_categories pc JOIN categories c ON c.id=pc.category_id WHERE pc.prompt_item_id=p.id AND c.name_ja=?)")
            params.append(category)
        if kind:
            clauses.append("p.kind=?")
            params.append(kind)
        if adult_level:
            clauses.append("p.adult_level=?")
            params.append(adult_level)
        if model:
            clauses.append("p.model_compatibility_json LIKE ?")
            params.append(f'%"{model}"%')
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = """
            SELECT p.id, p.display_name_ja, p.prompt_text, p.kind,
                   p.polarity, p.adult_level, p.model_compatibility_json,
                   p.notes,
                   GROUP_CONCAT(DISTINCT c.name_ja) AS categories
            FROM prompt_items p
            LEFT JOIN prompt_categories pc ON pc.prompt_item_id=p.id
            LEFT JOIN categories c ON c.id=pc.category_id
        """ + where + " GROUP BY p.id ORDER BY p.display_name_ja, p.prompt_text LIMIT ?"
        result_limit = max(1, min(int(limit), 2000))
        # Favorites live in the user overlay, not in the read-only catalog DB.
        # Scan the whole current catalog before applying that overlay filter.
        params.append(10000 if favorite_only else result_limit)
        user_items = self._load_user()["items"]
        con = _connect_readonly(self.db_path)
        try:
            results = []
            for row in con.execute(sql, params):
                item = dict(row)
                try:
                    item["models"] = json.loads(item.pop("model_compatibility_json") or "[]")
                except (TypeError, ValueError):
                    item["models"] = []
                overlay = user_items.get(item["id"], {})
                item["favorite"] = bool(overlay.get("favorite", False))
                item["use_count"] = int(overlay.get("use_count") or 0)
                item["last_used_at"] = overlay.get("last_used_at") or ""
                if not favorite_only or item["favorite"]:
                    results.append(item)
                    if len(results) >= result_limit:
                        break
            return results
        finally:
            con.close()

    def detail(self, item_id: str) -> dict | None:
        if not self.available:
            return None
        con = _connect_readonly(self.db_path)
        try:
            row = con.execute("SELECT * FROM prompt_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["categories"] = [r[0] for r in con.execute(
                "SELECT c.name_ja FROM categories c JOIN prompt_categories pc ON pc.category_id=c.id WHERE pc.prompt_item_id=? ORDER BY c.name_ja",
                (item_id,),
            )]
            item["aliases"] = [r[0] for r in con.execute(
                "SELECT alias FROM aliases WHERE prompt_item_id=? ORDER BY alias",
                (item_id,),
            )]
            item["relations"] = [dict(r) for r in con.execute(
                "SELECT relation_type, target_text FROM relations WHERE prompt_item_id=? ORDER BY relation_type, target_text",
                (item_id,),
            )]
            item["sources"] = [dict(r) for r in con.execute(
                "SELECT DISTINCT s.article_title, s.url FROM sources s JOIN item_sources i ON i.source_id=s.id WHERE i.prompt_item_id=? ORDER BY s.article_title",
                (item_id,),
            )]
            return item
        finally:
            con.close()
