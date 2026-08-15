from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from selfie_studio.prompt_catalog import PromptCatalog, install_catalog, validate_catalog


def create_catalog(path: Path, label="テストポーズ") -> None:
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE prompt_items (
        id TEXT PRIMARY KEY, display_name_ja TEXT, prompt_text TEXT, kind TEXT,
        polarity TEXT, adult_level TEXT, verification_status TEXT,
        model_compatibility_json TEXT, tags_json TEXT,
        raw_major_categories_json TEXT, raw_subcategories_json TEXT,
        raw_collection_categories_json TEXT, first_seen_at TEXT,
        last_seen_at TEXT, content_hash TEXT, notes TEXT
    );
    CREATE TABLE categories (id TEXT PRIMARY KEY, name_ja TEXT, parent_id TEXT);
    CREATE TABLE prompt_categories (prompt_item_id TEXT, category_id TEXT);
    CREATE TABLE aliases (id TEXT PRIMARY KEY, prompt_item_id TEXT, alias TEXT, alias_type TEXT);
    CREATE TABLE sources (id TEXT PRIMARY KEY, article_title TEXT, url TEXT);
    CREATE TABLE item_sources (prompt_item_id TEXT, source_id TEXT, source_row INTEGER);
    CREATE TABLE relations (id TEXT PRIMARY KEY, prompt_item_id TEXT, relation_type TEXT, target_text TEXT);
    """)
    con.execute(
        "INSERT INTO prompt_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "p1", label, "standing_pose", "atomic", "positive", "general",
            "external_unverified", json.dumps(["Stable Diffusion"]), "[]",
            "[]", "[]", "[]", None, None, "hash", "",
        ),
    )
    con.execute("INSERT INTO categories VALUES ('pose','ポーズ・動作',NULL)")
    con.execute("INSERT INTO prompt_categories VALUES ('p1','pose')")
    con.execute("INSERT INTO aliases VALUES ('a1','p1','standing pose','prompt_variant')")
    con.execute("INSERT INTO sources VALUES ('s1','出典記事','https://example.test')")
    con.execute("INSERT INTO item_sources VALUES ('p1','s1',1)")
    con.execute("INSERT INTO relations VALUES ('r1','p1','synergy','full body')")
    con.commit()
    con.close()


class PromptCatalogTests(unittest.TestCase):
    def test_search_detail_and_user_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "prompt_db_v2.sqlite"
            user = root / "prompt_catalog_user.json"
            create_catalog(db)
            self.assertEqual(validate_catalog(db)["prompt_items"], 1)
            catalog = PromptCatalog(db, user)
            con = sqlite3.connect(db)
            before = con.execute("SELECT COUNT(*) FROM prompt_items").fetchone()[0]
            con.close()
            rows = catalog.search(
                query="standing pose", category="ポーズ・動作",
                model="Stable Diffusion",
            )
            self.assertEqual([x["id"] for x in rows], ["p1"])
            detail = catalog.detail("p1")
            self.assertEqual(detail["relations"][0]["target_text"], "full body")
            self.assertEqual(detail["sources"][0]["article_title"], "出典記事")
            self.assertTrue(catalog.toggle_favorite("p1"))
            catalog.record_use("p1")
            favorite = catalog.search(favorite_only=True)
            self.assertEqual(favorite[0]["use_count"], 1)
            self.assertTrue(user.is_file())
            con = sqlite3.connect(db)
            after = con.execute("SELECT COUNT(*) FROM prompt_items").fetchone()[0]
            con.close()
            self.assertEqual(before, after)

    def test_zip_install_and_existing_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.sqlite"
            second = root / "second.sqlite"
            create_catalog(first, "旧版")
            create_catalog(second, "新版")
            package = root / "catalog.zip"
            with ZipFile(package, "w") as archive:
                archive.write(second, "folder/prompt_db_v2.sqlite")
            target_dir = root / "Data" / "PromptCatalog"
            initial = install_catalog(first, target_dir)
            self.assertEqual(initial["backup"], "")
            updated = install_catalog(package, target_dir)
            self.assertTrue(Path(updated["backup"]).is_file())
            catalog = PromptCatalog(
                target_dir / "prompt_db_v2.sqlite", root / "Data" / "user.json"
            )
            self.assertEqual(catalog.search()[0]["display_name_ja"], "新版")


if __name__ == "__main__":
    unittest.main()
