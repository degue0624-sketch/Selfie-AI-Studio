from __future__ import annotations

SCHEMA_VERSION = 1

DEFAULT_DATABASES = {
    "characters": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "projects": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "presets": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "prompt_library": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "adopted": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "history": {
        "schema_version": SCHEMA_VERSION,
        "items": []
    },
    "workspace": {
        "schema_version": SCHEMA_VERSION,
        "active_project_id": None,
        "active_character_id": None,
        "recent_project_ids": [],
        "ui": {}
    }
}

CHARACTER_TEMPLATE = {
    "id": "",
    "name": "",
    "display_name": "",
    "enabled": True,
    "base_prompt": "",
    "negative_prompt": "",
    "model": "",
    "vae": "",
    "sampler": "",
    "steps": 28,
    "cfg": 6.0,
    "width": 1024,
    "height": 1024,
    "loras": [],
    "notes": "",
    "tags": [],
    "master_refs": []
}

PROJECT_TEMPLATE = {
    "id": "",
    "name": "",
    "character_id": None,
    "status": "active",
    "description": "",
    "output_dir": "",
    "preset_ids": [],
    "notes": "",
    "tags": []
}

PRESET_TEMPLATE = {
    "id": "",
    "name": "",
    "category": "general",
    "prompt_add": "",
    "negative_add": "",
    "model": "",
    "vae": "",
    "sampler": "",
    "steps": None,
    "cfg": None,
    "width": None,
    "height": None,
    "loras": [],
    "notes": "",
    "tags": []
}
