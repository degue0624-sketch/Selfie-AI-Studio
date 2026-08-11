from __future__ import annotations
import base64

import struct
import zlib
import json
import hashlib
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from selfie_studio.studio_helpers import (
    normalize_model_key,
    preview_subsample_factor,
    format_lora_list,
    extract_seed_from_info,
    image_name,
)
from selfie_studio.generate_support import (
    clamp_queue_count,
    build_generation_payload,
    production_check_summary,
    split_missing_checks,
    compact_lora_text,
)
from selfie_studio.prompt_builder_support import (
    CATEGORIES as PROMPT_BUILDER_CATEGORIES,
    DEFAULT_PARTS as PROMPT_BUILDER_DEFAULT_PARTS,
    normalize_parts as normalize_prompt_builder_parts,
    build_prompt as build_prompt_from_parts,
)
from selfie_studio.prompt_assistant_support import (
    analyze_instruction as analyze_prompt_instruction,
)
from selfie_studio.ai_api_config import (
    load_api_config,
    save_api_config,
    masked_key,
    effective_api_key,
)
from selfie_studio.openai_api_client import (
    OpenAIApiError,
    test_connection as test_openai_connection,
    analyze_prompt as analyze_prompt_with_openai,
    analyze_image_review as analyze_image_review_with_openai,
)
from selfie_studio.image_review_support import (
    REVIEW_FIELDS,
    REVIEW_VALUES,
    normalize_review,
)
from selfie_studio.release_check import (
    check_path,
    check_callable,
    check_attr,
    summarize_checks,
)
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog

from .forge_api import ForgeApi, ForgeApiError
from .scanner import MODEL_EXTS, count_files, recent_images, scan_assets, format_mtime
from .settings import Settings, load_settings, save_settings
from .favorites import load_lora_favorites, save_lora_favorites
from .core_manager import get_repository
from .persistence import ensure_persistence, get_shared_root
from .ui_common import make_list_detail_pane, make_detail_box
from .themes import DEFAULT_THEME, get_theme, theme_names
from .ui_fonts import (
    DEFAULT_UI_FONT,
    UI_FONT_BODY,
    UI_FONT_EMPHASIS,
    UI_FONT_NORMAL,
    UI_FONT_SECTION,
    UI_FONT_SMALL,
    UI_FONT_TAB,
    configure_named_fonts,
    ui_font_choices,
)
import os
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
import tempfile
import shutil

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Selfie AI Studio v1.2.3")
        self.geometry("1200x800")
        self.minsize(980, 650)

        # Resolve version-independent shared storage before loading any user DB.
        self.shared_root = ensure_persistence(self, current_version="1.2.3")

        self.settings = load_settings()
        self.ui_theme_name = tk.StringVar(
            value=self.settings.ui_theme
            if self.settings.ui_theme in theme_names()
            else DEFAULT_THEME
        )
        self.ui_font_name = tk.StringVar(
            value=self.settings.ui_font
            if self.settings.ui_font in ui_font_choices()
            else DEFAULT_UI_FONT
        )
        self._apply_ui_theme()
        self.local_models = []
        self.nas_models = []
        self.current_preview = None
        self._studio_records = []
        self.lora_items = []
        self.lora_favorites = load_lora_favorites()
        self.active_loras = {}  # {lora_stem: weight}
        self.active_project_id = ""
        self.active_character_id = ""
        self.repo = get_repository()

        # Restore current workspace pointers for stable History linkage.
        try:
            _ws = self.repo.workspace()
            self.active_project_id = (
                _ws.get("current_project_id")
                or _ws.get("active_project_id")
                or ""
            )
            self.active_character_id = (
                _ws.get("current_character_id")
                or _ws.get("active_character_id")
                or ""
            )
        except Exception:
            pass
        self._build()
        self._load_settings()
        self.after(300, self.run_diagnostics)

    def _build(self):
        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="Selfie AI Studio", font=("", 18, "bold")).pack(side="left")
        self.connection_var = tk.StringVar(value="Forge: 未確認")
        ttk.Label(header, textvariable=self.connection_var).pack(side="right")
        self.ui_theme_combo = ttk.Combobox(
            header,
            textvariable=self.ui_theme_name,
            values=theme_names(),
            state="readonly",
            width=12,
        )
        self.ui_theme_combo.pack(side="right", padx=(6, 18))
        self.ui_theme_combo.bind("<<ComboboxSelected>>", self._ui_theme_selected)
        ttk.Label(header, text="UI Theme").pack(side="right")

        tabs = ttk.Notebook(self)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.home = ttk.Frame(tabs, padding=12)
        self.models = ttk.Frame(tabs, padding=12)
        self.lora = ttk.Frame(tabs, padding=12)
        self.prompt_library = ttk.Frame(tabs, padding=12)
        self.prompt_builder = ttk.Frame(tabs, padding=12)
        self.ai_assistant = ttk.Frame(tabs, padding=12)
        self.image_review = ttk.Frame(tabs, padding=12)
        self.character = ttk.Frame(tabs, padding=12)
        self.project = ttk.Frame(tabs, padding=12)
        self.generate = ttk.Frame(tabs, padding=12)
        self.history = ttk.Frame(tabs, padding=12)
        self.studio_history = ttk.Frame(tabs, padding=12)
        self.adopted_tab = ttk.Frame(tabs, padding=12)
        self.core_tab = ttk.Frame(tabs, padding=12)
        self.settings_tab = ttk.Frame(tabs, padding=12)
        tabs.add(self.home, text="ホーム")
        tabs.add(self.models, text="モデル")
        tabs.add(self.lora, text="LoRA")
        tabs.add(self.prompt_library, text="Prompt Library")
        tabs.add(self.prompt_builder, text="Prompt Builder")
        tabs.add(self.ai_assistant, text="AI Assistant")
        tabs.add(self.image_review, text="画像解析")
        tabs.add(self.character, text="Character")
        tabs.add(self.project, text="Project")
        tabs.add(self.generate, text="生成")
        tabs.add(self.history, text="最近の生成")
        tabs.add(self.studio_history, text="History")
        tabs.add(self.adopted_tab, text="採用DB")
        tabs.add(self.core_tab, text="Core")
        tabs.add(self.settings_tab, text="設定")

        self._build_home()
        self._build_models()
        self._build_lora()
        self._build_prompt_library()
        self._build_prompt_builder()
        self._build_ai_assistant()
        self._build_image_review()
        self._build_character()
        self._build_project()
        self._build_generate()
        self._build_history()
        self._build_studio_history()
        self._build_adopted()
        self._build_core()
        self._build_settings()

        self.status = tk.StringVar(value=f"準備完了 / 共通Data: {self.shared_root / 'Data'}")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=12, pady=(0, 8))
        self._apply_theme_to_tk_widgets(self)

    def _apply_ui_theme(self):
        colors = get_theme(self.ui_theme_name.get())
        self._theme_colors = colors
        self.configure(background=colors["background"])
        self._ui_font_available = configure_named_fonts(
            self, self.ui_font_name.get()
        )
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=colors["background"], foreground=colors["text"], font=UI_FONT_NORMAL)
        style.configure("TFrame", background=colors["background"])
        style.configure("Panel.TFrame", background=colors["panel"])
        style.configure("TLabel", background=colors["background"], foreground=colors["text"])
        style.configure("Muted.TLabel", foreground=colors["text_muted"], font=UI_FONT_SMALL)
        style.configure("Important.TLabel", foreground=colors["text"], font=UI_FONT_SECTION, padding=(2, 4))
        style.configure("TLabelframe", background=colors["panel"], bordercolor=colors["border"], relief="solid")
        style.configure("TLabelframe.Label", background=colors["panel"], foreground=colors["accent"], font=UI_FONT_SECTION, padding=(3, 5))
        style.configure("Important.TLabelframe", background=colors["panel"], bordercolor=colors["accent"], borderwidth=1, relief="solid")
        style.configure("Important.TLabelframe.Label", background=colors["panel"], foreground=colors["text"], font=UI_FONT_SECTION, padding=(5, 6))
        style.configure("TButton", background=colors["panel_alt"], foreground=colors["text"], bordercolor=colors["border"], padding=(10, 8), font=UI_FONT_EMPHASIS)
        style.map("TButton", background=[("active", colors["accent_hover"]), ("pressed", colors["selection"])])
        style.configure("Primary.TButton", background=colors["primary"], foreground=colors["background"], bordercolor=colors["primary"], padding=(18, 10), font=UI_FONT_EMPHASIS)
        style.map("Primary.TButton", background=[("active", colors["primary_hover"]), ("pressed", colors["primary_hover"])])
        style.configure("Accent.TButton", background=colors["accent_hover"], foreground=colors["text"], bordercolor=colors["accent"], padding=(10, 8), font=UI_FONT_NORMAL)
        style.map("Accent.TButton", background=[("active", colors["accent"]), ("pressed", colors["selection"])])
        style.configure("Success.TButton", background=colors["success"], foreground=colors["background"], bordercolor=colors["success"], padding=(10, 8), font=UI_FONT_EMPHASIS)
        style.map("Success.TButton", background=[("active", colors["primary_hover"]), ("pressed", colors["success"])])
        style.configure("Danger.TButton", background=colors["danger"], foreground=colors["text"], bordercolor=colors["danger"], font=UI_FONT_EMPHASIS)
        style.map("Danger.TButton", background=[("active", colors["primary_hover"])])
        for widget_style in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(widget_style, fieldbackground=colors["input"], background=colors["input"], foreground=colors["text"], bordercolor=colors["border"], arrowcolor=colors["text_muted"], font=UI_FONT_NORMAL, padding=5)
            style.map(widget_style, fieldbackground=[("readonly", colors["input"])], foreground=[("readonly", colors["text"])], selectbackground=[("readonly", colors["selection"])], bordercolor=[("focus", colors["accent"]), ("!focus", colors["border"])])
        style.configure("TCheckbutton", background=colors["background"], foreground=colors["text"])
        style.map("TCheckbutton", background=[("active", colors["background"])])
        style.configure("TNotebook", background=colors["background"], bordercolor=colors["border"])
        style.configure("TNotebook.Tab", background=colors["panel"], foreground=colors["text"], padding=(14, 9), font=UI_FONT_TAB)
        style.map("TNotebook.Tab", background=[("selected", colors["selection"]), ("active", colors["panel_alt"])], foreground=[("selected", colors["text"])])
        style.configure("Treeview", background=colors["panel"], fieldbackground=colors["panel"], foreground=colors["text"], bordercolor=colors["border"], font=UI_FONT_NORMAL, rowheight=29)
        style.map("Treeview", background=[("selected", colors["selection"])], foreground=[("selected", colors["text"])])
        style.configure("Treeview.Heading", background=colors["panel_alt"], foreground=colors["text"], relief="flat", font=UI_FONT_EMPHASIS, padding=(6, 5))

    def _apply_theme_to_tk_widgets(self, widget):
        colors = self._theme_colors
        prompt_body_widgets = {
            candidate
            for candidate in (
                getattr(self, "prompt", None),
                getattr(self, "negative", None),
            )
            if candidate is not None
        }
        for child in widget.winfo_children():
            try:
                if isinstance(child, tk.Text):
                    text_font = (
                        UI_FONT_BODY
                        if child in prompt_body_widgets
                        else UI_FONT_NORMAL
                    )
                    child.configure(background=colors["input"], foreground=colors["text"], insertbackground=colors["text"], selectbackground=colors["selection"], relief="flat", highlightbackground=colors["border"], highlightcolor=colors["accent"], highlightthickness=1, font=text_font, spacing1=1, spacing3=1)
                elif isinstance(child, tk.Canvas):
                    child.configure(background=colors["background"], highlightbackground=colors["border"])
                elif isinstance(child, tk.Listbox):
                    child.configure(background=colors["panel"], foreground=colors["text"], selectbackground=colors["selection"], selectforeground=colors["text"])
            except tk.TclError:
                pass
            self._apply_theme_to_tk_widgets(child)

    def _ui_theme_selected(self, _event=None):
        self._apply_ui_theme()
        self._apply_theme_to_tk_widgets(self)
        self.settings.ui_theme = self.ui_theme_name.get()
        save_settings(self.settings)
        if hasattr(self, "status"):
            self.status.set(f"UI Themeを変更しました: {self.ui_theme_name.get()}")

    def _ui_font_selected(self, _event=None):
        selected = self.ui_font_name.get()
        self._ui_font_available = configure_named_fonts(self, selected)
        # Reconfigure ttk styles and direct Tk widgets against the updated
        # named fonts. Prompt body keeps its independent UI_FONT_BODY family.
        self._apply_ui_theme()
        self._apply_theme_to_tk_widgets(self)
        self.settings.ui_font = selected
        save_settings(self.settings)
        if hasattr(self, "ui_font_status_var"):
            self.ui_font_status_var.set(
                "即時反映しました"
                if self._ui_font_available
                else "フォント未検出: Current / Defaultで表示中"
            )
        if hasattr(self, "status"):
            suffix = "" if self._ui_font_available else "（Defaultへフォールバック）"
            self.status.set(f"UI Fontを変更しました: {selected}{suffix}")


    def _build_home(self):
        top = ttk.Frame(self.home)
        top.pack(fill="x")

        ttk.Label(top, text="ホーム", font=("", 16, "bold")).pack(side="left")
        ttk.Button(
            top, text="更新",
            command=self.refresh_home_dashboard
        ).pack(side="right")

        main = ttk.Panedwindow(self.home, orient="horizontal")
        main.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        recent_box = ttk.LabelFrame(left, text="最近のProject", padding=8)
        recent_box.pack(fill="both", expand=True)

        cols = ("last", "name", "character", "generated", "adopted", "state")
        self.home_project_tree = ttk.Treeview(
            recent_box, columns=cols, show="headings", selectmode="browse"
        )
        for col, title, width in [
            ("last", "最終作業", 155),
            ("name", "Project", 205),
            ("character", "Character", 145),
            ("generated", "生成", 65),
            ("adopted", "採用", 65),
            ("state", "状態", 85),
        ]:
            self.home_project_tree.heading(col, text=title)
            self.home_project_tree.column(col, width=width)

        self.home_project_tree.pack(fill="both", expand=True)
        self.home_project_tree.bind(
            "<Double-1>", lambda _e: self.open_home_selected_project()
        )

        action = ttk.Frame(recent_box)
        action.pack(fill="x", pady=(8, 0))
        ttk.Button(
            action, text="選択Projectを開く",
            command=self.open_home_selected_project
        ).pack(side="left")

        status_box = ttk.LabelFrame(right, text="Studio状態", padding=8)
        status_box.pack(fill="x")

        self.home_studio_status = tk.StringVar(value="")
        ttk.Label(
            status_box,
            textvariable=self.home_studio_status,
            justify="left",
            anchor="w"
        ).pack(fill="x")
        ttk.Label(
            status_box,
            text="※ Model / LoRA はローカル情報から集計",
            anchor="w"
        ).pack(fill="x", pady=(6, 0))

        session_box = ttk.LabelFrame(right, text="最後の制作セッション", padding=8)
        session_box.pack(fill="both", expand=True, pady=(10, 0))

        self.home_last_session = tk.StringVar(value="保存済みセッションなし")
        ttk.Label(
            session_box,
            textvariable=self.home_last_session,
            justify="left",
            anchor="nw"
        ).pack(fill="both", expand=True)

        ttk.Button(
            session_box, text="Generateへ移動",
            command=lambda: self.tabs.select(self.generate)
        ).pack(anchor="e", pady=(8, 0))

        recent_images = ttk.Panedwindow(self.home, orient="horizontal")
        recent_images.pack(fill="x", pady=(10, 0))

        generated_box = ttk.LabelFrame(recent_images, text="最近生成", padding=8)
        adopted_box = ttk.LabelFrame(recent_images, text="最近採用", padding=8)
        recent_images.add(generated_box, weight=1)
        recent_images.add(adopted_box, weight=1)

        self.home_recent_generated_frame = ttk.Frame(generated_box)
        self.home_recent_generated_frame.pack(fill="x")

        self.home_recent_adopted_frame = ttk.Frame(adopted_box)
        self.home_recent_adopted_frame.pack(fill="x")

        self._home_recent_image_refs = []

        self.refresh_home_dashboard()

    def refresh_home_dashboard(self):
        if not hasattr(self, "home_project_tree"):
            return

        # Recent Projects
        for iid in self.home_project_tree.get_children():
            self.home_project_tree.delete(iid)

        projects = self.repo.list_items("projects")
        projects = sorted(
            projects,
            key=lambda x: str(
                (x.get("session") or {}).get("saved_at")
                or x.get("last_opened")
                or x.get("created_at")
                or ""
            ),
            reverse=True,
        )

        for item in projects[:12]:
            iid = str(item.get("id") or "")
            if not iid:
                continue
            session = item.get("session") or {}
            last = (
                session.get("saved_at")
                or item.get("last_opened")
                or item.get("created_at")
                or ""
            )
            self.home_project_tree.insert(
                "", "end", iid=iid,
                values=(
                    str(last).replace("T", " "),
                    item.get("name") or "",
                    item.get("character_name") or "",
                    self._project_generation_count(iid),
                    self._project_adopted_count(iid),
                    item.get("state") or "進行中",
                )
            )

        # Studio status
        try:
            model_count = len(getattr(self, "model_items", []) or [])
        except Exception:
            model_count = 0
        try:
            lora_count = len(getattr(self, "lora_items", []) or [])
        except Exception:
            lora_count = 0

        # If catalogs are not loaded yet, refresh local metadata only.
        # No OpenAI/external AI API is used here.
        if model_count == 0:
            try:
                self.scan_local_models()
                model_count = len(getattr(self, "model_items", []) or [])
            except Exception:
                pass
        if lora_count == 0:
            try:
                self.scan_loras()
                lora_count = len(getattr(self, "lora_items", []) or [])
            except Exception:
                pass

        char_count = len(self.repo.list_items("characters"))
        project_count = len(self.repo.list_items("projects"))
        prompt_count = len(self.repo.list_items("prompt_library"))

        forge_state = "未確認"
        try:
            text = self.connection_var.get()
            if "接続中" in text:
                forge_state = "接続中"
            elif text:
                forge_state = text.replace("Forge:", "").strip()
        except Exception:
            pass

        self.home_studio_status.set(
            "\n".join([
                f"Forge: {forge_state}",
                f"Model: {model_count}",
                f"LoRA: {lora_count}",
                f"Character: {char_count}",
                f"Project: {project_count}",
                f"Prompt Library: {prompt_count}",
            ])
        )

        # Latest saved session
        latest = None
        for item in projects:
            session = item.get("session") or {}
            if session.get("saved_at"):
                latest = item
                break

        if latest:
            session = latest.get("session") or {}
            char_name = latest.get("character_name") or ""
            if not char_name:
                char_id = session.get("character_id") or ""
                if char_id:
                    c = self.repo.get_item("characters", char_id)
                    if c:
                        char_name = c.get("name") or ""

            prompt_name = session.get("prompt_library_name") or "未設定"
            if prompt_name == "未選択":
                prompt_name = "未設定"

            self.home_last_session.set(
                "\n".join([
                    f"Project: {latest.get('name') or ''}",
                    f"Character: {char_name or '未設定'}",
                    f"Prompt: {prompt_name}",
                    f"Model: {session.get('model') or '未設定'}",
                    f"保存: {(session.get('saved_at') or '').replace('T', ' ')}",
                ])
            )
        else:
            self.home_last_session.set("保存済みセッションなし")

        self._refresh_home_recent_images()

    def _clear_home_recent_frame(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    def _make_home_thumb(self, path, max_w=150, max_h=110):
        path = Path(path or "")
        if not path.exists():
            return None
        try:
            img = tk.PhotoImage(file=str(path))
            w, h = img.width(), img.height()
            factor = max(
                1,
                (w + max_w - 1) // max_w,
                (h + max_h - 1) // max_h
            )
            if factor > 1:
                img = img.subsample(factor, factor)
            return img
        except Exception:
            return None

    def _refresh_home_recent_images(self):
        if not hasattr(self, "home_recent_generated_frame"):
            return

        self._clear_home_recent_frame(self.home_recent_generated_frame)
        self._clear_home_recent_frame(self.home_recent_adopted_frame)
        self._home_recent_image_refs = []

        history = sorted(
            self.repo.list_items("history"),
            key=lambda x: str(x.get("created_at") or x.get("time") or ""),
            reverse=True,
        )[:4]

        adopted = sorted(
            self.repo.list_items("adopted"),
            key=lambda x: str(x.get("adopted_at") or x.get("created_at") or ""),
            reverse=True,
        )[:4]

        def build_cards(frame, items, target):
            if not items:
                ttk.Label(frame, text="なし").pack(anchor="w")
                return

            row = ttk.Frame(frame)
            row.pack(fill="x")

            for item in items:
                card = ttk.Frame(row, padding=4)
                card.pack(side="left", padx=(0, 8))

                path = Path(item.get("image_path") or "")
                img = self._make_home_thumb(path)
                if img is not None:
                    self._home_recent_image_refs.append(img)
                    label = ttk.Label(card, image=img, cursor="hand2")
                else:
                    label = ttk.Label(
                        card,
                        text=path.name or "画像なし",
                        width=20,
                        anchor="center",
                        cursor="hand2"
                    )
                label.pack()

                ttk.Label(
                    card,
                    text=(item.get("name") or path.name)[:22],
                    anchor="center"
                ).pack(fill="x", pady=(4, 0))

                if target == "history":
                    label.bind(
                        "<Button-1>",
                        lambda _e, iid=item.get("id"): self._open_home_history_item(iid)
                    )
                else:
                    label.bind(
                        "<Button-1>",
                        lambda _e, iid=item.get("id"): self._open_home_adopted_item(iid)
                    )

        build_cards(self.home_recent_generated_frame, history, "history")
        build_cards(self.home_recent_adopted_frame, adopted, "adopted")

    def _open_home_history_item(self, item_id):
        try:
            self.tabs.select(self.studio_history)
            self.refresh_studio_history()
            if item_id in self.studio_history_tree.get_children():
                self.studio_history_tree.selection_set(item_id)
                self.studio_history_tree.focus(item_id)
                self.studio_history_tree.see(item_id)
                self._show_history_detail()
        except Exception:
            pass

    def _open_home_adopted_item(self, item_id):
        try:
            self.tabs.select(self.adopted_tab)
            self.refresh_adopted()
            if item_id in self.adopted_tree.get_children():
                self.adopted_tree.selection_set(item_id)
                self.adopted_tree.focus(item_id)
                self.adopted_tree.see(item_id)
                self._show_adopted_detail()
        except Exception:
            pass

    def open_home_selected_project(self):
        sel = self.home_project_tree.selection()
        if not sel:
            messagebox.showinfo("ホーム", "開くProjectを選択してください。")
            return

        project_id = sel[0]
        if hasattr(self, "project_tree") and project_id in self.project_tree.get_children():
            self.project_tree.selection_set(project_id)
            self.project_tree.focus(project_id)
            self._project_show_selected()
            self.project_open_selected()
            return

        item = self.repo.get_item("projects", project_id)
        if not item:
            messagebox.showwarning("ホーム", "Projectが見つかりません。")
            return

        self.active_project_id = project_id
        try:
            ws = self.repo.workspace()
            ws["current_project_id"] = project_id
            ws["active_project_id"] = project_id
            self.repo.save_all("workspace", ws)
        except Exception:
            pass

        try:
            self.tabs.select(self.project)
            self.refresh_project_list()
            if project_id in self.project_tree.get_children():
                self.project_tree.selection_set(project_id)
                self.project_tree.focus(project_id)
                self._project_show_selected()
        except Exception:
            pass

    def _build_models(self):
        bar = ttk.Frame(self.models)
        bar.pack(fill="x")

        ttk.Label(bar, text="検索").pack(side="left")
        self.model_search = tk.StringVar()
        ttk.Entry(
            bar, textvariable=self.model_search, width=28
        ).pack(side="left", padx=(6, 10))

        self.model_fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar,
            text="お気に入りのみ",
            variable=self.model_fav_only,
            command=self._refresh_model_tree
        ).pack(side="left")

        self.model_recommended_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bar,
            text="推奨のみ",
            variable=self.model_recommended_only,
            command=self._refresh_model_tree
        ).pack(side="left", padx=(10, 0))

        ttk.Label(bar, text="並び替え").pack(side="left", padx=(12, 0))
        self.model_sort_mode = tk.StringVar(value="名前順")
        self.model_sort_combo = ttk.Combobox(
            bar,
            textvariable=self.model_sort_mode,
            state="readonly",
            width=14,
            values=("名前順", "使用回数順", "最終使用順", "お気に入り順"),
        )
        self.model_sort_combo.pack(side="left", padx=(6, 0))
        self.model_sort_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._refresh_model_tree()
        )

        ttk.Button(
            bar, text="ローカル再読込",
            command=self.scan_local_models
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            bar, text="NAS再読込",
            command=self.scan_nas_models
        ).pack(side="right")

        self.model_search.trace_add(
            "write", lambda *_: self._refresh_model_tree()
        )

        self._model_meta = self._load_model_meta()

        cols = (
            "fav", "recommended", "kind", "name",
            "tags", "usecase", "usage", "last_used", "size", "path"
        )
        self.model_tree = ttk.Treeview(
            self.models,
            columns=cols,
            show="headings",
            selectmode="browse"
        )
        for col, title, width in [
            ("fav", "★", 45),
            ("recommended", "推奨", 55),
            ("kind", "場所", 70),
            ("name", "モデル名", 220),
            ("tags", "タグ", 140),
            ("usecase", "用途", 120),
            ("usage", "使用回数", 75),
            ("last_used", "最終使用", 135),
            ("size", "容量", 85),
            ("path", "パス", 430),
        ]:
            self.model_tree.heading(col, text=title)
            self.model_tree.column(col, width=width)
        self.model_tree.pack(fill="both", expand=True, pady=10)
        self.model_tree.bind(
            "<<TreeviewSelect>>",
            self._show_selected_model_meta
        )

        meta_box = ttk.LabelFrame(
            self.models, text="選択モデルの管理情報", padding=8
        )
        meta_box.pack(fill="x", pady=(0, 8))

        self.model_meta_tags = tk.StringVar()
        self.model_meta_usage = tk.StringVar()
        self.model_meta_cfg = tk.StringVar()
        self.model_meta_sampler = tk.StringVar()
        self.model_meta_notes = tk.StringVar()

        row1 = ttk.Frame(meta_box)
        row1.pack(fill="x")
        ttk.Label(row1, text="タグ", width=8).pack(side="left")
        ttk.Entry(
            row1, textvariable=self.model_meta_tags
        ).pack(side="left", fill="x", expand=True, padx=(4, 10))
        ttk.Label(row1, text="用途", width=8).pack(side="left")
        ttk.Entry(
            row1, textvariable=self.model_meta_usage, width=24
        ).pack(side="left", padx=(4, 0))

        row2 = ttk.Frame(meta_box)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="推奨CFG", width=8).pack(side="left")
        ttk.Entry(
            row2, textvariable=self.model_meta_cfg, width=10
        ).pack(side="left", padx=(4, 10))
        ttk.Label(row2, text="Sampler", width=8).pack(side="left")
        ttk.Entry(
            row2, textvariable=self.model_meta_sampler
        ).pack(side="left", fill="x", expand=True, padx=(4, 10))
        ttk.Button(
            row2, text="管理情報を保存",
            command=self.save_selected_model_meta
        ).pack(side="right")

        row3 = ttk.Frame(meta_box)
        row3.pack(fill="x", pady=(6, 0))
        ttk.Label(row3, text="メモ", width=8).pack(side="left")
        ttk.Entry(
            row3, textvariable=self.model_meta_notes
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        controls = ttk.Frame(self.models)
        controls.pack(fill="x")
        ttk.Button(
            controls,
            text="お気に入り切替",
            command=self.toggle_selected_model_favorite
        ).pack(side="left")

        self.model_info = tk.StringVar(value="")
        ttk.Label(
            self.models, textvariable=self.model_info
        ).pack(anchor="w", pady=(8, 0))

        self.model_character_info = tk.StringVar(
            value="Character推奨: 未選択"
        )
        ttk.Label(
            self.models, textvariable=self.model_character_info
        ).pack(anchor="w", pady=(2, 0))

        info = ttk.Label(
            self.models,
            text="※ モデル本体の移動・コピーは行いません。管理情報のみローカルDataへ保存します。"
        )
        info.pack(anchor="w", pady=(4, 0))


    def _build_lora(self):
        top = ttk.Frame(self.lora)
        top.pack(fill="x")

        ttk.Label(top, text="検索").pack(side="left")
        self.lora_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.lora_search, width=34).pack(side="left", padx=(6, 10))

        self.lora_fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="お気に入りのみ",
            variable=self.lora_fav_only,
            command=self._refresh_lora_tree
        ).pack(side="left")

        self.lora_recent_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="最近使用のみ",
            variable=self.lora_recent_only,
            command=self._refresh_lora_tree
        ).pack(side="left", padx=(10, 0))

        self.lora_recommended_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="推奨のみ",
            variable=self.lora_recommended_only,
            command=self._refresh_lora_tree
        ).pack(side="left", padx=(10, 0))

        ttk.Label(top, text="並び替え").pack(side="left", padx=(12, 0))
        self.lora_sort_mode = tk.StringVar(value="名前順")
        self.lora_sort_combo = ttk.Combobox(
            top,
            textvariable=self.lora_sort_mode,
            state="readonly",
            width=14,
            values=(
                "名前順",
                "使用回数順",
                "最終使用順",
                "お気に入り順",
            ),
        )
        self.lora_sort_combo.pack(side="left", padx=(6, 0))
        self.lora_sort_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._refresh_lora_tree()
        )

        ttk.Button(top, text="再読込", command=self.scan_loras).pack(side="right")
        self.lora_search.trace_add("write", lambda *_: self._refresh_lora_tree())

        cols = ("active", "fav", "recommended", "name", "weight", "usage", "last_used", "size", "path")
        self.lora_tree = ttk.Treeview(
            self.lora, columns=cols, show="headings", selectmode="extended"
        )
        for col, title, width in [
            ("active", "ON", 50),
            ("fav", "★", 45),
            ("recommended", "推奨", 55),
            ("name", "LoRA名", 230),
            ("weight", "Weight", 75),
            ("usage", "使用回数", 75),
            ("last_used", "最終使用", 135),
            ("size", "容量", 85),
            ("path", "パス", 460),
        ]:
            self.lora_tree.heading(col, text=title)
            self.lora_tree.column(col, width=width)
        self.lora_tree.pack(fill="both", expand=True, pady=10)
        self.lora_tree.bind("<Double-1>", lambda _e: self.toggle_selected_loras_active())

        controls = ttk.Frame(self.lora)
        controls.pack(fill="x")
        ttk.Label(controls, text="Weight").pack(side="left")
        self.lora_weight = tk.DoubleVar(value=1.0)
        ttk.Entry(controls, textvariable=self.lora_weight, width=8).pack(side="left", padx=(6, 14))

        ttk.Button(
            controls, text="ON / OFF切替",
            command=self.toggle_selected_loras_active
        ).pack(side="left")

        ttk.Button(
            controls, text="Weightを適用",
            command=self.apply_weight_to_selected_loras
        ).pack(side="left", padx=6)

        ttk.Button(
            controls, text="お気に入り切替",
            command=self.toggle_selected_lora_favorites
        ).pack(side="left")

        ttk.Button(
            controls, text="推奨LoRAをON",
            command=self.activate_recommended_loras
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            controls, text="選択解除",
            command=lambda: self.lora_tree.selection_remove(self.lora_tree.selection())
        ).pack(side="left", padx=6)

        self._lora_usage = self._load_lora_usage()

        self.lora_info = tk.StringVar(value="")
        ttk.Label(self.lora, textvariable=self.lora_info).pack(anchor="w", pady=(8,0))

        self.lora_character_info = tk.StringVar(value="Character推奨: 未選択")
        ttk.Label(
            self.lora, textvariable=self.lora_character_info
        ).pack(anchor="w", pady=(2,0))

        ttk.Label(
            self.lora,
            text="※ LoRAはStudio側でON/OFF管理。Prompt欄にはLoRAタグを表示せず、生成直前に内部合成します。"
        ).pack(anchor="w", pady=(4,0))

    def _lora_usage_path(self):
        return Path(self.shared_root) / "Data" / "lora_usage.json"

    def _load_lora_usage(self):
        path = self._lora_usage_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_lora_usage(self):
        path = self._lora_usage_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(
                json.dumps(self._lora_usage, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            os.replace(temp, path)
        except Exception:
            pass

    def _record_active_lora_usage(self, loras_snapshot):
        if not loras_snapshot:
            return

        now = datetime.now().isoformat(timespec="seconds")
        changed = False

        for name, weight in loras_snapshot.items():
            key = str(name).strip().lower()
            if not key:
                continue
            current = self._lora_usage.get(key)
            if not isinstance(current, dict):
                current = {}
            current["name"] = name
            current["usage_count"] = int(current.get("usage_count") or 0) + 1
            current["last_used_at"] = now
            try:
                current["last_weight"] = float(weight)
            except Exception:
                current["last_weight"] = weight
            self._lora_usage[key] = current
            changed = True

        if changed:
            self._save_lora_usage()
            self.after(0, self._refresh_lora_tree)

    def scan_loras(self):
        root = Path(self.setting_vars["forge_root"].get().strip()) / "models" / "Lora"
        self.lora_items = scan_assets(root, {".safetensors", ".pt"})
        self._refresh_lora_tree()
        if hasattr(self, "char_tree"):
            self.refresh_character_list()
        self.status.set(f"LoRAを {len(self.lora_items)} 件読み込みました")

    def _current_character_for_lora(self):
        character_id = getattr(self, "active_character_id", "") or ""
        if not character_id:
            try:
                ws = self.repo.workspace()
                character_id = (
                    ws.get("current_character_id")
                    or ws.get("active_character_id")
                    or ""
                )
            except Exception:
                character_id = ""
        if not character_id:
            return None
        try:
            return self.repo.get_item("characters", character_id)
        except Exception:
            return None

    def _recommended_lora_map(self):
        character = self._current_character_for_lora()
        if not character:
            return {}, "未選択"

        result = {}
        for entry in character.get("loras") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            try:
                weight = float(entry.get("weight", 1.0))
            except Exception:
                weight = 1.0
            result[name.lower()] = {
                "name": name,
                "weight": weight,
            }
        return result, (character.get("name") or "名称なし")

    def activate_recommended_loras(self):
        recommended, character_name = self._recommended_lora_map()
        if not recommended:
            messagebox.showinfo(
                "LoRA",
                "現在のCharacterには推奨LoRAが登録されていません。"
            )
            return

        available = {
            a.path.stem.lower(): a.path.stem
            for a in getattr(self, "lora_items", [])
        }

        added = 0
        missing = []
        for key, entry in recommended.items():
            actual = available.get(key)
            if getattr(self, "lora_items", None) and not actual:
                missing.append(entry["name"])
                continue
            stem = actual or entry["name"]
            self.active_loras[stem] = entry["weight"]
            added += 1

        self._refresh_lora_tree()
        self._schedule_generate_dirty_check()

        msg = f"Character「{character_name}」の推奨LoRAを {added} 件ONにしました。"
        if missing:
            msg += " / 見つからないLoRA: " + ", ".join(missing)
        self.status.set(msg + " 生成は開始していません。")

    def _refresh_lora_tree(self):
        if not hasattr(self, "lora_tree"):
            return
        q = self.lora_search.get().lower().strip() if hasattr(self, "lora_search") else ""
        fav_only = self.lora_fav_only.get() if hasattr(self, "lora_fav_only") else False
        self.lora_tree.delete(*self.lora_tree.get_children())

        recent_only = (
            self.lora_recent_only.get()
            if hasattr(self, "lora_recent_only")
            else False
        )
        recommended_only = (
            self.lora_recommended_only.get()
            if hasattr(self, "lora_recommended_only")
            else False
        )
        recommended_map, recommended_character_name = self._recommended_lora_map()

        rows = []
        for idx, a in enumerate(self.lora_items):
            key = str(a.path)
            is_fav = key in self.lora_favorites
            stem = a.path.stem
            active = stem in self.active_loras

            if fav_only and not is_fav:
                continue
            if q and q not in a.name.lower() and q not in str(a.path).lower():
                continue

            usage = self._lora_usage.get(stem.lower(), {})
            if not isinstance(usage, dict):
                usage = {}

            usage_count = int(usage.get("usage_count") or 0)
            last_used = str(usage.get("last_used_at") or "")
            is_recommended = stem.lower() in recommended_map

            if recent_only and not last_used:
                continue
            if recommended_only and not is_recommended:
                continue

            rows.append({
                "idx": idx,
                "asset": a,
                "key": key,
                "stem": stem,
                "active": active,
                "favorite": is_fav,
                "recommended": is_recommended,
                "usage_count": usage_count,
                "last_used": last_used,
            })

        sort_mode = (
            self.lora_sort_mode.get()
            if hasattr(self, "lora_sort_mode")
            else "名前順"
        )

        if sort_mode == "使用回数順":
            rows.sort(
                key=lambda r: (
                    r["usage_count"],
                    r["last_used"],
                    r["asset"].name.lower(),
                ),
                reverse=True,
            )
        elif sort_mode == "最終使用順":
            rows.sort(
                key=lambda r: (
                    r["last_used"],
                    r["usage_count"],
                    r["asset"].name.lower(),
                ),
                reverse=True,
            )
        elif sort_mode == "お気に入り順":
            rows.sort(
                key=lambda r: (
                    r["favorite"],
                    r["recommended"],
                    r["usage_count"],
                    r["last_used"],
                    r["asset"].name.lower(),
                ),
                reverse=True,
            )
        else:
            rows.sort(key=lambda r: r["asset"].name.lower())

        for row in rows:
            a = row["asset"]
            stem = row["stem"]
            self.lora_tree.insert(
                "", "end", iid=str(row["idx"]),
                values=(
                    "●" if row["active"] else "",
                    "★" if row["favorite"] else "",
                    "●" if row["recommended"] else "",
                    a.name,
                    f"{self.active_loras[stem]:g}" if row["active"] else "",
                    row["usage_count"],
                    row["last_used"].replace("T", " "),
                    f"{a.size_gb:.3f} GB",
                    str(a.path)
                )
            )

        shown = len(rows)

        self.lora_info.set(
            f"表示 {shown} / 全 {len(self.lora_items)} 件 / "
            f"ON {len(self.active_loras)} 件 / 推奨 {len(recommended_map)} 件"
        )
        if hasattr(self, "lora_character_info"):
            self.lora_character_info.set(
                f"Character推奨: {recommended_character_name}"
                + (
                    " / " + ", ".join(
                        f"{v['name']}:{v['weight']:g}"
                        for v in recommended_map.values()
                    )
                    if recommended_map else ""
                )
            )
        if hasattr(self, "active_lora_summary"):
            if self.active_loras:
                summary = ", ".join(
                    f"{name}({weight:g})"
                    for name, weight in list(self.active_loras.items())[:4]
                )
                if len(self.active_loras) > 4:
                    summary += f" 他{len(self.active_loras)-4}件"
                self.active_lora_summary.set(summary)
            else:
                self.active_lora_summary.set("0件")

    def _selected_lora_assets(self):
        out = []
        for iid in self.lora_tree.selection():
            try:
                idx = int(iid)
                if 0 <= idx < len(self.lora_items):
                    out.append(self.lora_items[idx])
            except Exception:
                pass
        return out

    def toggle_selected_lora_favorites(self):
        items = self._selected_lora_assets()
        if not items:
            messagebox.showinfo("LoRA", "お気に入りを切り替えるLoRAを選択してください。")
            return
        for a in items:
            key = str(a.path)
            if key in self.lora_favorites:
                self.lora_favorites.remove(key)
            else:
                self.lora_favorites.add(key)
        save_lora_favorites(self.lora_favorites)
        self._refresh_lora_tree()
        self.status.set("LoRAのお気に入りを更新しました")

    def _clean_prompt_lora_tags(self, prompt_text: str) -> str:
        cleaned = re.sub(
            r'<lora:([^:>]+):([0-9]*\.?[0-9]+)>',
            '',
            prompt_text,
            flags=re.IGNORECASE
        )
        cleaned = re.sub(r'\s*,\s*,+', ', ', cleaned)
        cleaned = re.sub(r'^\s*,\s*|\s*,\s*$', '', cleaned)
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
        return cleaned.strip()

    def _import_loras_from_prompt(self, prompt_text: str) -> None:
        matches = re.findall(
            r'<lora:([^:>]+):([0-9]*\.?[0-9]+)>',
            prompt_text,
            flags=re.IGNORECASE
        )
        # same LoRA => last value wins
        for name, weight in matches:
            try:
                self.active_loras[name] = float(weight)
            except ValueError:
                pass

    def _sync_prompt_field_remove_loras(self) -> None:
        current = self.prompt.get("1.0", "end").strip()
        self._import_loras_from_prompt(current)
        cleaned = self._clean_prompt_lora_tags(current)
        if cleaned != current:
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", cleaned)

    def toggle_selected_loras_active(self):
        items = self._selected_lora_assets()
        if not items:
            messagebox.showinfo("LoRA", "ON/OFFを切り替えるLoRAを選択してください。")
            return

        self._sync_prompt_field_remove_loras()
        try:
            default_weight = float(self.lora_weight.get())
        except Exception:
            messagebox.showerror("LoRA", "Weightは数値で指定してください。")
            return

        for a in items:
            stem = a.path.stem
            if stem in self.active_loras:
                del self.active_loras[stem]
            else:
                self.active_loras[stem] = default_weight

        self._refresh_lora_tree()
        self.status.set(
            f"LoRA状態を更新しました。現在ON: {len(self.active_loras)} 件。生成は開始していません。"
        )

    def apply_weight_to_selected_loras(self):
        items = self._selected_lora_assets()
        if not items:
            messagebox.showinfo("LoRA", "Weightを変更するLoRAを選択してください。")
            return

        try:
            weight = float(self.lora_weight.get())
        except Exception:
            messagebox.showerror("LoRA", "Weightは数値で指定してください。")
            return

        self._sync_prompt_field_remove_loras()
        for a in items:
            self.active_loras[a.path.stem] = weight

        self._refresh_lora_tree()
        self.status.set(
            f"{len(items)} 件のLoRA Weightを {weight:g} に更新しました。生成は開始していません。"
        )

    def _compose_prompt_with_loras(self, base_prompt: str) -> str:
        base = self._clean_prompt_lora_tags(base_prompt)
        tags = [
            f"<lora:{name}:{weight:g}>"
            for name, weight in self.active_loras.items()
        ]
        if base and tags:
            return base.rstrip().rstrip(",") + ", " + ", ".join(tags)
        if tags:
            return ", ".join(tags)
        return base


    def _build_prompt_library(self):
        top = ttk.Frame(self.prompt_library)
        top.pack(fill="x")

        ttk.Label(top, text="検索").pack(side="left")
        self.pl_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.pl_search, width=30).pack(side="left", padx=(6, 12))

        ttk.Label(top, text="カテゴリ").pack(side="left")
        self.pl_category_filter = tk.StringVar(value="すべて")
        self.pl_category_combo = ttk.Combobox(top, textvariable=self.pl_category_filter, state="readonly", width=18)
        self.pl_category_combo["values"] = ("すべて",)
        self.pl_category_combo.pack(side="left", padx=(6, 12))
        self.pl_category_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_prompt_library())

        ttk.Label(top, text="タグ").pack(side="left")
        self.pl_tag_filter = tk.StringVar()
        self.pl_tag_filter_entry = ttk.Entry(
            top, textvariable=self.pl_tag_filter, width=22
        )
        self.pl_tag_filter_entry.pack(side="left", padx=(6, 6))
        ttk.Button(
            top, text="タグ解除",
            command=lambda: self.pl_tag_filter.set("")
        ).pack(side="left", padx=(0, 12))
        self.pl_tag_filter.trace_add(
            "write", lambda *_: self.refresh_prompt_library()
        )

        self.pl_fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="お気に入りのみ", variable=self.pl_fav_only,
                        command=self.refresh_prompt_library).pack(side="left")

        ttk.Label(top, text="並び替え").pack(side="left", padx=(12, 0))
        self.pl_sort_mode = tk.StringVar(value="名前順")
        self.pl_sort_combo = ttk.Combobox(
            top,
            textvariable=self.pl_sort_mode,
            state="readonly",
            width=16,
            values=(
                "名前順",
                "使用回数順",
                "最終使用順",
                "お気に入り順",
            ),
        )
        self.pl_sort_combo.pack(side="left", padx=(6, 12))
        self.pl_sort_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_prompt_library()
        )

        ttk.Button(top, text="PNGを開く", command=self.pl_import_png).pack(side="right", padx=(6, 0))
        ttk.Button(top, text="更新", command=self.refresh_prompt_library).pack(side="right")

        self.pl_search.trace_add("write", lambda *_: self.refresh_prompt_library())

        mid, left, right = make_list_detail_pane(
            self.prompt_library,
            left_weight=3,
            right_weight=2,
            pady=(10, 6),
        )

        cols = ("fav", "name", "category", "tags", "usage", "last_used")
        self.pl_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, title, width in [
            ("fav", "★", 40),
            ("name", "正式名称", 240),
            ("category", "カテゴリ", 120),
            ("tags", "タグ", 220),
            ("usage", "使用回数", 75),
            ("last_used", "最終使用", 145),
        ]:
            self.pl_tree.heading(col, text=title)
            self.pl_tree.column(col, width=width)
        self.pl_tree.pack(fill="both", expand=True)
        self.pl_tree.bind("<<TreeviewSelect>>", self._pl_show_selected)
        self.pl_tree.bind("<Double-1>", self._pl_double_click)

        form = make_detail_box(right, "登録 / 編集", padding=8)

        self.pl_name = tk.StringVar()
        self.pl_category = tk.StringVar(value="general")
        self.pl_tags = tk.StringVar()
        self.pl_recommended_model = tk.StringVar()
        self.pl_recommended_loras = tk.StringVar()

        ttk.Label(form, text="正式名称").pack(anchor="w")
        ttk.Entry(form, textvariable=self.pl_name).pack(fill="x", pady=(2, 8))
        ttk.Label(form, text="カテゴリ").pack(anchor="w")
        ttk.Entry(form, textvariable=self.pl_category).pack(fill="x", pady=(2, 8))
        ttk.Label(form, text="タグ（カンマ区切り）").pack(anchor="w")
        ttk.Entry(form, textvariable=self.pl_tags).pack(fill="x", pady=(2, 8))

        rec = ttk.LabelFrame(form, text="推奨設定", padding=6)
        rec.pack(fill="x", pady=(0, 8))

        ttk.Label(rec, text="Model", width=8).grid(row=0, column=0, sticky="w")
        self.pl_recommended_model_combo = ttk.Combobox(
            rec,
            textvariable=self.pl_recommended_model,
            state="readonly",
        )
        self.pl_recommended_model_combo.grid(
            row=0, column=1, sticky="ew", padx=(4, 6)
        )
        ttk.Button(
            rec,
            text="モデル更新",
            command=self.refresh_prompt_recommendation_choices
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(rec, text="LoRA", width=8).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(
            rec,
            textvariable=self.pl_recommended_loras,
            state="readonly"
        ).grid(row=1, column=1, sticky="ew", padx=(4, 6), pady=(6, 0))
        ttk.Button(
            rec,
            text="LoRA選択",
            command=self.open_prompt_lora_picker
        ).grid(row=1, column=2, sticky="e", pady=(6, 0))

        ttk.Button(
            rec,
            text="現在のGenerate設定を取得",
            command=self.capture_current_generate_recommendations
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))

        rec.columnconfigure(1, weight=1)

        ttk.Label(form, text="Prompt").pack(anchor="w")
        self.pl_prompt = tk.Text(form, height=8, wrap="word")
        self.pl_prompt.pack(fill="both", expand=True, pady=(2, 8))
        ttk.Label(form, text="Negative").pack(anchor="w")
        self.pl_negative = tk.Text(form, height=5, wrap="word")
        self.pl_negative.pack(fill="both", expand=True, pady=(2, 8))

        ttk.Label(form, text="PNG生成情報").pack(anchor="w")
        self.pl_meta_summary = tk.StringVar(value="未読込")
        ttk.Label(form, textvariable=self.pl_meta_summary, justify="left").pack(fill="x", pady=(2, 8))

        btns = ttk.Frame(form)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="新規", command=self.pl_clear_form).pack(side="left")
        ttk.Button(btns, text="保存", command=self.pl_save_current).pack(side="left", padx=6)
        ttk.Button(
            btns, text="正式名称を変更",
            command=self.pl_rename_selected
        ).pack(side="left")
        ttk.Button(btns, text="お気に入り切替", command=self.pl_toggle_favorite).pack(side="left", padx=(6, 0))
        ttk.Button(
            btns, text="生成タブへ追記",
            command=self.pl_append_selected_to_generate
        ).pack(side="right")
        ttk.Button(
            btns, text="推奨設定も適用",
            command=self.pl_load_selected_with_recommendations
        ).pack(side="right")
        ttk.Button(
            btns, text="生成タブへ読み込む",
            command=self.pl_load_selected_to_generate
        ).pack(side="right", padx=(0, 6))

        self.pl_status = tk.StringVar(value="")
        ttk.Label(self.prompt_library, textvariable=self.pl_status).pack(anchor="w")
        self._pl_selected_id = None
        self.refresh_prompt_library()
        self.refresh_prompt_recommendation_choices()


    def _pl_parse_parameters_text(self, raw):
        if not raw or not isinstance(raw, str):
            return {}
        result = {"prompt": "", "negative_prompt": "", "settings": {}, "loras": [], "raw": raw}
        neg_marker = "\nNegative prompt:"
        steps_marker = "\nSteps:"
        if neg_marker in raw:
            prompt_part, rest = raw.split(neg_marker, 1)
            result["prompt"] = prompt_part.strip()
            if steps_marker in rest:
                neg_part, settings_part = rest.split(steps_marker, 1)
                result["negative_prompt"] = neg_part.strip()
                settings_line = "Steps:" + settings_part.strip()
            else:
                result["negative_prompt"] = rest.strip(); settings_line = ""
        else:
            if steps_marker in raw:
                prompt_part, settings_part = raw.split(steps_marker, 1)
                result["prompt"] = prompt_part.strip(); settings_line = "Steps:" + settings_part.strip()
            else:
                result["prompt"] = raw.strip(); settings_line = ""
        lora_pattern = re.compile(r"<lora:([^:>]+):([^>]+)>", re.IGNORECASE)
        for name, weight in lora_pattern.findall(result["prompt"]):
            try: weight_value = float(weight)
            except Exception: weight_value = weight
            result["loras"].append({"name": name.strip(), "weight": weight_value})
        if settings_line:
            keys=["Steps","Sampler","Schedule type","CFG scale","Seed","Size","Model hash","Model","VAE","VAE hash","Denoising strength","Clip skip","Version"]
            kp='|'.join(re.escape(k) for k in sorted(keys,key=len,reverse=True))
            pat=re.compile(rf"(?:^|, )({kp}): (.*?)(?=(?:, (?:{kp}): )|$)")
            for key,value in pat.findall(settings_line): result["settings"][key]=value.strip()
        return result

    def _pl_collect_png_metadata(self, path):
        """Read PNG textual metadata using only Python stdlib. No API/network usage."""
        path = Path(path)
        if path.suffix.lower() != ".png":
            raise ValueError("現在のメタデータ抽出はPNGのみ対応です。")

        PNG_SIG = b"\x89PNG\r\n\x1a\n"
        with open(path, "rb") as f:
            if f.read(8) != PNG_SIG:
                raise ValueError("PNGファイルとして認識できません。")

            width = height = None
            text_keys = {}
            while True:
                length_bytes = f.read(4)
                if not length_bytes:
                    break
                if len(length_bytes) != 4:
                    raise ValueError("PNGチャンク長が不正です。")
                length = struct.unpack(">I", length_bytes)[0]
                chunk_type = f.read(4)
                data = f.read(length)
                crc = f.read(4)
                if len(chunk_type) != 4 or len(data) != length or len(crc) != 4:
                    raise ValueError("PNGチャンクが途中で切れています。")

                if chunk_type == b"IHDR" and length >= 8:
                    width, height = struct.unpack(">II", data[:8])
                elif chunk_type == b"tEXt" and b"\x00" in data:
                    key_b, val_b = data.split(b"\x00", 1)
                    text_keys[key_b.decode("latin-1", errors="replace")] = val_b.decode("utf-8", errors="replace")
                elif chunk_type == b"zTXt" and b"\x00" in data:
                    key_b, rest = data.split(b"\x00", 1)
                    if len(rest) >= 2 and rest[0] == 0:
                        try:
                            val_b = zlib.decompress(rest[1:])
                            text_keys[key_b.decode("latin-1", errors="replace")] = val_b.decode("utf-8", errors="replace")
                        except Exception:
                            pass
                elif chunk_type == b"iTXt":
                    try:
                        key_b, rest = data.split(b"\x00", 1)
                        comp_flag, comp_method = rest[0], rest[1]
                        rest = rest[2:]
                        _, rest = rest.split(b"\x00", 1)
                        _, text_b = rest.split(b"\x00", 1)
                        if comp_flag == 1 and comp_method == 0:
                            text_b = zlib.decompress(text_b)
                        text_keys[key_b.decode("utf-8", errors="replace")] = text_b.decode("utf-8", errors="replace")
                    except Exception:
                        pass
                if chunk_type == b"IEND":
                    break

        raw = text_keys.get("parameters") or text_keys.get("Parameters") or ""
        parsed = self._pl_parse_parameters_text(raw)
        other_text = {k: v[:5000] for k, v in text_keys.items() if k not in {"parameters", "Parameters"} and isinstance(v, str)}
        return {
            "path": str(path),
            "width": width,
            "height": height,
            "parameters_found": bool(raw),
            "parsed": parsed,
            "text_keys": other_text,
        }

    def pl_import_png(self):
        path=filedialog.askopenfilename(title="生成PNGを選択",filetypes=[("PNG画像","*.png"),("すべてのファイル","*.*")])
        if not path: return
        try: meta=self._pl_collect_png_metadata(path)
        except Exception as e:
            messagebox.showerror("PNG読込",f"画像を読み込めませんでした。\\n{e}"); return
        if not meta.get("parameters_found"):
            self.pl_meta_summary.set(f"{Path(path).name}\\n{meta.get('width')}×{meta.get('height')}\\n生成パラメータ: 見つかりません")
            self.pl_status.set("画像は読めましたが、Forge/A1111形式の生成情報は見つかりませんでした。")
            messagebox.showinfo("PNG読込","画像は読み込めましたが、Forge/A1111形式の生成情報がありません。\\nスクリーンショット・SNS保存・変換済み画像では情報が消えている場合があります。")
            return
        parsed=meta.get("parsed") or {}
        self.pl_clear_form()
        self.pl_name.set(Path(path).stem)
        self.pl_category.set("PNG取込")
        self.pl_tags.set("png_import")
        self.pl_prompt.delete("1.0","end"); self.pl_prompt.insert("1.0",parsed.get("prompt") or "")
        self.pl_negative.delete("1.0","end"); self.pl_negative.insert("1.0",parsed.get("negative_prompt") or "")
        st=parsed.get("settings") or {}; loras=parsed.get("loras") or []
        lines=[Path(path).name,f"{meta.get('width')}×{meta.get('height')}"]
        for key in ("Model","Sampler","Schedule type","Steps","CFG scale","Seed","Size","VAE"):
            if st.get(key): lines.append(f"{key}: {st[key]}")
        if loras: lines.append("LoRA: "+", ".join(f"{x.get('name')}({x.get('weight')})" for x in loras))
        self.pl_meta_summary.set("\\n".join(lines))
        self._pl_last_png_metadata=meta
        self.pl_status.set("PNG生成情報を読み込みました。内容を確認して『保存』を押すとPrompt Libraryへ登録されます。")

    def _pl_items(self):
        return self.repo.list_items("prompt_library")

    def refresh_prompt_library(self):
        if not hasattr(self, "pl_tree"):
            return
        items = self._pl_items()
        categories = sorted({
            (item.get("category") or "general").strip()
            for item in items
            if (item.get("category") or "").strip()
        })
        current_filter = self.pl_category_filter.get()
        values = ("すべて", *categories)
        self.pl_category_combo["values"] = values
        if current_filter not in values:
            self.pl_category_filter.set("すべて")

        q = self.pl_search.get().strip().lower()
        q_terms = [x for x in re.split(r"\s+", q) if x]
        cat = self.pl_category_filter.get()
        fav_only = self.pl_fav_only.get()
        tag_query = self.pl_tag_filter.get().strip()
        required_tags = [
            x.strip().lower()
            for x in tag_query.replace("、", ",").split(",")
            if x.strip()
        ]

        filtered = []
        for item in items:
            name = item.get("name") or ""
            category = item.get("category") or "general"
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in tags.split(",") if x.strip()]
            hay = " ".join([
                name,
                category,
                " ".join(tags),
                item.get("prompt") or "",
                item.get("negative_prompt") or "",
                item.get("recommended_model") or "",
                " ".join(
                    f"{x.get('name','')} {x.get('weight','')}"
                    for x in (item.get("recommended_loras") or [])
                    if isinstance(x, dict)
                ),
            ]).lower()
            if q_terms and not all(term in hay for term in q_terms):
                continue
            if cat != "すべて" and category != cat:
                continue
            normalized_tags = [str(x).strip().lower() for x in tags]
            if required_tags and not all(
                any(req in tag for tag in normalized_tags)
                for req in required_tags
            ):
                continue
            if fav_only and not item.get("favorite", False):
                continue
            filtered.append(item)

        sort_mode = (
            self.pl_sort_mode.get()
            if hasattr(self, "pl_sort_mode")
            else "名前順"
        )
        if sort_mode == "使用回数順":
            filtered = sorted(
                filtered,
                key=lambda x: (
                    int(x.get("usage_count") or 0),
                    str(x.get("last_used_at") or ""),
                    str(x.get("name") or "").lower(),
                ),
                reverse=True,
            )
        elif sort_mode == "最終使用順":
            filtered = sorted(
                filtered,
                key=lambda x: (
                    str(x.get("last_used_at") or ""),
                    int(x.get("usage_count") or 0),
                ),
                reverse=True,
            )
        elif sort_mode == "お気に入り順":
            filtered = sorted(
                filtered,
                key=lambda x: (
                    bool(x.get("favorite", False)),
                    int(x.get("usage_count") or 0),
                    str(x.get("last_used_at") or ""),
                    str(x.get("name") or "").lower(),
                ),
                reverse=True,
            )
        else:
            filtered = sorted(
                filtered,
                key=lambda x: str(x.get("name") or "").lower(),
            )

        self.pl_tree.delete(*self.pl_tree.get_children())
        for item in filtered:
            name = item.get("name") or ""
            category = item.get("category") or "general"
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in tags.split(",") if x.strip()]
            self.pl_tree.insert(
                "", "end", iid=item.get("id"),
                values=(
                    "★" if item.get("favorite", False) else "",
                    name,
                    category,
                    ", ".join(tags),
                    int(item.get("usage_count") or 0),
                    str(item.get("last_used_at") or "").replace("T", " "),
                )
            )

        self.pl_status.set(f"表示 {len(filtered)} / 全 {len(items)} 件")

    def _pl_double_click(self, event):
        region = self.pl_tree.identify("region", event.x, event.y)
        if region != "cell":
            return

        column = self.pl_tree.identify_column(event.x)
        item = self._pl_selected_item()
        if not item:
            return

        # #4 is the Tags column: use its tags as a quick filter.
        if column == "#4":
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in tags.split(",") if x.strip()]
            self.pl_tag_filter.set(", ".join(tags))
            self.pl_status.set("選択項目のタグで絞り込みました。")
            return

        self.pl_load_selected_to_generate()

    def _pl_selected_item(self):
        sel = self.pl_tree.selection()
        if not sel:
            return None
        return self.repo.get_item("prompt_library", sel[0])

    def _pl_show_selected(self, _event=None):
        item = self._pl_selected_item()
        if not item:
            return
        self._pl_selected_id = item.get("id")
        self.pl_name.set(item.get("name") or "")
        self.pl_category.set(item.get("category") or "general")
        tags = item.get("tags") or []
        if isinstance(tags, list):
            tags = ", ".join(tags)
        self.pl_tags.set(tags)
        if hasattr(self, "pl_recommended_model"):
            self.pl_recommended_model.set(item.get("recommended_model") or "")
        if hasattr(self, "pl_recommended_loras"):
            rec_loras = []
            for x in item.get("recommended_loras") or []:
                if not isinstance(x, dict) or not x.get("name"):
                    continue
                try:
                    weight = float(x.get("weight", 1.0))
                    rec_loras.append(f"{x['name']}:{weight:g}")
                except Exception:
                    rec_loras.append(str(x.get("name")))
            self.pl_recommended_loras.set(", ".join(rec_loras))
        self.pl_prompt.delete("1.0", "end")
        self.pl_prompt.insert("1.0", item.get("prompt") or "")
        self.pl_negative.delete("1.0", "end")
        self.pl_negative.insert("1.0", item.get("negative_prompt") or "")
        if hasattr(self, "pl_meta_summary"):
            self.pl_meta_summary.set("保存済みLibrary項目")

    def pl_clear_form(self):
        self._pl_selected_id = None
        self.pl_name.set("")
        self.pl_category.set("general")
        self.pl_tags.set("")
        if hasattr(self, "pl_recommended_model"):
            self.pl_recommended_model.set("")
        if hasattr(self, "pl_recommended_loras"):
            self.pl_recommended_loras.set("")
        self.pl_prompt.delete("1.0", "end")
        self.pl_negative.delete("1.0", "end")
        if hasattr(self, "pl_meta_summary"):
            self.pl_meta_summary.set("未読込")
        self.pl_tree.selection_remove(self.pl_tree.selection())
        self.pl_status.set("新規登録モード")

    def pl_save_current(self):
        name = self.pl_name.get().strip()
        if not name:
            messagebox.showinfo("Prompt Library", "正式名称を入力してください。")
            return

        for other in self.repo.list_items("prompt_library"):
            if (
                other.get("id") != self._pl_selected_id
                and (other.get("name") or "").strip() == name
            ):
                messagebox.showwarning(
                    "Prompt Library",
                    f"同じ正式名称「{name}」が既に存在します。\n"
                    "別の名称を使用してください。"
                )
                return

        recommended_loras = []
        for raw in self.pl_recommended_loras.get().split(","):
            raw = raw.strip()
            if not raw:
                continue
            if ":" in raw:
                lora_name, weight_text = raw.rsplit(":", 1)
                lora_name = lora_name.strip()
                try:
                    weight = float(weight_text.strip())
                except Exception:
                    weight = 1.0
            else:
                lora_name = raw
                weight = 1.0
            if lora_name:
                recommended_loras.append({
                    "name": lora_name,
                    "weight": weight,
                })

        item = {
            "id": self._pl_selected_id or "",
            "name": name,
            "category": self.pl_category.get().strip() or "general",
            "tags": [x.strip() for x in self.pl_tags.get().split(",") if x.strip()],
            "recommended_model": self.pl_recommended_model.get().strip(),
            "recommended_loras": recommended_loras,
            "prompt": self.pl_prompt.get("1.0", "end").strip(),
            "negative_prompt": self.pl_negative.get("1.0", "end").strip(),
            "favorite": False,
            "notes": "",
            "usage_count": 0,
            "last_used_at": "",
        }
        if self._pl_selected_id:
            old = self.repo.get_item("prompt_library", self._pl_selected_id)
            if old:
                item["favorite"] = bool(old.get("favorite", False))
                item["notes"] = old.get("notes") or ""
                item["usage_count"] = int(old.get("usage_count") or 0)
                item["last_used_at"] = old.get("last_used_at") or ""

        saved = self.repo.upsert_item("prompt_library", item)
        self._pl_selected_id = saved.get("id")
        self.refresh_prompt_library()
        if self._pl_selected_id in self.pl_tree.get_children():
            self.pl_tree.selection_set(self._pl_selected_id)
            self.pl_tree.focus(self._pl_selected_id)
        self.pl_status.set(f"保存しました: {name}")
        if hasattr(self, "core_tree"):
            self.refresh_core_status()

    def pl_rename_selected(self):
        item = self._pl_selected_item()
        if not item:
            messagebox.showinfo(
                "Prompt Library",
                "正式名称を変更する項目を選択してください。"
            )
            return

        new_name = self.pl_name.get().strip()
        if not new_name:
            messagebox.showinfo(
                "Prompt Library",
                "正式名称を入力してください。"
            )
            return

        current_name = item.get("name") or ""
        if new_name == current_name:
            messagebox.showinfo(
                "Prompt Library",
                "正式名称は変更されていません。"
            )
            return

        # Prevent ambiguous duplicate names because Generate quick-setup selects by name.
        for other in self.repo.list_items("prompt_library"):
            if (
                other.get("id") != item.get("id")
                and (other.get("name") or "").strip() == new_name
            ):
                messagebox.showwarning(
                    "Prompt Library",
                    f"同じ正式名称「{new_name}」が既に存在します。\n"
                    "別の名称を使用してください。"
                )
                return

        updated = dict(item)
        updated["name"] = new_name
        saved = self.repo.upsert_item("prompt_library", updated)

        self._pl_selected_id = saved.get("id")
        self.refresh_prompt_library()

        if self._pl_selected_id in self.pl_tree.get_children():
            self.pl_tree.selection_set(self._pl_selected_id)
            self.pl_tree.focus(self._pl_selected_id)
            self.pl_tree.see(self._pl_selected_id)
            self._pl_show_selected()

        # If this Prompt is currently active in Generate, keep the displayed name in sync.
        if getattr(self, "_quick_active_prompt_name", "") == current_name:
            self._quick_active_prompt_name = new_name
            try:
                if self.quick_prompt.get() == current_name:
                    self.quick_prompt.set(new_name)
            except Exception:
                pass

            if hasattr(self, "quick_active_summary"):
                project_name = getattr(self, "_quick_active_project_name", "未選択")
                character_name = getattr(self, "_quick_active_character_name", "未選択")
                self.quick_active_summary.set(
                    f"現在適用中: Project={project_name} / "
                    f"Character={character_name} / Prompt={new_name}"
                )

        self.status.set(
            f"Prompt Libraryの正式名称を変更しました: {current_name} → {new_name}"
        )
        messagebox.showinfo(
            "Prompt Library",
            f"正式名称を変更しました。\n\n{new_name}"
        )

    def pl_toggle_favorite(self):
        item = self._pl_selected_item()
        if not item:
            messagebox.showinfo("Prompt Library", "お気に入りを切り替える項目を選択してください。")
            return
        item["favorite"] = not bool(item.get("favorite", False))
        self.repo.upsert_item("prompt_library", item)
        self.refresh_prompt_library()
        if item["id"] in self.pl_tree.get_children():
            self.pl_tree.selection_set(item["id"])
            self.pl_tree.focus(item["id"])
        self.pl_status.set("お気に入りを更新しました")

    def _pl_record_usage(self, item):
        if not item:
            return item
        updated = dict(item)
        updated["usage_count"] = int(updated.get("usage_count") or 0) + 1
        updated["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        saved = self.repo.upsert_item("prompt_library", updated)
        try:
            self.refresh_prompt_library()
            if saved.get("id") in self.pl_tree.get_children():
                self.pl_tree.selection_set(saved.get("id"))
                self.pl_tree.focus(saved.get("id"))
        except Exception:
            pass
        return saved

    def pl_append_selected_to_generate(self):
        item = self._pl_selected_item()
        if not item:
            messagebox.showinfo(
                "Prompt Library",
                "生成タブへ追記する項目を選択してください。"
            )
            return

        add_prompt = (item.get("prompt") or "").strip()
        add_negative = (item.get("negative_prompt") or "").strip()

        current_prompt = self.prompt.get("1.0", "end").strip()
        current_negative = self.negative.get("1.0", "end").strip()

        if add_prompt:
            merged_prompt = (
                f"{current_prompt}, {add_prompt}"
                if current_prompt else add_prompt
            )
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", merged_prompt)

        if add_negative:
            merged_negative = (
                f"{current_negative}, {add_negative}"
                if current_negative else add_negative
            )
            self.negative.delete("1.0", "end")
            self.negative.insert("1.0", merged_negative)

        item = self._pl_record_usage(item)

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        self.status.set(
            f"Prompt Library「{item.get('name','')}」を生成タブへ追記しました。生成は開始していません。"
        )

    def pl_load_selected_to_generate(self):
        item = self._pl_selected_item()
        if not item:
            messagebox.showinfo("Prompt Library", "生成タブへ読み込む項目を選択してください。")
            return
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", item.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", item.get("negative_prompt") or "")
        item = self._pl_record_usage(item)
        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        self._quick_active_prompt_name = item.get("name") or "未選択"
        if hasattr(self, "quick_active_summary"):
            project_name = getattr(self, "_quick_active_project_name", "未選択")
            character_name = getattr(self, "_quick_active_character_name", "未選択")
            self.quick_active_summary.set(
                f"現在適用中: Project={project_name} / "
                f"Character={character_name} / Prompt={self._quick_active_prompt_name}"
            )

        self._mark_generate_saved()
        self._refresh_generate_workflow_state()
        self.status.set(
            f"Prompt Library「{item.get('name','')}」を生成タブへ読み込みました。生成は開始していません。"
        )


    def refresh_prompt_recommendation_choices(self):
        if not hasattr(self, "pl_recommended_model_combo"):
            return

        model_names = []
        try:
            for asset in list(getattr(self, "local_models", [])) + list(getattr(self, "nas_models", [])):
                name = asset.path.name
                if name not in model_names:
                    model_names.append(name)
        except Exception:
            pass

        try:
            current_values = list(self.model_combo["values"])
            for name in current_values:
                if name and name not in model_names:
                    model_names.append(name)
        except Exception:
            pass

        self.pl_recommended_model_combo["values"] = [""] + model_names

    def capture_current_generate_recommendations(self):
        # Model
        try:
            model = self.model_combo.get().strip()
        except Exception:
            model = ""
        self.pl_recommended_model.set(model)

        # LoRA
        parts = []
        try:
            for name, weight in self.active_loras.items():
                parts.append(f"{name}:{float(weight):g}")
        except Exception:
            pass
        self.pl_recommended_loras.set(", ".join(parts))

        self.pl_status.set(
            "現在のGenerate設定から推奨Model / LoRAを取得しました。"
        )

    def open_prompt_lora_picker(self):
        win = tk.Toplevel(self)
        win.title("Prompt Library - 推奨LoRA選択")
        win.geometry("620x520")
        win.transient(self)

        ttk.Label(
            win,
            text="推奨に含めるLoRAを選択し、Weightを設定してください。",
            padding=(10, 10, 10, 4)
        ).pack(anchor="w")

        body = ttk.Frame(win, padding=(10, 4, 10, 8))
        body.pack(fill="both", expand=True)

        listbox = tk.Listbox(
            body,
            selectmode="extended",
            exportselection=False
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        lora_names = []
        try:
            for asset in getattr(self, "lora_items", []):
                name = asset.path.stem
                lora_names.append(name)
                listbox.insert("end", name)
        except Exception:
            pass

        current = {}
        for raw in self.pl_recommended_loras.get().split(","):
            raw = raw.strip()
            if not raw:
                continue
            if ":" in raw:
                name, weight_text = raw.rsplit(":", 1)
                try:
                    current[name.strip()] = float(weight_text.strip())
                except Exception:
                    current[name.strip()] = 1.0
            else:
                current[raw] = 1.0

        for idx, name in enumerate(lora_names):
            if name in current:
                listbox.selection_set(idx)

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.pack(fill="x")

        ttk.Label(bottom, text="Weight").pack(side="left")
        weight_var = tk.DoubleVar(value=1.0)
        ttk.Entry(
            bottom,
            textvariable=weight_var,
            width=8
        ).pack(side="left", padx=(6, 12))

        def apply_picker():
            selected = listbox.curselection()
            if not selected:
                self.pl_recommended_loras.set("")
                win.destroy()
                return

            try:
                weight = float(weight_var.get())
            except Exception:
                messagebox.showerror(
                    "Prompt Library",
                    "Weightは数値で指定してください。",
                    parent=win
                )
                return

            result = []
            for idx in selected:
                name = lora_names[idx]
                # Keep prior per-LoRA weight if it existed, otherwise use current Weight field.
                use_weight = current.get(name, weight)
                result.append(f"{name}:{float(use_weight):g}")

            self.pl_recommended_loras.set(", ".join(result))
            win.destroy()

        ttk.Button(
            bottom,
            text="選択を反映",
            command=apply_picker
        ).pack(side="right")
        ttk.Button(
            bottom,
            text="キャンセル",
            command=win.destroy
        ).pack(side="right", padx=(0, 6))

    def pl_load_selected_with_recommendations(self):
        item = self._pl_selected_item()
        if not item:
            messagebox.showinfo(
                "Prompt Library",
                "生成タブへ読み込む項目を選択してください。"
            )
            return

        # Prompt / Negative
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", item.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", item.get("negative_prompt") or "")

        # Recommended model: select only; do not switch Forge automatically.
        model = str(item.get("recommended_model") or "").strip()
        if model:
            try:
                values = list(self.model_combo["values"])
                if model not in values:
                    self.model_combo["values"] = values + [model]
                self.model_combo.set(model)
            except Exception:
                pass

        # Recommended LoRA: merge into current Studio-side ON state.
        missing = []
        available = {
            a.path.stem.lower(): a.path.stem
            for a in getattr(self, "lora_items", [])
        }
        for entry in item.get("recommended_loras") or []:
            if not isinstance(entry, dict):
                continue
            requested = str(entry.get("name") or "").strip()
            if not requested:
                continue
            actual = available.get(requested.lower())
            if getattr(self, "lora_items", None) and not actual:
                missing.append(requested)
                continue
            try:
                weight = float(entry.get("weight", 1.0))
            except Exception:
                weight = 1.0
            self.active_loras[actual or requested] = weight

        self._refresh_lora_tree()
        item = self._pl_record_usage(item)

        self._quick_active_prompt_name = item.get("name") or "未選択"
        if hasattr(self, "quick_active_summary"):
            project_name = getattr(self, "_quick_active_project_name", "未選択")
            character_name = getattr(self, "_quick_active_character_name", "未選択")
            self.quick_active_summary.set(
                f"現在適用中: Project={project_name} / "
                f"Character={character_name} / Prompt={self._quick_active_prompt_name}"
            )

        self._mark_generate_saved()
        self._refresh_generate_workflow_state()

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        message = (
            f"Prompt Library「{item.get('name','')}」と推奨設定を適用しました。"
            "生成は開始していません。"
        )
        if missing:
            message += " 見つからないLoRA: " + ", ".join(missing)
        self.status.set(message)

    def _prompt_builder_data_path(self):
        return Path(self.shared_root) / "Data" / "prompt_builder_parts.json"

    def _load_prompt_builder_parts(self):
        path = self._prompt_builder_data_path()
        if not path.exists():
            return dict(PROMPT_BUILDER_DEFAULT_PARTS)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return normalize_prompt_builder_parts(data)
        except Exception:
            return dict(PROMPT_BUILDER_DEFAULT_PARTS)

    def _save_prompt_builder_parts(self):
        path = self._prompt_builder_data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                self.prompt_builder_parts,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )
        os.replace(temp, path)

    def _build_prompt_builder(self):
        top = ttk.Frame(self.prompt_builder)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="Prompt Builder",
            font=("", 15, "bold")
        ).pack(side="left")

        ttk.Button(
            top,
            text="部品追加",
            command=self.prompt_builder_add_part
        ).pack(side="right")
        ttk.Button(
            top,
            text="Prompt Libraryから追加",
            command=self.prompt_builder_add_from_library
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            top,
            text="現在Promptから追加",
            command=self.prompt_builder_add_from_generate
        ).pack(side="right", padx=(0, 6))

        ttk.Label(
            self.prompt_builder,
            text=(
                "カテゴリごとの部品を選び、Generate Promptへ組み立てます。"
                " 既存Promptは消さず、ベースとして利用できます。"
            ),
            anchor="w"
        ).pack(fill="x", pady=(4, 8))

        self.prompt_builder_parts = self._load_prompt_builder_parts()
        self.prompt_builder_favorite_only = tk.BooleanVar(value=False)
        self.prompt_builder_recommend_only = tk.BooleanVar(value=False)
        self.prompt_builder_selected = {
            key: tk.StringVar(value="")
            for key, _ in PROMPT_BUILDER_CATEGORIES
        }
        self.prompt_builder_combos = {}

        filter_row = ttk.Frame(self.prompt_builder)
        filter_row.pack(fill="x", pady=(0, 8))

        ttk.Checkbutton(
            filter_row,
            text="お気に入りのみ",
            variable=self.prompt_builder_favorite_only,
            command=self._refresh_prompt_builder_choices
        ).pack(side="left")

        ttk.Checkbutton(
            filter_row,
            text="Character推奨のみ",
            variable=self.prompt_builder_recommend_only,
            command=self._refresh_prompt_builder_choices
        ).pack(side="left", padx=(10, 0))

        self.prompt_builder_character_info = tk.StringVar(
            value="Character推奨: 未選択"
        )
        ttk.Label(
            filter_row,
            textvariable=self.prompt_builder_character_info
        ).pack(side="right")

        grid = ttk.LabelFrame(
            self.prompt_builder,
            text="Prompt部品",
            padding=8
        )
        grid.pack(fill="x")

        for row, (key, label) in enumerate(PROMPT_BUILDER_CATEGORIES):
            ttk.Label(
                grid, text=label, width=12
            ).grid(row=row, column=0, sticky="w", pady=4)

            combo = ttk.Combobox(
                grid,
                textvariable=self.prompt_builder_selected[key],
                state="readonly"
            )
            combo.grid(
                row=row, column=1,
                sticky="ew",
                padx=(6, 6),
                pady=4
            )
            self.prompt_builder_combos[key] = combo

            ttk.Button(
                grid,
                text="★",
                command=lambda k=key: self.prompt_builder_toggle_favorite(k)
            ).grid(row=row, column=2, padx=(0, 4))

            ttk.Button(
                grid,
                text="編集",
                command=lambda k=key: self.prompt_builder_edit_part(k)
            ).grid(row=row, column=3, padx=(0, 4))

            ttk.Button(
                grid,
                text="削除",
                command=lambda k=key: self.prompt_builder_delete_part(k)
            ).grid(row=row, column=4)

        grid.columnconfigure(1, weight=1)

        preview_box = ttk.LabelFrame(
            self.prompt_builder,
            text="組み立て結果",
            padding=8
        )
        preview_box.pack(fill="both", expand=True, pady=(10, 0))

        self.prompt_builder_preview = tk.Text(
            preview_box,
            height=12,
            wrap="word"
        )
        self.prompt_builder_preview.pack(
            fill="both", expand=True
        )

        actions = ttk.Frame(preview_box)
        actions.pack(fill="x", pady=(8, 0))

        ttk.Button(
            actions,
            text="プレビュー更新",
            command=self.prompt_builder_refresh_preview
        ).pack(side="left")

        ttk.Button(
            actions,
            text="選択クリア",
            command=self.prompt_builder_clear_selection
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            actions,
            text="Generateへ反映",
            command=self.prompt_builder_apply_to_generate
        ).pack(side="right")

        self.prompt_builder_status = tk.StringVar(value="")
        ttk.Label(
            self.prompt_builder,
            textvariable=self.prompt_builder_status,
            anchor="w"
        ).pack(fill="x", pady=(6, 0))

        self._refresh_prompt_builder_choices()
        self.prompt_builder_refresh_preview()

    def _prompt_builder_current_character_name(self):
        name = getattr(self, "_quick_active_character_name", "") or ""
        if name and name != "未選択":
            return name
        try:
            item = self.repo.get_item(
                "characters",
                getattr(self, "active_character_id", "") or ""
            )
            if item:
                return item.get("name") or ""
        except Exception:
            pass
        return ""

    def _refresh_prompt_builder_choices(self):
        character_name = self._prompt_builder_current_character_name()
        if hasattr(self, "prompt_builder_character_info"):
            self.prompt_builder_character_info.set(
                f"Character推奨: {character_name or '未選択'}"
            )

        fav_only = (
            self.prompt_builder_favorite_only.get()
            if hasattr(self, "prompt_builder_favorite_only")
            else False
        )
        rec_only = (
            self.prompt_builder_recommend_only.get()
            if hasattr(self, "prompt_builder_recommend_only")
            else False
        )

        for key, _label in PROMPT_BUILDER_CATEGORIES:
            combo = self.prompt_builder_combos.get(key)
            if not combo:
                continue

            items = []
            for item in self.prompt_builder_parts.get(key) or []:
                if fav_only and not item.get("favorite", False):
                    continue
                if rec_only:
                    chars = item.get("characters") or []
                    if not character_name or character_name not in chars:
                        continue
                items.append(item)

            # Favorites and current Character recommendations first.
            items.sort(
                key=lambda x: (
                    character_name in (x.get("characters") or []),
                    bool(x.get("favorite", False)),
                    str(x.get("name") or "").lower(),
                ),
                reverse=True
            )

            names = [""] + [x.get("name") for x in items if x.get("name")]
            combo["values"] = names
            if self.prompt_builder_selected[key].get() not in names:
                self.prompt_builder_selected[key].set("")

    def _prompt_builder_find_part(self, category, name):
        for item in self.prompt_builder_parts.get(category) or []:
            if (item.get("name") or "") == name:
                return item
        return None

    def _prompt_builder_selected_prompt_parts(self):
        result = []
        for key, _label in PROMPT_BUILDER_CATEGORIES:
            name = self.prompt_builder_selected[key].get().strip()
            if not name:
                continue
            item = self._prompt_builder_find_part(key, name)
            if item and item.get("prompt"):
                result.append(item["prompt"])
        return result

    def prompt_builder_refresh_preview(self):
        base = ""
        try:
            base = self.prompt.get("1.0", "end").strip()
        except Exception:
            pass

        built = build_prompt_from_parts(
            base,
            self._prompt_builder_selected_prompt_parts()
        )

        self.prompt_builder_preview.delete("1.0", "end")
        self.prompt_builder_preview.insert("1.0", built)

        selected_count = sum(
            1
            for key, _ in PROMPT_BUILDER_CATEGORIES
            if self.prompt_builder_selected[key].get().strip()
        )
        self.prompt_builder_status.set(
            f"選択部品: {selected_count}件"
        )

    def prompt_builder_clear_selection(self):
        for key, _label in PROMPT_BUILDER_CATEGORIES:
            self.prompt_builder_selected[key].set("")
        self.prompt_builder_refresh_preview()

    def prompt_builder_apply_to_generate(self):
        self.prompt_builder_refresh_preview()
        built = self.prompt_builder_preview.get("1.0", "end").strip()
        if not built:
            messagebox.showinfo(
                "Prompt Builder",
                "組み立てるPromptがありません。"
            )
            return

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", built)
        self._quick_active_prompt_name = "Prompt Builder"
        self._schedule_generate_dirty_check()
        self._refresh_generate_workflow_state()

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        self.status.set(
            "Prompt Builderの内容をGenerateへ反映しました。生成は開始していません。"
        )

    def _prompt_builder_choose_category(self, title="Prompt Builder"):
        category = simpledialog.askstring(
            title,
            "カテゴリを入力してください。\\n"
            "outfit / background / expression / pose / camera / quality",
            parent=self
        )
        if not category:
            return ""
        category = category.strip().lower()
        valid = {key for key, _ in PROMPT_BUILDER_CATEGORIES}
        if category not in valid:
            messagebox.showinfo(
                title,
                "カテゴリ名が正しくありません。"
            )
            return ""
        return category

    def _prompt_builder_add_record(
        self, category, name, prompt_text, *, character_name=""
    ):
        items = self.prompt_builder_parts.setdefault(category, [])
        if any((x.get("name") or "") == name for x in items):
            messagebox.showinfo(
                "Prompt Builder",
                "同じ名前の部品がすでにあります。"
            )
            return False

        items.append({
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
            "name": name,
            "prompt": prompt_text,
            "favorite": False,
            "characters": [character_name] if character_name else [],
        })
        self._save_prompt_builder_parts()
        self._refresh_prompt_builder_choices()
        self.prompt_builder_selected[category].set(name)
        self.prompt_builder_refresh_preview()
        return True

    def prompt_builder_add_from_generate(self):
        prompt_text = self.prompt.get("1.0", "end").strip()
        if not prompt_text:
            messagebox.showinfo(
                "Prompt Builder",
                "GenerateのPromptが空です。"
            )
            return

        category = self._prompt_builder_choose_category(
            "現在Promptから追加"
        )
        if not category:
            return

        name = simpledialog.askstring(
            "現在Promptから追加",
            "部品名を入力してください。",
            parent=self
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        # Allow selecting a comma-delimited chunk instead of forcing the full prompt.
        suggested = prompt_text
        prompt_part = simpledialog.askstring(
            "現在Promptから追加",
            "部品として保存するPrompt文字列",
            initialvalue=suggested,
            parent=self
        )
        if not prompt_part:
            return

        self._prompt_builder_add_record(
            category,
            name,
            prompt_part.strip(),
            character_name=self._prompt_builder_current_character_name()
        )

    def prompt_builder_add_from_library(self):
        try:
            items = self.repo.list_items("prompt_library")
        except Exception:
            items = []

        names = [x.get("name") for x in items if x.get("name")]
        if not names:
            messagebox.showinfo(
                "Prompt Builder",
                "Prompt Libraryに登録がありません。"
            )
            return

        chosen = simpledialog.askstring(
            "Prompt Libraryから追加",
            "Prompt Library名を入力してください。\\n\\n"
            + "\\n".join(names[:20]),
            parent=self
        )
        if not chosen:
            return
        chosen = chosen.strip()

        item = next(
            (x for x in items if (x.get("name") or "") == chosen),
            None
        )
        if not item:
            messagebox.showinfo(
                "Prompt Builder",
                "指定したPrompt Library項目が見つかりません。"
            )
            return

        category = self._prompt_builder_choose_category(
            "Prompt Libraryから追加"
        )
        if not category:
            return

        prompt_text = item.get("prompt") or ""
        if not prompt_text:
            messagebox.showinfo(
                "Prompt Builder",
                "選択したPrompt LibraryのPromptが空です。"
            )
            return

        name = simpledialog.askstring(
            "Prompt Libraryから追加",
            "部品名",
            initialvalue=item.get("name") or "",
            parent=self
        )
        if not name:
            return

        prompt_part = simpledialog.askstring(
            "Prompt Libraryから追加",
            "部品として保存するPrompt文字列",
            initialvalue=prompt_text,
            parent=self
        )
        if not prompt_part:
            return

        self._prompt_builder_add_record(
            category,
            name.strip(),
            prompt_part.strip(),
            character_name=self._prompt_builder_current_character_name()
        )

    def prompt_builder_toggle_favorite(self, category):
        name = self.prompt_builder_selected[category].get().strip()
        if not name:
            messagebox.showinfo(
                "Prompt Builder",
                "お気に入りを切り替える部品を選択してください。"
            )
            return

        item = self._prompt_builder_find_part(category, name)
        if not item:
            return

        item["favorite"] = not bool(item.get("favorite", False))
        self._save_prompt_builder_parts()
        self._refresh_prompt_builder_choices()
        if name in self.prompt_builder_combos[category]["values"]:
            self.prompt_builder_selected[category].set(name)
        self.prompt_builder_status.set(
            f"お気に入り{'ON' if item['favorite'] else 'OFF'}: {name}"
        )

    def prompt_builder_add_part(self):
        category = self._prompt_builder_choose_category()
        if not category:
            return

        name = simpledialog.askstring(
            "Prompt Builder",
            "部品名を入力してください。",
            parent=self
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        prompt_text = simpledialog.askstring(
            "Prompt Builder",
            "Prompt文字列を入力してください。",
            parent=self
        )
        if not prompt_text:
            return
        prompt_text = prompt_text.strip()
        if not prompt_text:
            return

        self._prompt_builder_add_record(
            category,
            name,
            prompt_text,
            character_name=self._prompt_builder_current_character_name()
        )

    def prompt_builder_edit_part(self, category):
        name = self.prompt_builder_selected[category].get().strip()
        if not name:
            messagebox.showinfo(
                "Prompt Builder",
                "編集する部品を選択してください。"
            )
            return

        item = self._prompt_builder_find_part(category, name)
        if not item:
            return

        new_name = simpledialog.askstring(
            "Prompt Builder",
            "部品名",
            initialvalue=item.get("name") or "",
            parent=self
        )
        if not new_name:
            return
        new_name = new_name.strip()

        new_prompt = simpledialog.askstring(
            "Prompt Builder",
            "Prompt文字列",
            initialvalue=item.get("prompt") or "",
            parent=self
        )
        if not new_prompt:
            return
        new_prompt = new_prompt.strip()

        item["name"] = new_name
        item["prompt"] = new_prompt

        current_character = self._prompt_builder_current_character_name()
        if current_character:
            if messagebox.askyesno(
                "Prompt Builder",
                f"この部品をCharacter「{current_character}」の推奨にしますか？"
            ):
                chars = list(item.get("characters") or [])
                if current_character not in chars:
                    chars.append(current_character)
                item["characters"] = chars

        self._save_prompt_builder_parts()
        self._refresh_prompt_builder_choices()
        self.prompt_builder_selected[category].set(new_name)
        self.prompt_builder_refresh_preview()

    def prompt_builder_delete_part(self, category):
        name = self.prompt_builder_selected[category].get().strip()
        if not name:
            messagebox.showinfo(
                "Prompt Builder",
                "削除する部品を選択してください。"
            )
            return

        if not messagebox.askyesno(
            "Prompt Builder",
            f"「{name}」を削除しますか？"
        ):
            return

        self.prompt_builder_parts[category] = [
            x for x in self.prompt_builder_parts.get(category, [])
            if (x.get("name") or "") != name
        ]
        self._save_prompt_builder_parts()
        self.prompt_builder_selected[category].set("")
        self._refresh_prompt_builder_choices()
        self.prompt_builder_refresh_preview()

    def _build_ai_assistant(self):
        top = ttk.Frame(self.ai_assistant)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="AI Assistant",
            font=("", 15, "bold")
        ).pack(side="left")

        api_enabled = False
        try:
            cfg = load_api_config(
                Path(self.shared_root) / "Data" / "ai_api_config.json"
            )
            api_enabled = bool(cfg.get("enabled", False))
        except Exception:
            pass

        self.ai_assistant_mode = tk.StringVar(
            value=("OpenAI API" if api_enabled else "ローカル補助")
        )
        ttk.Combobox(
            top,
            textvariable=self.ai_assistant_mode,
            state="readonly",
            values=("ローカル補助", "OpenAI API"),
            width=16
        ).pack(side="right")
        ttk.Label(top, text="解析モード").pack(
            side="right", padx=(0, 6)
        )

        ttk.Label(
            self.ai_assistant,
            text=(
                "現在は安全なローカル補助のみ。"
                " Project / Character / Model / LoRA / 生成設定は変更しません。"
            ),
            anchor="w"
        ).pack(fill="x", pady=(4, 10))

        instruction_box = ttk.LabelFrame(
            self.ai_assistant,
            text="指示",
            padding=8
        )
        instruction_box.pack(fill="x")

        self.ai_instruction = tk.Text(
            instruction_box,
            height=4,
            wrap="word"
        )
        self.ai_instruction.pack(fill="x")

        quick = ttk.Frame(instruction_box)
        quick.pack(fill="x", pady=(6, 0))

        for label, text in (
            ("背景→図書館", "背景だけ図書館にして"),
            ("夜にする", "夜に変更"),
            ("笑顔", "表情だけ笑顔にして"),
            ("カメラ引き", "カメラを少し引いて"),
            ("品質追加", "品質タグを追加"),
            ("Prompt整理", "このPromptを整理して"),
        ):
            ttk.Button(
                quick,
                text=label,
                command=lambda t=text: self.ai_assistant_set_instruction(t)
            ).pack(side="left", padx=(0, 6))

        current_box = ttk.LabelFrame(
            self.ai_assistant,
            text="現在のGenerate",
            padding=8
        )
        current_box.pack(fill="both", expand=True, pady=(10, 0))

        self.ai_current_prompt = tk.Text(
            current_box,
            height=7,
            wrap="word",
            state="disabled"
        )
        self.ai_current_prompt.pack(fill="both", expand=True)

        proposal_box = ttk.LabelFrame(
            self.ai_assistant,
            text="変更案",
            padding=8
        )
        proposal_box.pack(fill="both", expand=True, pady=(10, 0))

        self.ai_proposal_summary = tk.StringVar(value="解析前")
        ttk.Label(
            proposal_box,
            textvariable=self.ai_proposal_summary,
            anchor="w"
        ).pack(fill="x")

        self.ai_proposal_reason = tk.StringVar(value="")
        ttk.Label(
            proposal_box,
            textvariable=self.ai_proposal_reason,
            anchor="w",
            wraplength=900
        ).pack(fill="x", pady=(3, 0))

        self.ai_proposal_prompt = tk.Text(
            proposal_box,
            height=7,
            wrap="word",
            state="disabled"
        )
        self.ai_proposal_prompt.pack(fill="both", expand=True, pady=(6, 0))

        action = ttk.Frame(self.ai_assistant)
        action.pack(fill="x", pady=(10, 0))

        ttk.Button(
            action,
            text="現在Promptを取得",
            command=self.ai_assistant_refresh_current
        ).pack(side="left")

        ttk.Button(
            action,
            text="解析",
            command=self.ai_assistant_analyze
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            action,
            text="元に戻す",
            command=self.ai_assistant_undo
        ).pack(side="right")

        ttk.Button(
            action,
            text="適用",
            command=self.ai_assistant_apply
        ).pack(side="right", padx=(0, 6))

        ttk.Button(
            action,
            text="履歴",
            command=self.ai_assistant_show_history
        ).pack(side="right", padx=(0, 6))

        self.ai_assistant_status = tk.StringVar(value="")
        ttk.Label(
            self.ai_assistant,
            textvariable=self.ai_assistant_status,
            anchor="w"
        ).pack(fill="x", pady=(6, 0))

        self._ai_proposal = None
        self._ai_undo_stack = []
        self._ai_history = []

        self.ai_assistant_refresh_current()

    def ai_assistant_set_instruction(self, text):
        self.ai_instruction.delete("1.0", "end")
        self.ai_instruction.insert("1.0", text)

    def _ai_assistant_current_values(self):
        try:
            prompt = self.prompt.get("1.0", "end").strip()
        except Exception:
            prompt = ""
        try:
            negative = self.negative.get("1.0", "end").strip()
        except Exception:
            negative = ""
        return prompt, negative

    def ai_assistant_refresh_current(self):
        prompt, negative = self._ai_assistant_current_values()
        text = "Prompt:\n" + prompt + "\n\nNegative:\n" + negative
        self.ai_current_prompt.configure(state="normal")
        self.ai_current_prompt.delete("1.0", "end")
        self.ai_current_prompt.insert("1.0", text)
        self.ai_current_prompt.configure(state="disabled")
        self.ai_assistant_status.set("現在のGenerate内容を取得しました。")

    def _ai_character_context(self):
        character_id = getattr(self, "active_character_id", "") or ""
        if not character_id:
            return ""
        try:
            item = self.repo.get_item("characters", character_id)
        except Exception:
            item = None
        if not item:
            return ""

        loras = []
        for x in item.get("loras") or []:
            if isinstance(x, dict) and x.get("name"):
                loras.append(str(x.get("name")))

        return "\n".join([
            f"Character name: {item.get('name') or ''}",
            f"Base prompt: {item.get('prompt') or ''}",
            f"Base negative: {item.get('negative_prompt') or ''}",
            f"LoRA names (reference only; do not change): {', '.join(loras)}",
        ])

    def _ai_builder_context(self):
        if not hasattr(self, "prompt_builder_selected"):
            return ""
        lines = []
        for key, label in PROMPT_BUILDER_CATEGORIES:
            try:
                name = self.prompt_builder_selected[key].get().strip()
            except Exception:
                name = ""
            if name:
                lines.append(f"{label}: {name}")
        return "\n".join(lines)

    def ai_assistant_analyze(self):
        instruction = self.ai_instruction.get(
            "1.0", "end"
        ).strip()
        prompt, negative = self._ai_assistant_current_values()

        if not instruction:
            messagebox.showinfo(
                "AI Assistant",
                "指示を入力してください。"
            )
            return

        mode = self.ai_assistant_mode.get().strip()

        if mode != "OpenAI API":
            proposal = analyze_prompt_instruction(
                instruction,
                prompt,
                negative
            )
            proposal["source"] = "ローカル補助"
            self._ai_show_proposal(proposal)
            return

        cfg = load_api_config(
            Path(self.shared_root)
            / "Data"
            / "ai_api_config.json"
        )
        if not cfg.get("enabled", False):
            messagebox.showinfo(
                "AI Assistant",
                "設定タブで「AI APIを使用する」をONにして保存してください。"
            )
            return

        api_key, _source = effective_api_key(
            cfg,
            getattr(self, "ai_api_key", tk.StringVar()).get()
            if hasattr(self, "ai_api_key")
            else ""
        )
        if not api_key:
            messagebox.showinfo(
                "AI Assistant",
                "OpenAI APIキーが未設定です。"
            )
            return

        model = str(
            cfg.get("model")
            or "gpt-5.6-luna"
        )
        base_url = str(
            cfg.get("base_url")
            or "https://api.openai.com/v1"
        )

        self.ai_assistant_status.set(
            f"OpenAI APIで解析中… ({model})"
        )

        character_context = self._ai_character_context()
        builder_context = self._ai_builder_context()

        def worker():
            try:
                proposal = analyze_prompt_with_openai(
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    instruction=instruction,
                    prompt=prompt,
                    negative_prompt=negative,
                    character_context=character_context,
                    builder_context=builder_context,
                )
                self.after(
                    0,
                    lambda p=proposal:
                        self._ai_show_proposal(p)
                )
            except Exception as e:
                self.after(
                    0,
                    lambda err=str(e):
                        self.ai_assistant_status.set(
                            "OpenAI API解析に失敗しました。"
                        )
                )
                self.after(
                    0,
                    lambda err=str(e):
                        messagebox.showerror(
                            "AI Assistant",
                            err
                        )
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def _ai_show_proposal(self, proposal):
        self._ai_proposal = proposal

        source = proposal.get("source") or ""
        summary = proposal.get("summary") or "変更案なし"
        self.ai_proposal_summary.set(
            f"[{source}] {summary}" if source else summary
        )
        if hasattr(self, "ai_proposal_reason"):
            self.ai_proposal_reason.set(
                proposal.get("reason") or ""
            )

        text = (
            "Prompt:\n"
            + (proposal.get("prompt") or "")
            + "\n\nNegative:\n"
            + (proposal.get("negative_prompt") or "")
        )
        self.ai_proposal_prompt.configure(state="normal")
        self.ai_proposal_prompt.delete("1.0", "end")
        self.ai_proposal_prompt.insert("1.0", text)
        self.ai_proposal_prompt.configure(state="disabled")

        if proposal.get("ok"):
            self.ai_assistant_status.set(
                "変更案を作成しました。まだGenerateへ適用していません。"
            )
        else:
            self.ai_assistant_status.set(
                proposal.get("summary")
                or "変更案を作成できませんでした。"
            )

    def ai_assistant_apply(self):
        proposal = getattr(self, "_ai_proposal", None)
        if not proposal or not proposal.get("ok"):
            messagebox.showinfo(
                "AI Assistant",
                "先に解析して、適用可能な変更案を作成してください。"
            )
            return

        current_prompt, current_negative = self._ai_assistant_current_values()
        self._ai_undo_stack.append({
            "prompt": current_prompt,
            "negative_prompt": current_negative,
        })

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", proposal.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert(
            "1.0",
            proposal.get("negative_prompt") or ""
        )

        history_item = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "instruction": self.ai_instruction.get("1.0", "end").strip(),
            "summary": proposal.get("summary") or "",
            "before_prompt": current_prompt,
            "after_prompt": proposal.get("prompt") or "",
        }
        self._ai_history.append(history_item)

        self._quick_active_prompt_name = "AI Assistant"
        self._schedule_generate_dirty_check()
        self._refresh_generate_workflow_state()
        self.ai_assistant_refresh_current()

        self.ai_assistant_status.set(
            "変更案をGenerateへ適用しました。画像生成は開始していません。"
        )
        self.status.set(
            "AI AssistantのPrompt変更を適用しました。生成は開始していません。"
        )

    def ai_assistant_undo(self):
        if not self._ai_undo_stack:
            messagebox.showinfo(
                "AI Assistant",
                "元に戻せる変更がありません。"
            )
            return

        previous = self._ai_undo_stack.pop()
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", previous.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert(
            "1.0",
            previous.get("negative_prompt") or ""
        )

        self._schedule_generate_dirty_check()
        self._refresh_generate_workflow_state()
        self.ai_assistant_refresh_current()

        self.ai_assistant_status.set(
            "直前のAI Assistant変更を元に戻しました。"
        )

    def ai_assistant_show_history(self):
        win = tk.Toplevel(self)
        win.title("AI Assistant - 変更履歴")
        win.geometry("900x520")
        win.transient(self)

        text = tk.Text(
            win,
            wrap="word",
            state="normal"
        )
        text.pack(fill="both", expand=True, padx=10, pady=10)

        if not self._ai_history:
            text.insert("1.0", "まだ変更履歴はありません。")
        else:
            lines = []
            for i, item in enumerate(reversed(self._ai_history), 1):
                lines.extend([
                    f"[{i}] {item.get('time','')}",
                    f"指示: {item.get('instruction','')}",
                    f"変更: {item.get('summary','')}",
                    "",
                ])
            text.insert("1.0", "\n".join(lines))

        text.configure(state="disabled")

        ttk.Button(
            win,
            text="閉じる",
            command=win.destroy
        ).pack(pady=(0, 10))

    def _image_review_data_path(self):
        return Path(self.shared_root) / "Data" / "image_reviews.json"

    def _load_image_reviews(self):
        path = self._image_review_data_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_image_reviews(self):
        path = self._image_review_data_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(
                self._image_reviews,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )
        os.replace(temp, path)

    def _build_image_review(self):
        top = ttk.Frame(self.image_review)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="画像解析 / 制作レビュー",
            font=("", 15, "bold")
        ).pack(side="left")

        ttk.Button(
            top,
            text="History更新",
            command=self.refresh_image_review_sources
        ).pack(side="right")

        ttk.Label(
            self.image_review,
            text=(
                "生成画像を選び、顔・目・髪・背景・光源・構図などを記録します。"
                " OpenAI API設定済みならAI解析も利用できます。"
            ),
            anchor="w"
        ).pack(fill="x", pady=(4, 8))

        body = ttk.Panedwindow(
            self.image_review,
            orient="horizontal"
        )
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)

        right_outer = ttk.Frame(body)
        right_canvas = tk.Canvas(
            right_outer,
            highlightthickness=0
        )
        right_scroll = ttk.Scrollbar(
            right_outer,
            orient="vertical",
            command=right_canvas.yview
        )
        right_canvas.configure(
            yscrollcommand=right_scroll.set
        )

        right = ttk.Frame(right_canvas)
        right_window = right_canvas.create_window(
            (0, 0),
            window=right,
            anchor="nw"
        )

        def _review_right_configure(_event=None):
            try:
                right_canvas.configure(
                    scrollregion=right_canvas.bbox("all")
                )
            except Exception:
                pass

        def _review_canvas_resize(event):
            try:
                right_canvas.itemconfigure(
                    right_window,
                    width=event.width
                )
            except Exception:
                pass

        right.bind(
            "<Configure>",
            _review_right_configure
        )
        right_canvas.bind(
            "<Configure>",
            _review_canvas_resize
        )

        def _review_mousewheel(event):
            try:
                right_canvas.yview_scroll(
                    int(-1 * (event.delta / 120)),
                    "units"
                )
            except Exception:
                pass

        right_canvas.bind("<MouseWheel>", _review_mousewheel)
        right.bind("<MouseWheel>", _review_mousewheel)

        right_canvas.pack(
            side="left",
            fill="both",
            expand=True
        )
        right_scroll.pack(
            side="right",
            fill="y"
        )

        body.add(left, weight=2)
        body.add(right_outer, weight=3)

        source_box = ttk.LabelFrame(
            left,
            text="画像一覧",
            padding=8
        )
        source_box.pack(fill="both", expand=True)

        filter_row = ttk.Frame(source_box)
        filter_row.pack(fill="x", pady=(0, 6))

        self.image_review_source_filter = tk.StringVar(value="すべて")
        ttk.Label(filter_row, text="対象").pack(side="left")
        ttk.Combobox(
            filter_row,
            textvariable=self.image_review_source_filter,
            state="readonly",
            width=12,
            values=("すべて", "History", "採用DB")
        ).pack(side="left", padx=(6, 0))
        self.image_review_source_filter.trace_add(
            "write",
            lambda *_: self.refresh_image_review_sources()
        )

        cols = ("source", "time", "project", "image")
        self.image_review_tree = ttk.Treeview(
            source_box,
            columns=cols,
            show="headings",
            selectmode="browse"
        )
        for col, title, width in [
            ("source", "種別", 75),
            ("time", "日時", 135),
            ("project", "Project", 130),
            ("image", "画像", 250),
        ]:
            self.image_review_tree.heading(col, text=title)
            self.image_review_tree.column(col, width=width)

        review_scroll = ttk.Scrollbar(
            source_box,
            orient="vertical",
            command=self.image_review_tree.yview
        )
        self.image_review_tree.configure(
            yscrollcommand=review_scroll.set
        )
        self.image_review_tree.pack(
            side="left", fill="both", expand=True
        )
        review_scroll.pack(side="right", fill="y")
        self.image_review_tree.bind(
            "<<TreeviewSelect>>",
            self._show_image_review_selected
        )

        preview_box = ttk.LabelFrame(
            right,
            text="プレビュー",
            padding=8
        )
        preview_box.pack(fill="both", expand=True)

        self.image_review_preview = ttk.Label(
            preview_box,
            text="画像を選択してください",
            anchor="center"
        )
        self.image_review_preview.pack(
            fill="both", expand=True
        )
        self._image_review_preview_ref = None

        rating_box = ttk.LabelFrame(
            right,
            text="評価",
            padding=8
        )
        rating_box.pack(fill="x", pady=(8, 0))

        self.image_review_rating_vars = {}
        for row, (key, label) in enumerate(REVIEW_FIELDS):
            ttk.Label(
                rating_box,
                text=label,
                width=10
            ).grid(row=row, column=0, sticky="w", pady=2)

            var = tk.StringVar(value="未評価")
            self.image_review_rating_vars[key] = var
            ttk.Combobox(
                rating_box,
                textvariable=var,
                state="readonly",
                values=REVIEW_VALUES,
                width=12
            ).grid(row=row, column=1, sticky="w", padx=(6, 0), pady=2)

        notes_box = ttk.LabelFrame(
            right,
            text="改善メモ",
            padding=8
        )
        notes_box.pack(fill="x", pady=(8, 0))

        self.image_review_notes = tk.Text(
            notes_box,
            height=7,
            wrap="word"
        )
        self.image_review_notes.pack(fill="x")

        actions = ttk.Frame(right)
        actions.pack(fill="x", pady=(8, 12))

        ttk.Button(
            actions,
            text="保存",
            command=self.save_image_review_selected
        ).pack(side="left")

        ttk.Button(
            actions,
            text="Generateへ復元",
            command=self.restore_image_review_selected_to_generate
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            actions,
            text="画像を開く",
            command=self.open_image_review_selected
        ).pack(side="left", padx=(6, 0))

        self.image_review_ai_status = tk.StringVar(
            value="AI解析: 未実行（API設定時のみ）"
        )
        ttk.Button(
            actions,
            text="AI解析",
            command=self.image_review_ai_analyze
        ).pack(side="right")

        ttk.Label(
            right,
            textvariable=self.image_review_ai_status,
            anchor="e"
        ).pack(fill="x", pady=(4, 0))

        ai_result_box = ttk.LabelFrame(
            right,
            text="AI解析結果",
            padding=8
        )
        ai_result_box.pack(fill="x", pady=(8, 12))

        self.image_review_ai_result_text = tk.Text(
            ai_result_box,
            height=13,
            wrap="word",
            state="disabled"
        )
        self.image_review_ai_result_text.pack(fill="x")

        ai_result_actions = ttk.Frame(ai_result_box)
        ai_result_actions.pack(fill="x", pady=(6, 0))

        ttk.Button(
            ai_result_actions,
            text="AI評価をレビューへ反映",
            command=self.apply_ai_image_review_ratings
        ).pack(side="left")

        ttk.Button(
            ai_result_actions,
            text="Prompt案をGenerateへ反映",
            command=self.apply_ai_image_review_prompt
        ).pack(side="right")

        self._image_review_ai_result = None

        self._image_reviews = self._load_image_reviews()
        self._image_review_records = {}
        self._image_review_selected_key = ""
        self.refresh_image_review_sources()

    def _image_review_record_key(self, item):
        path = str((item or {}).get("image_path") or "")
        if path:
            return path
        return str((item or {}).get("id") or "")

    def refresh_image_review_sources(self):
        if not hasattr(self, "image_review_tree"):
            return

        self.image_review_tree.delete(
            *self.image_review_tree.get_children()
        )
        self._image_review_records = {}

        source_filter = self.image_review_source_filter.get()
        rows = []

        if source_filter in ("すべて", "History"):
            try:
                for item in self.repo.list_items("history"):
                    row = dict(item)
                    row["_review_source"] = "History"
                    rows.append(row)
            except Exception:
                pass

        if source_filter in ("すべて", "採用DB"):
            try:
                for item in self.repo.list_items("adopted"):
                    row = dict(item)
                    row["_review_source"] = "採用DB"
                    rows.append(row)
            except Exception:
                pass

        def sort_key(x):
            value = x.get("adopted_at") or x.get("created_at") or ""

            # Always return the same sortable type.
            # Numeric timestamps and ISO/date strings can coexist in old data.
            if isinstance(value, (int, float)):
                return (2, float(value))

            text = str(value or "").strip()
            if not text:
                return (0, "")

            try:
                return (2, float(text))
            except Exception:
                pass

            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return (2, parsed.timestamp())
            except Exception:
                return (1, text)

        rows.sort(key=sort_key, reverse=True)

        for index, item in enumerate(rows):
            path = Path(item.get("image_path") or "")
            if not path:
                continue

            project_name = self._adopted_entity_name(
                "projects",
                item.get("project_id") or ""
            )

            time_value = item.get("adopted_at") or item.get("created_at") or ""
            if isinstance(time_value, (int, float)):
                try:
                    time_text = datetime.fromtimestamp(
                        time_value
                    ).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    time_text = str(time_value)
            else:
                time_text = str(time_value).replace("T", " ")[:16]

            iid = f"review_{index}"
            self._image_review_records[iid] = item
            self.image_review_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    item.get("_review_source") or "",
                    time_text,
                    project_name,
                    path.name,
                )
            )

    def _selected_image_review_record(self):
        sel = self.image_review_tree.selection()
        if not sel:
            return None
        return self._image_review_records.get(sel[0])

    def _show_image_review_selected(self, _event=None):
        item = self._selected_image_review_record()
        if not item:
            return

        key = self._image_review_record_key(item)
        self._image_review_selected_key = key

        review = normalize_review(
            self._image_reviews.get(key) or {}
        )

        for field, _label in REVIEW_FIELDS:
            self.image_review_rating_vars[field].set(
                review["ratings"].get(field, "未評価")
            )

        self.image_review_notes.delete("1.0", "end")
        self.image_review_notes.insert(
            "1.0",
            review.get("notes") or ""
        )

        path = Path(item.get("image_path") or "")
        if not path.exists():
            self._image_review_preview_ref = None
            self.image_review_preview.configure(
                image="",
                text=f"画像が見つかりません\n{path}"
            )
            return

        try:
            img = tk.PhotoImage(file=str(path))
            w, h = img.width(), img.height()
            factor = preview_subsample_factor(
                w, h, 520, 360
            )
            if factor > 1:
                img = img.subsample(factor, factor)
            self._image_review_preview_ref = img
            self.image_review_preview.configure(
                image=img,
                text=""
            )
        except Exception:
            self._image_review_preview_ref = None
            self.image_review_preview.configure(
                image="",
                text=f"プレビューできません\n{path.name}"
            )

    def save_image_review_selected(self):
        item = self._selected_image_review_record()
        if not item:
            messagebox.showinfo(
                "画像解析",
                "保存する画像を選択してください。"
            )
            return

        key = self._image_review_record_key(item)
        ratings = {
            field: self.image_review_rating_vars[field].get()
            for field, _label in REVIEW_FIELDS
        }

        self._image_reviews[key] = {
            "ratings": ratings,
            "notes": self.image_review_notes.get(
                "1.0", "end"
            ).strip(),
            "updated_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        try:
            self._save_image_reviews()
        except Exception as e:
            messagebox.showerror(
                "画像解析",
                f"保存に失敗しました。\n{e}"
            )
            return

        self.status.set(
            f"画像レビューを保存しました: "
            f"{Path(item.get('image_path') or '').name}"
        )

    def restore_image_review_selected_to_generate(self):
        item = self._selected_image_review_record()
        if not item:
            messagebox.showinfo(
                "画像解析",
                "Generateへ復元する画像を選択してください。"
            )
            return

        # History / Adopted both carry the same generation fields used by
        # load_selected_history / adopted restore.
        try:
            if item.get("_review_source") == "採用DB":
                self._restore_adopted_to_generate(item)
            else:
                # Temporarily reuse the same safe field restoration logic.
                self._import_loras_from_prompt(
                    item.get("prompt") or ""
                )
                visible_prompt = self._clean_prompt_lora_tags(
                    item.get("prompt") or ""
                )
                self.prompt.delete("1.0", "end")
                self.prompt.insert("1.0", visible_prompt)

                self.negative.delete("1.0", "end")
                self.negative.insert(
                    "1.0",
                    item.get("negative_prompt") or ""
                )

                stored_loras = item.get("loras") or []
                if stored_loras:
                    self.active_loras.clear()
                    for x in stored_loras:
                        if isinstance(x, dict) and x.get("name"):
                            try:
                                weight = float(
                                    x.get("weight", 1.0)
                                )
                            except Exception:
                                weight = 1.0
                            self.active_loras[x["name"]] = weight
                    self._refresh_lora_tree()

                if item.get("sampler"):
                    self.sampler.set(item.get("sampler"))
                if item.get("steps", "") != "":
                    self.steps.set(int(item.get("steps")))
                if item.get("cfg", "") != "":
                    self.cfg.set(float(item.get("cfg")))
                if item.get("width", "") != "":
                    self.width.set(int(item.get("width")))
                if item.get("height", "") != "":
                    self.height.set(int(item.get("height")))

                model = str(item.get("model") or "").strip()
                if model:
                    values = list(self.model_combo["values"])
                    if model not in values:
                        self.model_combo["values"] = values + [model]
                    self.model_combo.set(model)

                self._schedule_generate_dirty_check()
                self._refresh_generate_workflow_state()
                self.tabs.select(self.generate)
        except Exception as e:
            messagebox.showerror(
                "画像解析",
                f"Generateへの復元に失敗しました。\n{e}"
            )
            return

        self.status.set(
            "選択画像の生成設定をGenerateへ復元しました。"
            "生成は開始していません。"
        )

    def open_image_review_selected(self):
        item = self._selected_image_review_record()
        if not item:
            messagebox.showinfo(
                "画像解析",
                "開く画像を選択してください。"
            )
            return

        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "画像解析",
                f"画像ファイルが見つかりません。\n\n{path}"
            )
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror(
                "画像解析",
                f"画像を開けませんでした。\n{e}"
            )

    def _image_review_generation_context(self, item):
        lora_text = []
        for x in (item or {}).get("loras") or []:
            if isinstance(x, dict) and x.get("name"):
                try:
                    lora_text.append(
                        f"{x['name']}:{float(x.get('weight', 1.0)):g}"
                    )
                except Exception:
                    lora_text.append(str(x.get("name")))

        return "\n".join([
            f"Model: {(item or {}).get('model') or ''}",
            f"LoRA: {', '.join(lora_text)}",
            f"Sampler: {(item or {}).get('sampler') or ''}",
            f"Steps: {(item or {}).get('steps') or ''}",
            f"CFG: {(item or {}).get('cfg') or ''}",
            f"Size: {(item or {}).get('width') or ''}x{(item or {}).get('height') or ''}",
        ])

    def _image_review_character_context_for_item(self, item):
        character_id = (item or {}).get("character_id") or ""
        if not character_id:
            return self._ai_character_context()

        try:
            char = self.repo.get_item("characters", character_id)
        except Exception:
            char = None

        if not char:
            return self._ai_character_context()

        loras = []
        for x in char.get("loras") or []:
            if isinstance(x, dict) and x.get("name"):
                loras.append(str(x.get("name")))

        return "\n".join([
            f"Character name: {char.get('name') or ''}",
            f"Base prompt: {char.get('prompt') or ''}",
            f"Base negative: {char.get('negative_prompt') or ''}",
            f"Reference LoRA names: {', '.join(loras)}",
        ])

    def image_review_ai_analyze(self):
        item = self._selected_image_review_record()
        if not item:
            messagebox.showinfo(
                "画像解析",
                "AI解析する画像を選択してください。"
            )
            return

        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "画像解析",
                f"画像ファイルが見つかりません。\n\n{path}"
            )
            return

        cfg = load_api_config(
            Path(self.shared_root)
            / "Data"
            / "ai_api_config.json"
        )
        if not cfg.get("enabled", False):
            messagebox.showinfo(
                "画像解析",
                "設定タブで「AI APIを使用する」をONにして保存してください。"
            )
            return

        api_key, source = effective_api_key(
            cfg,
            self.ai_api_key.get()
            if hasattr(self, "ai_api_key")
            else ""
        )
        if not api_key:
            messagebox.showinfo(
                "画像解析",
                "OpenAI APIキーが未設定です。"
            )
            return

        if not messagebox.askyesno(
            "AI画像解析",
            "選択画像をOpenAI APIへ送信して解析します。\n\n"
            "OpenAIの画像入力要件に適合する画像のみ送信してください。\n"
            "NSFW画像は送信しないでください。\n\n"
            f"画像: {path.name}\n"
            f"API Key: {source}\n\n"
            "送信しますか？"
        ):
            return

        model = str(
            cfg.get("model")
            or "gpt-5.6-luna"
        )
        base_url = str(
            cfg.get("base_url")
            or "https://api.openai.com/v1"
        )

        self.image_review_ai_status.set(
            f"AI解析中… ({model})"
        )

        prompt = item.get("prompt") or ""
        negative = item.get("negative_prompt") or ""
        character_context = self._image_review_character_context_for_item(item)
        generation_context = self._image_review_generation_context(item)

        def worker():
            try:
                result = analyze_image_review_with_openai(
                    api_key=api_key,
                    model=model,
                    base_url=base_url,
                    image_path=path,
                    prompt=prompt,
                    negative_prompt=negative,
                    character_context=character_context,
                    generation_context=generation_context,
                )
                self.after(
                    0,
                    lambda r=result:
                        self._show_ai_image_review_result(r)
                )
            except Exception as e:
                self.after(
                    0,
                    lambda:
                        self.image_review_ai_status.set(
                            "AI解析: 失敗"
                        )
                )
                self.after(
                    0,
                    lambda err=str(e):
                        messagebox.showerror(
                            "AI画像解析",
                            err
                        )
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    def _show_ai_image_review_result(self, result):
        self._image_review_ai_result = result

        lines = [
            f"総評: {result.get('summary') or ''}",
            "",
            "良い点:",
        ]
        for x in result.get("strengths") or []:
            lines.append(f"・{x}")

        lines.extend(["", "問題点:"])
        for x in result.get("issues") or []:
            lines.append(f"・{x}")

        lines.extend(["", "修正優先順位:"])
        for i, x in enumerate(result.get("priorities") or [], 1):
            lines.append(f"{i}. {x}")

        lines.extend([
            "",
            "Prompt修正案:",
            result.get("prompt_suggestion") or "(変更なし)",
            "",
            "Negative修正案:",
            result.get("negative_prompt_suggestion") or "(変更なし)",
            "",
            "LoRA提案:",
        ])
        lora_suggestions = result.get("lora_suggestions") or []
        lines.extend(
            [f"・{x}" for x in lora_suggestions]
            if lora_suggestions else ["・なし"]
        )

        lines.append("")
        lines.append("生成設定提案:")
        setting_suggestions = (
            result.get("generation_setting_suggestions") or []
        )
        lines.extend(
            [f"・{x}" for x in setting_suggestions]
            if setting_suggestions else ["・なし"]
        )

        self.image_review_ai_result_text.configure(state="normal")
        self.image_review_ai_result_text.delete("1.0", "end")
        self.image_review_ai_result_text.insert(
            "1.0",
            "\n".join(lines)
        )
        self.image_review_ai_result_text.configure(state="disabled")

        self.image_review_ai_status.set(
            "AI解析: 完了（まだ何も反映していません）"
        )

    def apply_ai_image_review_ratings(self):
        result = getattr(self, "_image_review_ai_result", None)
        if not result:
            messagebox.showinfo(
                "画像解析",
                "先にAI解析を実行してください。"
            )
            return

        ratings = result.get("ratings") or {}
        for field, _label in REVIEW_FIELDS:
            value = ratings.get(field)
            if value in REVIEW_VALUES:
                self.image_review_rating_vars[field].set(value)

        notes = []
        if result.get("summary"):
            notes.append("【AI総評】")
            notes.append(result.get("summary"))
        if result.get("issues"):
            notes.append("\n【問題点】")
            notes.extend(f"・{x}" for x in result.get("issues"))
        if result.get("priorities"):
            notes.append("\n【修正優先順位】")
            notes.extend(
                f"{i}. {x}"
                for i, x in enumerate(result.get("priorities"), 1)
            )

        self.image_review_notes.delete("1.0", "end")
        self.image_review_notes.insert(
            "1.0",
            "\n".join(notes)
        )
        self.image_review_ai_status.set(
            "AI評価をレビュー欄へ反映しました。保存はまだです。"
        )

    def apply_ai_image_review_prompt(self):
        result = getattr(self, "_image_review_ai_result", None)
        if not result:
            messagebox.showinfo(
                "画像解析",
                "先にAI解析を実行してください。"
            )
            return

        prompt = str(result.get("prompt_suggestion") or "").strip()
        negative = str(
            result.get("negative_prompt_suggestion") or ""
        ).strip()

        if not prompt and not negative:
            messagebox.showinfo(
                "画像解析",
                "AIからPrompt変更案は出ていません。"
            )
            return

        if not messagebox.askyesno(
            "Generateへ反映",
            "AIのPrompt / Negative修正案をGenerateへ反映しますか？\n\n"
            "Model・LoRA・Sampler・Steps・CFG・サイズは変更しません。"
        ):
            return

        if prompt:
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", prompt)
        if negative:
            self.negative.delete("1.0", "end")
            self.negative.insert("1.0", negative)

        self._quick_active_prompt_name = "画像AI解析"
        self._schedule_generate_dirty_check()
        self._refresh_generate_workflow_state()

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        self.status.set(
            "AI画像解析のPrompt修正案をGenerateへ反映しました。"
            "生成は開始していません。"
        )


    def _build_character(self):
        top = ttk.Frame(self.character)
        top.pack(fill="x")

        ttk.Label(top, text="検索").pack(side="left")
        self.char_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.char_search, width=28).pack(side="left", padx=(6, 12))

        self.char_fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="お気に入りのみ",
            variable=self.char_fav_only,
            command=self.refresh_character_list
        ).pack(side="left")

        ttk.Button(top, text="更新", command=self.refresh_character_list).pack(side="right")

        self.char_search.trace_add("write", lambda *_: self.refresh_character_list())

        pane, left, right = make_list_detail_pane(
            self.character,
            left_weight=2,
            right_weight=3,
            pady=(10, 6),
        )

        cols = ("fav", "status", "name", "model", "loras")
        self.char_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, title, width in [
            ("fav", "★", 40),
            ("status", "状態", 95),
            ("name", "キャラクター", 180),
            ("model", "モデル", 240),
            ("loras", "LoRA", 200),
        ]:
            self.char_tree.heading(col, text=title)
            self.char_tree.column(col, width=width)

        self.char_tree.pack(fill="both", expand=True)
        self.char_tree.bind("<<TreeviewSelect>>", self._char_show_selected)
        self.char_tree.bind("<Double-1>", lambda _e: self.char_apply_selected())

        form = make_detail_box(right, "Character設定", padding=8)

        self.char_name = tk.StringVar()
        self.char_model = tk.StringVar()
        self.char_loras = tk.StringVar()
        self.char_sampler = tk.StringVar()
        self.char_steps = tk.StringVar()
        self.char_cfg = tk.StringVar()
        self.char_width = tk.StringVar()
        self.char_height = tk.StringVar()
        self.char_apply_model_to_forge = tk.BooleanVar(value=False)
        self.char_apply_summary = tk.StringVar(value="Characterを選択してください。")

        char_tabs = ttk.Notebook(form)
        char_tabs.pack(fill="both", expand=True)

        basic_tab = ttk.Frame(char_tabs, padding=8)
        generation_tab = ttk.Frame(char_tabs, padding=8)
        char_tabs.add(basic_tab, text="基本")
        char_tabs.add(generation_tab, text="生成設定")

        # --- 基本タブ ---
        ttk.Label(basic_tab, text="名前").pack(anchor="w")
        ttk.Entry(
            basic_tab, textvariable=self.char_name
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(basic_tab, text="推奨モデル").pack(anchor="w")
        ttk.Entry(
            basic_tab, textvariable=self.char_model
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(
            basic_tab, text="LoRA（例: shiori4:0.8, detail:0.5）"
        ).pack(anchor="w")
        ttk.Entry(
            basic_tab, textvariable=self.char_loras
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(basic_tab, text="基本Prompt").pack(anchor="w")
        self.char_prompt = tk.Text(
            basic_tab, height=7, wrap="word"
        )
        self.char_prompt.pack(fill="both", expand=True, pady=(2, 8))

        ttk.Label(basic_tab, text="基本Negative").pack(anchor="w")
        self.char_negative = tk.Text(
            basic_tab, height=5, wrap="word"
        )
        self.char_negative.pack(fill="both", expand=True, pady=(2, 0))

        # --- 生成設定タブ ---
        settings = ttk.LabelFrame(generation_tab, text="生成設定", padding=8)
        settings.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(settings)
        row1.pack(fill="x")
        ttk.Label(row1, text="Sampler", width=10).pack(side="left")
        ttk.Entry(
            row1, textvariable=self.char_sampler, width=22
        ).pack(side="left", padx=(0, 10))
        ttk.Label(row1, text="Steps").pack(side="left")
        ttk.Entry(
            row1, textvariable=self.char_steps, width=7
        ).pack(side="left", padx=(6, 10))
        ttk.Label(row1, text="CFG").pack(side="left")
        ttk.Entry(
            row1, textvariable=self.char_cfg, width=7
        ).pack(side="left", padx=(6, 0))

        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Width", width=10).pack(side="left")
        ttk.Entry(
            row2, textvariable=self.char_width, width=8
        ).pack(side="left", padx=(0, 10))
        ttk.Label(row2, text="Height").pack(side="left")
        ttk.Entry(
            row2, textvariable=self.char_height, width=8
        ).pack(side="left", padx=(6, 0))

        ttk.Checkbutton(
            generation_tab,
            text="Character適用時にモデルもForgeへ切り替える",
            variable=self.char_apply_model_to_forge
        ).pack(anchor="w", pady=(0, 8))

        char_summary_box = ttk.LabelFrame(
            generation_tab, text="適用内容", padding=6
        )
        char_summary_box.pack(fill="x", pady=(0, 8))
        ttk.Label(
            char_summary_box,
            textvariable=self.char_apply_summary,
            justify="left",
            anchor="w"
        ).pack(fill="x")

        action_row = ttk.Frame(generation_tab)
        action_row.pack(fill="x")
        ttk.Button(
            action_row, text="現在の生成設定を取得",
            command=self.char_capture_current_generate
        ).pack(side="left")
        ttk.Button(
            action_row, text="PNGから取得",
            command=self.char_import_from_png
        ).pack(side="left", padx=6)
        ttk.Button(
            action_row, text="現在の生成設定で更新",
            command=self.char_update_from_current_generate
        ).pack(side="left")

        # Character全体の主要操作はタブ外の下部に固定。
        btns = ttk.Frame(form)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(
            btns, text="新規", command=self.char_clear_form
        ).pack(side="left")
        ttk.Button(
            btns, text="保存", command=self.char_save_current
        ).pack(side="left", padx=6)
        ttk.Button(
            btns, text="お気に入り切替",
            command=self.char_toggle_favorite
        ).pack(side="left")
        ttk.Button(
            btns, text="採用画像一覧",
            command=self.open_character_adopted_window
        ).pack(side="left", padx=6)
        ttk.Button(
            btns, text="生成タブへ適用",
            command=self.char_apply_selected
        ).pack(side="right")

        self.char_status = tk.StringVar(value="")
        ttk.Label(
            self.character, textvariable=self.char_status
        ).pack(anchor="w")

        self._char_selected_id = None
        self.refresh_character_list()

    def _char_items(self):
        return self.repo.list_items("characters")


    def _char_normalize_model_name(self, value):
        value = (value or "").strip().lower()
        value = re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", value, flags=re.IGNORECASE)
        value = value.replace(".safetensors", "").replace(".ckpt", "")
        return value.strip()

    def _quiet_refresh_model_catalog(self):
        """Quietly refresh Forge model titles before Character/Project matching."""
        try:
            api = self.api()
            models = api.list_models()
            titles = [x.get("title", "") for x in models if x.get("title")]
            if not titles:
                return False

            self.model_combo.configure(values=titles)

            try:
                opts = api.ping()
                current = (opts.get("sd_model_checkpoint", "") or "").strip()
            except Exception:
                current = ""

            if current:
                self.current_model_var.set(current)
                norm_current = self._char_normalize_model_name(current)
                for i, title in enumerate(titles):
                    if title == current or self._char_normalize_model_name(title) == norm_current:
                        self.model_combo.current(i)
                        break
            elif not self.model_combo.get().strip():
                self.model_combo.current(0)

            return True
        except Exception:
            return False

    def _char_available_model_titles(self):
        values = []
        try:
            values.extend([str(v) for v in self.model_combo["values"] if str(v).strip()])
        except Exception:
            pass
        try:
            current = self.current_model_var.get().strip()
            if current and current not in {"未取得", "(取得できず)"}:
                values.append(current)
        except Exception:
            pass
        # De-duplicate while preserving order.
        out = []
        seen = set()
        for v in values:
            if v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _char_find_model_match(self, saved_model):
        """Return (kind, title): exact / compatible / missing / unchecked."""
        saved_model = (saved_model or "").strip()
        if not saved_model:
            return ("missing", None)

        titles = self._char_available_model_titles()
        if not titles:
            return ("unchecked", None)

        if saved_model in titles:
            return ("exact", saved_model)

        norm_saved = self._char_normalize_model_name(saved_model)
        for title in titles:
            if self._char_normalize_model_name(title) == norm_saved:
                return ("compatible", title)

        return ("missing", None)

    def _char_missing_loras(self, loras):
        # If LoRA scan has not run yet, readiness is not determinable.
        if not getattr(self, "lora_items", None):
            return None

        available = {a.path.stem.lower() for a in self.lora_items}
        missing = []
        for x in loras or []:
            name = x.get("name") if isinstance(x, dict) else str(x)
            if name and name.lower() not in available:
                missing.append(name)
        return missing

    def _char_readiness(self, item):
        model_kind, _ = self._char_find_model_match(item.get("model") or "")
        missing_loras = self._char_missing_loras(item.get("loras") or [])

        if model_kind == "unchecked" or missing_loras is None:
            return "未確認"

        if model_kind == "missing":
            return "モデル不足"

        if missing_loras:
            return "一部不足"

        if model_kind == "compatible":
            return "要確認"

        return "準備完了"

    def refresh_character_list(self):
        if not hasattr(self, "char_tree"):
            return

        q = self.char_search.get().strip().lower()
        fav_only = self.char_fav_only.get()

        items = self._char_items()
        self.char_tree.delete(*self.char_tree.get_children())

        shown = 0
        for item in items:
            name = item.get("name") or ""
            model = item.get("model") or ""
            loras = item.get("loras") or []
            lora_text = ", ".join(
                f"{x.get('name')}:{x.get('weight', 1)}" if isinstance(x, dict) else str(x)
                for x in loras
            )

            hay = " ".join([name, model, lora_text]).lower()
            if q and q not in hay:
                continue
            if fav_only and not item.get("favorite", False):
                continue

            self.char_tree.insert(
                "", "end", iid=item.get("id"),
                values=(
                    "★" if item.get("favorite", False) else "",
                    self._char_readiness(item),
                    name,
                    model,
                    lora_text
                )
            )
            shown += 1

        self.char_status.set(f"表示 {shown} / 全 {len(items)} 件")

    def _char_selected_item(self):
        sel = self.char_tree.selection()
        if not sel:
            return None
        return self.repo.get_item("characters", sel[0])

    def _char_parse_loras(self, raw):
        out = []
        for part in (raw or "").split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                name, weight = part.rsplit(":", 1)
                name = name.strip()
                try:
                    weight_val = float(weight.strip())
                except Exception:
                    weight_val = 1.0
            else:
                name = part
                weight_val = 1.0
            if name:
                out.append({"name": name, "weight": weight_val})
        return out

    def _char_show_selected(self, _event=None):
        item = self._char_selected_item()
        if not item:
            return

        self._char_selected_id = item.get("id")
        self.char_name.set(item.get("name") or "")
        self.char_model.set(item.get("model") or "")

        loras = item.get("loras") or []
        self.char_loras.set(", ".join(
            f"{x.get('name')}:{x.get('weight', 1):g}" if isinstance(x, dict) else str(x)
            for x in loras
        ))

        self.char_prompt.delete("1.0", "end")
        self.char_prompt.insert("1.0", item.get("prompt") or "")
        self.char_negative.delete("1.0", "end")
        self.char_negative.insert("1.0", item.get("negative_prompt") or "")
        self._refresh_character_apply_summary(item)

        gen = item.get("generation") or {}
        self.char_sampler.set(str(gen.get("sampler") or ""))
        self.char_steps.set(str(gen.get("steps") or ""))
        self.char_cfg.set(str(gen.get("cfg") or ""))
        self.char_width.set(str(gen.get("width") or ""))
        self.char_height.set(str(gen.get("height") or ""))

    def open_character_adopted_window(self):
        item = self._char_selected_item()
        if not item:
            messagebox.showinfo("Character", "Characterを選択してください。")
            return

        character_id = str(item.get("id") or "")
        character_name = item.get("name") or item.get("display_name") or "Character"

        adopted_items = [
            x for x in self.repo.list_items("adopted")
            if str(x.get("character_id") or "") == character_id
        ]
        adopted_items = sorted(
            adopted_items,
            key=lambda x: str(x.get("adopted_at") or x.get("created_at") or ""),
            reverse=True,
        )

        win = tk.Toplevel(self)
        win.title(f"{character_name} - 採用画像")
        win.geometry("1100x620")
        win.transient(self)

        top = ttk.Frame(win, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(
            top,
            text=f"{character_name} の採用画像",
            font=("", 13, "bold")
        ).pack(side="left")
        count_var = tk.StringVar(value=f"{len(adopted_items)}件")
        ttk.Label(top, textvariable=count_var).pack(side="right")

        body = ttk.Panedwindow(win, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        left = ttk.Frame(body)
        right = ttk.LabelFrame(body, text="プレビュー", padding=10)
        body.add(left, weight=2)
        body.add(right, weight=3)

        cols = ("master", "adopted_at", "project", "name", "image")
        tree = ttk.Treeview(
            left, columns=cols, show="headings", selectmode="browse"
        )
        for col, title, width in [
            ("master", "Master", 80),
            ("adopted_at", "採用日時", 145),
            ("project", "Project", 140),
            ("name", "正式名称", 160),
            ("image", "画像", 240),
        ]:
            tree.heading(col, text=title)
            tree.column(col, width=width)

        scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        preview_label = ttk.Label(
            right, text="画像を選択してください", anchor="center"
        )
        preview_label.pack(fill="both", expand=True)

        info_text = tk.Text(
            right, height=10, wrap="word", state="disabled"
        )
        info_text.pack(fill="x", pady=(8, 0))

        preview_ref = {"image": None}

        records = {}
        for adopted in adopted_items:
            iid = str(adopted.get("id") or "")
            if not iid:
                continue
            records[iid] = adopted
            project_name = self._adopted_entity_name(
                "projects", adopted.get("project_id") or ""
            )
            tree.insert(
                "", "end", iid=iid,
                values=(
                    adopted.get("master_type") or "",
                    adopted.get("adopted_at") or "",
                    project_name,
                    adopted.get("name") or Path(adopted.get("image_path") or "").name,
                    Path(adopted.get("image_path") or "").name,
                )
            )

        def _show_preview(_event=None):
            sel = tree.selection()
            if not sel:
                preview_ref["image"] = None
                preview_label.configure(image="", text="画像を選択してください")
                info_text.configure(state="normal")
                info_text.delete("1.0", "end")
                info_text.configure(state="disabled")
                return

            adopted = records.get(sel[0])
            if not adopted:
                return

            path = Path(adopted.get("image_path") or "")
            if not path.exists():
                preview_ref["image"] = None
                preview_label.configure(image="", text="画像ファイルが見つかりません")
            else:
                try:
                    img = tk.PhotoImage(file=str(path))
                    w, h = img.width(), img.height()
                    max_w, max_h = 460, 360
                    factor = max(
                        1,
                        (w + max_w - 1) // max_w,
                        (h + max_h - 1) // max_h
                    )
                    if factor > 1:
                        img = img.subsample(factor, factor)
                    preview_ref["image"] = img
                    preview_label.configure(image=img, text="")
                except Exception:
                    preview_ref["image"] = None
                    preview_label.configure(
                        image="", text=f"プレビューを表示できません\n{path.name}"
                    )

            project_name = self._adopted_entity_name(
                "projects", adopted.get("project_id") or ""
            )
            tags = adopted.get("tags") or []
            if isinstance(tags, list):
                tags = ", ".join(str(x) for x in tags)

            display_name = adopted.get("name") or path.name
            lines = [
                f"正式名称: {display_name}",
                f"Master: {adopted.get('master_type') or ''}",
                f"Project: {project_name}",
                f"採用日: {adopted.get('adopted_at') or ''}",
                f"タグ: {tags}",
                "",
                "メモ:",
                adopted.get("notes") or "",
            ]

            info_text.configure(state="normal")
            info_text.delete("1.0", "end")
            info_text.insert("1.0", "\n".join(lines))
            info_text.configure(state="disabled")

        def _open_selected(_event=None):
            sel = tree.selection()
            if not sel:
                return
            adopted = records.get(sel[0])
            if not adopted:
                return
            path = Path(adopted.get("image_path") or "")
            if not path.exists():
                messagebox.showwarning(
                    "Character",
                    f"画像ファイルが見つかりません。\n\n{path}",
                    parent=win
                )
                return
            try:
                os.startfile(str(path))
            except Exception as e:
                messagebox.showerror(
                    "Character",
                    f"画像を開けませんでした。\n{e}",
                    parent=win
                )

        tree.bind("<<TreeviewSelect>>", _show_preview)
        tree.bind("<Double-1>", _open_selected)

        def _restore_selected_to_generate():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo(
                    "Character",
                    "Generateへ復元する採用画像を選択してください。",
                    parent=win
                )
                return

            adopted = records.get(sel[0])
            if not adopted:
                return

            try:
                self._restore_adopted_to_generate(adopted)
            except Exception as e:
                messagebox.showerror(
                    "Character",
                    f"Generateへの復元に失敗しました。\n{e}",
                    parent=win
                )
                return

            win.destroy()

        def _set_master():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo(
                    "Character",
                    "マスターに設定する採用画像を選択してください。",
                    parent=win
                )
                return
            adopted = records.get(sel[0])
            if not adopted:
                return

            master_type = master_type_var.get().strip()
            if not master_type:
                messagebox.showinfo(
                    "Character",
                    "Master種別を選択してください。",
                    parent=win
                )
                return

            # Keep only one master per type for this Character.
            try:
                for current in self.repo.list_items("adopted"):
                    if (
                        str(current.get("character_id") or "") == character_id
                        and current.get("master_type") == master_type
                        and current.get("id") != adopted.get("id")
                    ):
                        updated_current = dict(current)
                        updated_current["master_type"] = ""
                        self.repo.upsert_item("adopted", updated_current)

                updated = dict(adopted)
                updated["master_type"] = master_type
                saved = self.repo.upsert_item("adopted", updated)
                records[saved.get("id") or sel[0]] = saved
            except Exception as e:
                messagebox.showerror(
                    "Character",
                    f"マスター設定に失敗しました。\n{e}",
                    parent=win
                )
                return

            tree.set(sel[0], "master", master_type)
            _show_preview()
            messagebox.showinfo(
                "Character",
                f"{master_type}マスターに設定しました。",
                parent=win
            )

        def _clear_master():
            sel = tree.selection()
            if not sel:
                messagebox.showinfo(
                    "Character",
                    "マスター解除する画像を選択してください。",
                    parent=win
                )
                return
            adopted = records.get(sel[0])
            if not adopted:
                return
            if not adopted.get("master_type"):
                messagebox.showinfo(
                    "Character",
                    "この画像はマスター設定されていません。",
                    parent=win
                )
                return
            try:
                updated = dict(adopted)
                updated["master_type"] = ""
                saved = self.repo.upsert_item("adopted", updated)
                records[saved.get("id") or sel[0]] = saved
            except Exception as e:
                messagebox.showerror(
                    "Character",
                    f"マスター解除に失敗しました。\n{e}",
                    parent=win
                )
                return
            tree.set(sel[0], "master", "")
            _show_preview()
            messagebox.showinfo(
                "Character",
                "マスター設定を解除しました。",
                parent=win
            )

        # Keep all actions in a fixed top toolbar so preview size can never push
        # the controls below the visible window.
        buttons = ttk.Frame(win, padding=(10, 4, 10, 6))
        buttons.pack(fill="x", before=body)

        ttk.Label(buttons, text="Master").pack(side="left")
        master_type_var = tk.StringVar(value="正面")
        ttk.Combobox(
            buttons,
            textvariable=master_type_var,
            state="readonly",
            width=10,
            values=("正面", "横顔", "全身", "その他"),
        ).pack(side="left", padx=(6, 6))
        ttk.Button(
            buttons,
            text="マスター設定",
            command=_set_master
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="マスター解除",
            command=_clear_master
        ).pack(side="left", padx=(6, 12))
        ttk.Button(
            buttons,
            text="Generateへ復元",
            command=_restore_selected_to_generate
        ).pack(side="left")

        ttk.Button(buttons, text="閉じる", command=win.destroy).pack(side="right")
        ttk.Button(
            buttons, text="画像を開く",
            command=_open_selected
        ).pack(side="right", padx=(0, 6))

    def _restore_adopted_to_generate(self, adopted):
        """Restore safe generation settings from an adopted record."""
        prompt_text = adopted.get("prompt") or ""
        negative_text = adopted.get("negative_prompt") or ""

        # Restore managed LoRA state first, then keep visible prompt clean.
        try:
            self._import_loras_from_prompt(prompt_text)
        except Exception:
            pass

        stored_loras = adopted.get("loras") or []
        if stored_loras:
            try:
                self.active_loras.clear()
                for x in stored_loras:
                    if not isinstance(x, dict) or not x.get("name"):
                        continue
                    try:
                        weight = float(x.get("weight", 1.0))
                    except Exception:
                        weight = 1.0
                    self.active_loras[x["name"]] = weight
            except Exception:
                pass

        try:
            visible_prompt = self._clean_prompt_lora_tags(prompt_text)
        except Exception:
            visible_prompt = prompt_text

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", visible_prompt)

        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", negative_text)

        try:
            self._refresh_lora_tree()
        except Exception:
            pass

        # Basic generation controls.
        for key, var_name, caster in [
            ("steps", "steps", int),
            ("cfg", "cfg", float),
            ("width", "width", int),
            ("height", "height", int),
        ]:
            value = adopted.get(key, "")
            if value in ("", None):
                continue
            try:
                getattr(self, var_name).set(caster(value))
            except Exception:
                pass

        sampler = (adopted.get("sampler") or "").strip()
        if sampler:
            try:
                self.sampler.set(sampler)
            except Exception:
                pass

        # Restore seed only when the current Generate UI exposes a seed variable.
        seed_value = adopted.get("seed", "")
        if seed_value not in ("", None):
            for attr_name in ("seed", "seed_var", "generate_seed", "seed_value"):
                obj = getattr(self, attr_name, None)
                if obj is None or not hasattr(obj, "set"):
                    continue
                try:
                    obj.set(int(seed_value))
                    break
                except Exception:
                    try:
                        obj.set(str(seed_value))
                        break
                    except Exception:
                        pass

        # Select matching model in Studio UI. Do not force a Forge model switch here.
        model = (adopted.get("model") or "").strip()
        if model:
            try:
                values = list(self.model_combo["values"])
                norm_model = self._char_normalize_model_name(model)
                for i, title in enumerate(values):
                    if title == model or self._char_normalize_model_name(title) == norm_model:
                        self.model_combo.current(i)
                        break
            except Exception:
                pass

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        image_name = Path(adopted.get("image_path") or "").name
        self.status.set(
            f"採用画像「{image_name}」の生成設定をGenerateへ復元しました。生成は開始していません。"
        )

    def _refresh_character_apply_summary(self, item=None):
        if not hasattr(self, "char_apply_summary"):
            return

        if item is None:
            try:
                item = self._char_selected_item()
            except Exception:
                item = None

        if not item:
            self.char_apply_summary.set("Characterを選択してください。")
            return

        gen = item.get("generation") or {}
        loras = []
        for entry in item.get("loras") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            try:
                weight = float(entry.get("weight", 1.0))
                loras.append(f"{entry['name']}:{weight:g}")
            except Exception:
                loras.append(str(entry.get("name")))

        lines = [
            f"Model: {item.get('model') or '未設定'}",
            f"LoRA: {', '.join(loras) if loras else 'なし'}",
            "生成設定: "
            + " / ".join([
                f"Sampler={gen.get('sampler') or '未設定'}",
                f"Steps={gen.get('steps') if gen.get('steps') is not None else '未設定'}",
                f"CFG={gen.get('cfg') if gen.get('cfg') is not None else '未設定'}",
                f"Size={gen.get('width') or '?'}x{gen.get('height') or '?'}",
            ]),
            f"Prompt: {'あり' if (item.get('prompt') or '').strip() else '未設定'}"
            f" / Negative: {'あり' if (item.get('negative_prompt') or '').strip() else '未設定'}",
        ]
        self.char_apply_summary.set("\n".join(lines))

    def char_clear_form(self):
        self._char_selected_id = None
        self.char_name.set("")
        self.char_model.set("")
        self.char_loras.set("")
        self.char_prompt.delete("1.0", "end")
        self.char_negative.delete("1.0", "end")
        self.char_sampler.set("")
        self.char_steps.set("")
        self.char_cfg.set("")
        self.char_width.set("")
        self.char_height.set("")
        self.char_tree.selection_remove(self.char_tree.selection())
        if hasattr(self, "char_apply_summary"):
            self.char_apply_summary.set("Characterを選択してください。")
        self.char_status.set("新規登録モード")


    def _char_fill_from_current_generate(self, preserve_name=True):
        """Copy current Generate tab state into the Character form."""
        current_name = self.char_name.get().strip() if preserve_name else ""

        # Current model:
        # 1) Prefer the model currently reported by Forge / UI status
        # 2) Fallback to Studio's model selector
        model_value = ""

        # Common StringVar names used by the app for the active/current model.
        for attr_name in (
            "current_model_var",
            "current_model",
            "model_status_var",
            "selected_model_var",
            "model_var",
        ):
            try:
                obj = getattr(self, attr_name, None)
                if obj is None:
                    continue
                value = obj.get() if hasattr(obj, "get") else obj
                value = str(value).strip()
                if value and value.lower() not in {"none", "未取得", "未選択"}:
                    model_value = value
                    break
            except Exception:
                pass

        # If the model combobox itself has a visible value, use it as fallback.
        if not model_value:
            try:
                value = str(self.model_combo.get()).strip()
                if value:
                    model_value = value
            except Exception:
                pass

        self.char_model.set(model_value)

        # Managed LoRA state
        try:
            lora_text = ", ".join(
                f"{name}:{float(weight):g}"
                for name, weight in self.active_loras.items()
            )
        except Exception:
            lora_text = ""
        self.char_loras.set(lora_text)

        # Prompt / Negative
        try:
            prompt_text = self.prompt.get("1.0", "end").strip()
        except Exception:
            prompt_text = ""
        try:
            negative_text = self.negative.get("1.0", "end").strip()
        except Exception:
            negative_text = ""

        self.char_prompt.delete("1.0", "end")
        self.char_prompt.insert("1.0", prompt_text)
        self.char_negative.delete("1.0", "end")
        self.char_negative.insert("1.0", negative_text)

        # Generation settings
        try:
            self.char_sampler.set(str(self.sampler.get()).strip())
        except Exception:
            self.char_sampler.set("")
        try:
            self.char_steps.set(str(self.steps.get()))
        except Exception:
            self.char_steps.set("")
        try:
            self.char_cfg.set(str(self.cfg.get()))
        except Exception:
            self.char_cfg.set("")
        try:
            self.char_width.set(str(self.width.get()))
        except Exception:
            self.char_width.set("")
        try:
            self.char_height.set(str(self.height.get()))
        except Exception:
            self.char_height.set("")

        if preserve_name and current_name:
            self.char_name.set(current_name)

    def char_capture_current_generate(self):
        self._char_fill_from_current_generate(preserve_name=True)
        model_note = self.char_model.get().strip() or "未取得"
        self.char_status.set(
            f"現在の生成設定を取得しました。モデル: {model_note}。まだ保存していません。"
        )

    def char_import_from_png(self):
        path = filedialog.askopenfilename(
            title="Character設定の元にするPNGを選択",
            filetypes=[
                ("PNG画像", "*.png"),
                ("すべてのファイル", "*.*"),
            ],
        )
        if not path:
            return

        try:
            meta = self._pl_collect_png_metadata(path)
        except Exception as e:
            messagebox.showerror("Character PNG読込", f"PNGを読み込めませんでした。\n{e}")
            return

        parsed = meta.get("parsed") or {}
        settings = parsed.get("settings") or {}
        loras = parsed.get("loras") or []

        if not meta.get("parameters_found"):
            messagebox.showinfo(
                "Character PNG読込",
                "PNGは読めましたが、Forge/A1111形式の生成情報が見つかりませんでした。"
            )
            return

        # Keep current Character name if already entered.
        current_name = self.char_name.get().strip()

        self.char_prompt.delete("1.0", "end")
        self.char_prompt.insert("1.0", parsed.get("prompt") or "")
        self.char_negative.delete("1.0", "end")
        self.char_negative.insert("1.0", parsed.get("negative_prompt") or "")

        if settings.get("Model"):
            self.char_model.set(settings["Model"])

        self.char_loras.set(", ".join(
            f"{x.get('name')}:{x.get('weight', 1)}"
            for x in loras if x.get("name")
        ))

        if settings.get("Sampler"):
            self.char_sampler.set(settings["Sampler"])
        if settings.get("Steps"):
            self.char_steps.set(settings["Steps"])
        if settings.get("CFG scale"):
            self.char_cfg.set(settings["CFG scale"])

        size = settings.get("Size") or ""
        if "x" in size.lower():
            parts = re.split(r"[xX]", size)
            if len(parts) == 2:
                self.char_width.set(parts[0].strip())
                self.char_height.set(parts[1].strip())
        else:
            if meta.get("width"):
                self.char_width.set(str(meta["width"]))
            if meta.get("height"):
                self.char_height.set(str(meta["height"]))

        if current_name:
            self.char_name.set(current_name)

        self.char_status.set(
            f"PNGからCharacter設定を取得しました: {Path(path).name}。まだ保存していません。"
        )

    def char_update_from_current_generate(self):
        item = self._char_selected_item()
        if not item:
            messagebox.showinfo(
                "Character",
                "更新するCharacterを左の一覧から選択してください。"
            )
            return

        # Keep identity + favorite/notes/master refs, replace production settings.
        self._char_selected_id = item.get("id")
        self.char_name.set(item.get("name") or "")
        self._char_fill_from_current_generate(preserve_name=True)

        favorite = bool(item.get("favorite", False))
        notes = item.get("notes") or ""
        master_refs = item.get("master_refs") or []

        def parse_num(value, kind="float"):
            value = str(value).strip()
            if not value:
                return None
            try:
                return int(float(value)) if kind == "int" else float(value)
            except Exception:
                return None

        updated = {
            "id": item.get("id"),
            "name": item.get("name") or self.char_name.get().strip(),
            "model": self.char_model.get().strip(),
            "loras": self._char_parse_loras(self.char_loras.get()),
            "prompt": self.char_prompt.get("1.0", "end").strip(),
            "negative_prompt": self.char_negative.get("1.0", "end").strip(),
            "generation": {
                "sampler": self.char_sampler.get().strip(),
                "steps": parse_num(self.char_steps.get(), "int"),
                "cfg": parse_num(self.char_cfg.get(), "float"),
                "width": parse_num(self.char_width.get(), "int"),
                "height": parse_num(self.char_height.get(), "int"),
            },
            "favorite": favorite,
            "notes": notes,
            "master_refs": master_refs,
        }

        saved = self.repo.upsert_item("characters", updated)
        self._char_selected_id = saved.get("id")
        self.refresh_character_list()
        if self._char_selected_id in self.char_tree.get_children():
            self.char_tree.selection_set(self._char_selected_id)
            self.char_tree.focus(self._char_selected_id)
        self._char_show_selected()
        self.char_status.set(
            f"現在の生成設定でCharacterを更新しました: {updated.get('name','')}"
        )
        if hasattr(self, "core_tree"):
            self.refresh_core_status()

    def char_save_current(self):
        name = self.char_name.get().strip()
        if not name:
            messagebox.showinfo("Character", "キャラクター名を入力してください。")
            return

        favorite = False
        notes = ""
        if self._char_selected_id:
            old = self.repo.get_item("characters", self._char_selected_id)
            if old:
                favorite = bool(old.get("favorite", False))
                notes = old.get("notes") or ""

        def parse_num(value, kind="float"):
            value = str(value).strip()
            if not value:
                return None
            try:
                return int(value) if kind == "int" else float(value)
            except Exception:
                return None

        master_refs = []
        if self._char_selected_id:
            old_item = self.repo.get_item("characters", self._char_selected_id)
            if old_item:
                master_refs = old_item.get("master_refs") or []

        item = {
            "id": self._char_selected_id or "",
            "name": name,
            "model": self.char_model.get().strip(),
            "loras": self._char_parse_loras(self.char_loras.get()),
            "prompt": self.char_prompt.get("1.0", "end").strip(),
            "negative_prompt": self.char_negative.get("1.0", "end").strip(),
            "generation": {
                "sampler": self.char_sampler.get().strip(),
                "steps": parse_num(self.char_steps.get(), "int"),
                "cfg": parse_num(self.char_cfg.get(), "float"),
                "width": parse_num(self.char_width.get(), "int"),
                "height": parse_num(self.char_height.get(), "int"),
            },
            "favorite": favorite,
            "notes": notes,
            "master_refs": master_refs,
        }

        saved = self.repo.upsert_item("characters", item)
        self._char_selected_id = saved.get("id")
        self.refresh_character_list()

        if self._char_selected_id in self.char_tree.get_children():
            self.char_tree.selection_set(self._char_selected_id)
            self.char_tree.focus(self._char_selected_id)

        self.char_status.set(f"保存しました: {name}")
        if hasattr(self, "core_tree"):
            self.refresh_core_status()

    def char_toggle_favorite(self):
        item = self._char_selected_item()
        if not item:
            messagebox.showinfo("Character", "お気に入りを切り替えるキャラクターを選択してください。")
            return
        item["favorite"] = not bool(item.get("favorite", False))
        self.repo.upsert_item("characters", item)
        self.refresh_character_list()
        if item["id"] in self.char_tree.get_children():
            self.char_tree.selection_set(item["id"])
            self.char_tree.focus(item["id"])
        self.char_status.set("お気に入りを更新しました")


    def _char_select_model_in_combo(self, title):
        if not title:
            return False
        try:
            values = list(self.model_combo["values"])
            for i, candidate in enumerate(values):
                if candidate == title:
                    self.model_combo.current(i)
                    return True
            self.model_combo.set(title)
            return True
        except Exception:
            return False

    def _char_apply_model_to_forge(self, title, character_name):
        def work():
            api = self.api()
            api.set_model(title)
            opts = api.ping()
            current = opts.get("sd_model_checkpoint", title)
            self.after(0, lambda: self.current_model_var.set(current))
            self.after(0, self.refresh_character_list)
            self.after(0, self._refresh_generate_workflow_state)
        self._bg(work, f"Character「{character_name}」のモデルをForgeへ適用しました")

    def char_apply_selected(self):
        item = self._char_selected_item()
        if not item:
            messagebox.showinfo("Character", "生成タブへ適用するキャラクターを選択してください。")
            return

        name = item.get("name") or ""
        saved_model = item.get("model") or ""

        if saved_model and not self._char_available_model_titles():
            self._quiet_refresh_model_catalog()

        model_kind, model_title = self._char_find_model_match(saved_model)

        # Resolve model availability safely before applying anything.
        if saved_model:
            if model_kind == "compatible":
                if messagebox.askyesno(
                    "モデル候補",
                    "保存されているモデルと完全一致するものはありませんが、"
                    "同名の候補が見つかりました。\n\n"
                    f"保存: {saved_model}\n"
                    f"候補: {model_title}\n\n"
                    "この候補を使用しますか？\n"
                    "「はい」を選ぶと、このCharacterの保存モデル名も候補の正式表記へ更新します。"
                ):
                    # Learn the approved exact Forge model title so the same prompt does not repeat.
                    if model_title and model_title != saved_model:
                        learned = dict(item)
                        learned["model"] = model_title
                        learned = self.repo.upsert_item("characters", learned)
                        item = learned
                        saved_model = model_title
                        self._char_selected_id = learned.get("id")
                        self.refresh_character_list()
                        if self._char_selected_id in self.char_tree.get_children():
                            self.char_tree.selection_set(self._char_selected_id)
                            self.char_tree.focus(self._char_selected_id)
                        self._char_show_selected()
                        self.char_status.set(
                            f"承認したモデル候補をCharacterへ保存しました: {model_title}"
                        )
                        if hasattr(self, "core_tree"):
                            self.refresh_core_status()
                else:
                    model_title = None

            elif model_kind == "missing":
                if not messagebox.askyesno(
                    "モデルが見つかりません",
                    f"Character「{name}」のモデルがForge側に見つかりません。\n\n"
                    f"{saved_model}\n\n"
                    "モデルをスキップして、その他の設定だけ適用しますか？"
                ):
                    return
                model_title = None

            elif model_kind == "unchecked":
                if not messagebox.askyesno(
                    "モデル未確認",
                    "Forgeのモデル一覧を自動取得できなかったため、"
                    "保存モデルの存在を確認できません。\n\n"
                    "モデルをスキップして、その他の設定だけ適用しますか？"
                ):
                    return
                model_title = None

        # Prompt / Negative
        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", item.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", item.get("negative_prompt") or "")

        # Model selector and optional Forge switch.
        if model_title:
            self._char_select_model_in_combo(model_title)

        # Managed LoRA state. Missing files are skipped rather than invented.
        available_loras = {a.path.stem.lower(): a.path.stem for a in getattr(self, "lora_items", [])}
        missing_loras = []
        self.active_loras.clear()
        for x in item.get("loras") or []:
            if not isinstance(x, dict) or not x.get("name"):
                continue
            requested = x["name"]
            actual = available_loras.get(requested.lower())
            if getattr(self, "lora_items", None) and not actual:
                missing_loras.append(requested)
                continue
            try:
                weight = float(x.get("weight", 1.0))
            except Exception:
                weight = 1.0
            self.active_loras[actual or requested] = weight
        self._refresh_lora_tree()

        # Generation settings.
        gen = item.get("generation") or {}
        try:
            if gen.get("sampler"):
                self.sampler.set(gen["sampler"])
        except Exception:
            pass
        try:
            if gen.get("steps") is not None:
                self.steps.set(int(gen["steps"]))
        except Exception:
            pass
        try:
            if gen.get("cfg") is not None:
                self.cfg.set(float(gen["cfg"]))
        except Exception:
            pass
        try:
            if gen.get("width") is not None:
                self.width.set(int(gen["width"]))
        except Exception:
            pass
        try:
            if gen.get("height") is not None:
                self.height.set(int(gen["height"]))
        except Exception:
            pass

        # Register this Character as the active production Character.
        self.active_character_id = item.get("id") or ""
        try:
            ws = self.repo.workspace()
            ws["current_character_id"] = self.active_character_id
            ws["active_character_id"] = self.active_character_id
            self.repo.save_all("workspace", ws)
        except Exception:
            pass

        self._quick_active_character_name = name or "未選択"
        if hasattr(self, "quick_character"):
            self.quick_character.set(name or "未選択")

        if hasattr(self, "quick_active_summary"):
            project_name = getattr(self, "_quick_active_project_name", "未選択")
            prompt_name = getattr(self, "_quick_active_prompt_name", "未選択")
            self.quick_active_summary.set(
                f"現在適用中: Project={project_name} / "
                f"Character={self._quick_active_character_name} / Prompt={prompt_name}"
            )

        self._refresh_lora_tree()
        self._refresh_model_tree()
        if hasattr(self, "prompt_builder_combos"):
            self._refresh_prompt_builder_choices()
        self._refresh_generate_workflow_state()
        self.refresh_production_check()
        self._schedule_generate_dirty_check()

        notes = []
        if model_title:
            notes.append("Model")
        elif saved_model:
            notes.append("Modelスキップ")
        if self.active_loras:
            notes.append(f"LoRA {len(self.active_loras)}件")
        if (item.get("prompt") or "").strip():
            notes.append("Prompt")
        if (item.get("negative_prompt") or "").strip():
            notes.append("Negative")
        if gen:
            notes.append("生成設定")
        if missing_loras:
            notes.append("不足LoRA: " + ", ".join(missing_loras))

        self.char_status.set(
            f"Generateへ適用済み: {name}"
            + (" / " + " / ".join(notes) if notes else "")
        )
        self.status.set(
            f"Character「{name}」の制作設定をGenerateへ適用しました。"
            + (" / " + " / ".join(notes) if notes else "")
            + "。生成は開始していません。"
        )

        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

        # Only switch Forge model when explicitly enabled.
        if (
            model_title
            and hasattr(self, "char_apply_model_to_forge")
            and self.char_apply_model_to_forge.get()
        ):
            current = ""
            try:
                current = self.current_model_var.get().strip()
            except Exception:
                pass
            if self._char_normalize_model_name(current) != self._char_normalize_model_name(model_title):
                self._char_apply_model_to_forge(model_title, name)



    def _build_project(self):
        top = ttk.Frame(self.project)
        top.pack(fill="x")

        ttk.Label(top, text="検索").pack(side="left")
        self.project_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.project_search, width=28).pack(side="left", padx=(6, 12))

        ttk.Label(top, text="状態").pack(side="left")
        self.project_status_filter = tk.StringVar(value="すべて")
        self.project_status_combo = ttk.Combobox(
            top, textvariable=self.project_status_filter, state="readonly", width=14
        )
        self.project_status_combo["values"] = ("すべて", "進行中", "保留", "完了")
        self.project_status_combo.pack(side="left", padx=(6, 12))
        self.project_status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_project_list())

        self.project_fav_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="お気に入りのみ",
            variable=self.project_fav_only,
            command=self.refresh_project_list
        ).pack(side="left")

        ttk.Button(top, text="更新", command=self.refresh_project_list).pack(side="right")
        self.project_search.trace_add("write", lambda *_: self.refresh_project_list())

        pane, left, right = make_list_detail_pane(
            self.project,
            left_weight=2,
            right_weight=3,
            pady=(10, 6),
        )

        cols = ("fav", "status", "name", "character", "output")
        self.project_tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for col, title, width in [
            ("fav", "★", 40),
            ("status", "状態", 80),
            ("name", "Project", 220),
            ("character", "Character", 170),
            ("output", "保存先", 300),
        ]:
            self.project_tree.heading(col, text=title)
            self.project_tree.column(col, width=width)
        self.project_tree.pack(fill="both", expand=True)
        self.project_tree.bind("<<TreeviewSelect>>", self._project_show_selected)
        self.project_tree.bind("<Double-1>", lambda _e: self.project_open_selected())

        form = make_detail_box(right, "Project設定", padding=8)

        self.project_name = tk.StringVar()
        self.project_character = tk.StringVar()
        self.project_output_dir = tk.StringVar()
        self.project_state = tk.StringVar(value="進行中")
        self.project_notes = tk.StringVar()

        ttk.Label(form, text="Project名").pack(anchor="w")
        ttk.Entry(form, textvariable=self.project_name).pack(fill="x", pady=(2, 8))

        ttk.Label(form, text="Character").pack(anchor="w")
        char_row = ttk.Frame(form)
        char_row.pack(fill="x", pady=(2, 8))
        self.project_character_combo = ttk.Combobox(
            char_row, textvariable=self.project_character, state="readonly"
        )
        self.project_character_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(
            char_row, text="再読込", command=self._project_refresh_character_choices
        ).pack(side="left", padx=(6, 0))

        ttk.Label(form, text="保存先").pack(anchor="w")
        out_row = ttk.Frame(form)
        out_row.pack(fill="x", pady=(2, 8))
        ttk.Entry(out_row, textvariable=self.project_output_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="選択", command=self.project_choose_output_dir).pack(side="left", padx=(6, 0))

        ttk.Label(form, text="状態").pack(anchor="w")
        ttk.Combobox(
            form,
            textvariable=self.project_state,
            state="readonly",
            values=("進行中", "保留", "完了")
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(form, text="メモ").pack(anchor="w")
        self.project_notes_text = tk.Text(form, height=6, wrap="word")
        self.project_notes_text.pack(fill="both", expand=True, pady=(2, 8))

        project_status_box = ttk.LabelFrame(form, text="制作状況", padding=8)
        project_status_box.pack(fill="x", pady=(0, 8))
        self.project_session_saved_at = tk.StringVar(value="最終セッション: 未保存")
        self.project_generated_count = tk.StringVar(value="生成枚数: 0")
        self.project_adopted_count = tk.StringVar(value="採用枚数: 0")
        self.project_active_character = tk.StringVar(value="Character: 未設定")
        self.project_last_prompt = tk.StringVar(value="最後のPrompt: 未設定")

        for var in (
            self.project_session_saved_at,
            self.project_generated_count,
            self.project_adopted_count,
            self.project_active_character,
            self.project_last_prompt,
        ):
            ttk.Label(
                project_status_box, textvariable=var, anchor="w"
            ).pack(fill="x")

        action = ttk.Frame(form)
        action.pack(fill="x", pady=(4, 0))
        ttk.Button(action, text="新規", command=self.project_clear_form).pack(side="left")
        ttk.Button(action, text="保存", command=self.project_save_current).pack(side="left", padx=6)
        ttk.Button(action, text="お気に入り切替", command=self.project_toggle_favorite).pack(side="left")
        ttk.Button(
            action, text="採用画像一覧",
            command=self.open_project_adopted_window
        ).pack(side="left", padx=6)
        ttk.Button(action, text="Projectを開く", command=self.project_open_selected).pack(side="right")

        self.project_status_var = tk.StringVar(value="")
        ttk.Label(self.project, textvariable=self.project_status_var).pack(anchor="w")

        self._project_selected_id = None
        self._project_refresh_character_choices()
        self.refresh_project_list()

    def _project_items(self):
        return self.repo.list_items("projects")

    def _project_refresh_character_choices(self):
        if not hasattr(self, "project_character_combo"):
            return
        items = self.repo.list_items("characters")
        names = [x.get("name") for x in items if x.get("name")]
        self.project_character_combo["values"] = names
        current = self.project_character.get().strip()
        if current and current not in names:
            self.project_character.set("")

    def refresh_project_list(self):
        if not hasattr(self, "project_tree"):
            return

        self._project_refresh_character_choices()

        q = self.project_search.get().strip().lower()
        state_filter = self.project_status_filter.get()
        fav_only = self.project_fav_only.get()

        items = self._project_items()
        self.project_tree.delete(*self.project_tree.get_children())

        shown = 0
        for item in items:
            name = item.get("name") or ""
            character_name = item.get("character_name") or ""
            output_dir = item.get("output_dir") or ""
            state = item.get("state") or "進行中"
            hay = " ".join([name, character_name, output_dir, item.get("notes") or ""]).lower()

            if q and q not in hay:
                continue
            if state_filter != "すべて" and state != state_filter:
                continue
            if fav_only and not item.get("favorite", False):
                continue

            self.project_tree.insert(
                "", "end", iid=item.get("id"),
                values=(
                    "★" if item.get("favorite", False) else "",
                    state,
                    name,
                    character_name,
                    output_dir,
                )
            )
            shown += 1

        self.project_status_var.set(f"表示 {shown} / 全 {len(items)} 件")

    def _project_selected_item(self):
        sel = self.project_tree.selection()
        if not sel:
            return None
        return self.repo.get_item("projects", sel[0])

    def open_project_adopted_window(self):
        item = self._project_selected_item()
        if not item:
            messagebox.showinfo("Project", "Projectを選択してください。")
            return

        project_id = str(item.get("id") or "")
        project_name = item.get("name") or "Project"

        adopted_items = [
            x for x in self.repo.list_items("adopted")
            if str(x.get("project_id") or "") == project_id
        ]
        adopted_items = sorted(
            adopted_items,
            key=lambda x: str(x.get("adopted_at") or x.get("created_at") or ""),
            reverse=True,
        )

        win = tk.Toplevel(self)
        win.title(f"{project_name} - 採用画像")
        win.geometry("1100x620")
        win.transient(self)

        top = ttk.Frame(win, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(
            top,
            text=f"{project_name} の採用画像",
            font=("", 13, "bold")
        ).pack(side="left")
        ttk.Label(top, text=f"{len(adopted_items)}件").pack(side="right")

        body = ttk.Panedwindow(win, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        left = ttk.Frame(body)
        right = ttk.LabelFrame(body, text="プレビュー", padding=10)
        body.add(left, weight=2)
        body.add(right, weight=3)

        cols = ("adopted_at", "character", "name", "image")
        tree = ttk.Treeview(
            left, columns=cols, show="headings", selectmode="browse"
        )
        for col, title, width in [
            ("adopted_at", "採用日時", 155),
            ("character", "Character", 150),
            ("name", "正式名称", 160),
            ("image", "画像", 260),
        ]:
            tree.heading(col, text=title)
            tree.column(col, width=width)

        scroll = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        preview_label = ttk.Label(
            right, text="画像を選択してください", anchor="center"
        )
        preview_label.pack(fill="both", expand=True)

        info_text = tk.Text(right, height=10, wrap="word", state="disabled")
        info_text.pack(fill="x", pady=(8, 0))

        preview_ref = {"image": None}
        records = {}

        for adopted in adopted_items:
            iid = str(adopted.get("id") or "")
            if not iid:
                continue
            records[iid] = adopted
            character_name = self._adopted_entity_name(
                "characters", adopted.get("character_id") or ""
            )
            tree.insert(
                "", "end", iid=iid,
                values=(
                    adopted.get("adopted_at") or "",
                    character_name,
                    adopted.get("name") or Path(adopted.get("image_path") or "").name,
                    Path(adopted.get("image_path") or "").name,
                )
            )

        def _selected():
            sel = tree.selection()
            if not sel:
                return None
            return records.get(sel[0])

        def _show_preview(_event=None):
            adopted = _selected()
            if not adopted:
                preview_ref["image"] = None
                preview_label.configure(image="", text="画像を選択してください")
                info_text.configure(state="normal")
                info_text.delete("1.0", "end")
                info_text.configure(state="disabled")
                return

            path = Path(adopted.get("image_path") or "")
            if not path.exists():
                preview_ref["image"] = None
                preview_label.configure(image="", text="画像ファイルが見つかりません")
            else:
                try:
                    img = tk.PhotoImage(file=str(path))
                    w, h = img.width(), img.height()
                    max_w, max_h = 480, 420
                    factor = max(
                        1,
                        (w + max_w - 1) // max_w,
                        (h + max_h - 1) // max_h
                    )
                    if factor > 1:
                        img = img.subsample(factor, factor)
                    preview_ref["image"] = img
                    preview_label.configure(image=img, text="")
                except Exception:
                    preview_ref["image"] = None
                    preview_label.configure(
                        image="", text=f"プレビューを表示できません\n{path.name}"
                    )

            character_name = self._adopted_entity_name(
                "characters", adopted.get("character_id") or ""
            )
            tags = adopted.get("tags") or []
            if isinstance(tags, list):
                tags = ", ".join(str(x) for x in tags)

            display_name = adopted.get("name") or path.name
            lines = [
                f"正式名称: {display_name}",
                f"Character: {character_name}",
                f"採用日: {adopted.get('adopted_at') or ''}",
                f"タグ: {tags}",
                "",
                "メモ:",
                adopted.get("notes") or "",
            ]
            info_text.configure(state="normal")
            info_text.delete("1.0", "end")
            info_text.insert("1.0", "\n".join(lines))
            info_text.configure(state="disabled")

        def _open_selected(_event=None):
            adopted = _selected()
            if not adopted:
                return
            path = Path(adopted.get("image_path") or "")
            if not path.exists():
                messagebox.showwarning(
                    "Project",
                    f"画像ファイルが見つかりません。\n\n{path}",
                    parent=win
                )
                return
            try:
                os.startfile(str(path))
            except Exception as e:
                messagebox.showerror(
                    "Project",
                    f"画像を開けませんでした。\n{e}",
                    parent=win
                )

        def _restore_selected():
            adopted = _selected()
            if not adopted:
                messagebox.showinfo(
                    "Project",
                    "Generateへ復元する採用画像を選択してください。",
                    parent=win
                )
                return
            try:
                self._restore_adopted_to_generate(adopted)
            except Exception as e:
                messagebox.showerror(
                    "Project",
                    f"Generateへの復元に失敗しました。\n{e}",
                    parent=win
                )
                return
            win.destroy()

        tree.bind("<<TreeviewSelect>>", _show_preview)
        tree.bind("<Double-1>", _open_selected)

        buttons = ttk.Frame(win, padding=(10, 0, 10, 10))
        buttons.pack(fill="x")
        ttk.Button(
            buttons, text="Generateへ復元",
            command=_restore_selected
        ).pack(side="left")
        ttk.Button(
            buttons, text="画像を開く",
            command=_open_selected
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            buttons, text="閉じる",
            command=win.destroy
        ).pack(side="right")

    def _project_show_selected(self, _event=None):
        item = self._project_selected_item()
        if not item:
            return

        self._project_selected_id = item.get("id")
        self.project_name.set(item.get("name") or "")
        self.project_character.set(item.get("character_name") or "")
        self.project_output_dir.set(item.get("output_dir") or "")
        self.project_state.set(item.get("state") or "進行中")

        self.project_notes_text.delete("1.0", "end")
        self.project_notes_text.insert("1.0", item.get("notes") or "")
        self._refresh_project_production_status(item)

    def _project_generation_count(self, project_id):
        if not project_id:
            return 0
        try:
            return sum(
                1 for x in self.repo.list_items("history")
                if str(x.get("project_id") or "") == str(project_id)
            )
        except Exception:
            return 0

    def _project_adopted_count(self, project_id):
        if not project_id:
            return 0
        try:
            return sum(
                1 for x in self.repo.list_items("adopted")
                if str(x.get("project_id") or "") == str(project_id)
            )
        except Exception:
            return 0

    def _refresh_project_production_status(self, item):
        if not item:
            return

        project_id = item.get("id") or ""
        session = item.get("session") or {}
        saved_at = session.get("saved_at") or ""
        saved_text = saved_at.replace("T", " ") if saved_at else "未保存"

        character_name = item.get("character_name") or ""
        if not character_name:
            character_id = session.get("character_id") or ""
            if character_id:
                char_item = self.repo.get_item("characters", character_id)
                if char_item:
                    character_name = char_item.get("name") or ""

        prompt_name = session.get("prompt_library_name") or ""
        if prompt_name == "未選択":
            prompt_name = ""

        self.project_session_saved_at.set(f"最終セッション: {saved_text}")
        self.project_generated_count.set(
            f"生成枚数: {self._project_generation_count(project_id)}"
        )
        self.project_adopted_count.set(
            f"採用枚数: {self._project_adopted_count(project_id)}"
        )
        self.project_active_character.set(
            f"Character: {character_name or '未設定'}"
        )
        self.project_last_prompt.set(
            f"最後のPrompt: {prompt_name or '未設定'}"
        )

    def project_clear_form(self):
        self._project_selected_id = None
        self.project_name.set("")
        self.project_character.set("")
        self.project_output_dir.set("")
        self.project_state.set("進行中")
        self.project_notes_text.delete("1.0", "end")
        self.project_tree.selection_remove(self.project_tree.selection())
        if hasattr(self, "project_session_saved_at"):
            self.project_session_saved_at.set("最終セッション: 未保存")
            self.project_generated_count.set("生成枚数: 0")
            self.project_adopted_count.set("採用枚数: 0")
            self.project_active_character.set("Character: 未設定")
            self.project_last_prompt.set("最後のPrompt: 未設定")
        self.project_status_var.set("新規Projectモード")

    def project_choose_output_dir(self):
        path = filedialog.askdirectory(title="Projectの保存先を選択")
        if path:
            self.project_output_dir.set(path)

    def _project_find_character(self, name):
        name = (name or "").strip()
        if not name:
            return None
        for item in self.repo.list_items("characters"):
            if (item.get("name") or "").strip() == name:
                return item
        return None

    def project_save_current(self):
        name = self.project_name.get().strip()
        if not name:
            messagebox.showinfo("Project", "Project名を入力してください。")
            return

        character_name = self.project_character.get().strip()
        if character_name and not self._project_find_character(character_name):
            messagebox.showinfo("Project", "選択したCharacterが見つかりません。")
            return

        favorite = False
        created_at = None
        if self._project_selected_id:
            old = self.repo.get_item("projects", self._project_selected_id)
            if old:
                favorite = bool(old.get("favorite", False))
                created_at = old.get("created_at")

        item = {
            "id": self._project_selected_id or "",
            "name": name,
            "character_name": character_name,
            "output_dir": self.project_output_dir.get().strip(),
            "state": self.project_state.get().strip() or "進行中",
            "notes": self.project_notes_text.get("1.0", "end").strip(),
            "favorite": favorite,
            "created_at": created_at,
            "preset_id": "",
            "last_opened": "",
        }

        saved = self.repo.upsert_item("projects", item)
        self._project_selected_id = saved.get("id")
        self.refresh_project_list()

        if self._project_selected_id in self.project_tree.get_children():
            self.project_tree.selection_set(self._project_selected_id)
            self.project_tree.focus(self._project_selected_id)

        self.project_status_var.set(f"保存しました: {name}")
        if hasattr(self, "core_tree"):
            self.refresh_core_status()

    def project_toggle_favorite(self):
        item = self._project_selected_item()
        if not item:
            messagebox.showinfo("Project", "お気に入りを切り替えるProjectを選択してください。")
            return

        item["favorite"] = not bool(item.get("favorite", False))
        self.repo.upsert_item("projects", item)
        self.refresh_project_list()
        if item["id"] in self.project_tree.get_children():
            self.project_tree.selection_set(item["id"])
            self.project_tree.focus(item["id"])
        self.project_status_var.set("お気に入りを更新しました")

    def _project_apply_character_item(self, char_item):
        """Apply a Character record directly to Generate tab without requiring tree selection."""
        name = char_item.get("name") or ""
        saved_model = char_item.get("model") or ""

        if saved_model and not self._char_available_model_titles():
            self._quiet_refresh_model_catalog()

        model_kind, model_title = self._char_find_model_match(saved_model)

        if saved_model:
            if model_kind == "compatible":
                if messagebox.askyesno(
                    "モデル候補",
                    "Projectに紐付いたCharacterのモデルに同名候補が見つかりました。\\n\\n"
                    f"保存: {saved_model}\\n"
                    f"候補: {model_title}\\n\\n"
                    "この候補を使用しますか？"
                ):
                    if model_title and model_title != saved_model:
                        learned = dict(char_item)
                        learned["model"] = model_title
                        char_item = self.repo.upsert_item("characters", learned)
                        saved_model = model_title
                        if hasattr(self, "char_tree"):
                            self.refresh_character_list()
                else:
                    model_title = None
            elif model_kind == "missing":
                if not messagebox.askyesno(
                    "モデルが見つかりません",
                    f"Character「{name}」のモデルがForge側に見つかりません。\\n\\n"
                    f"{saved_model}\\n\\n"
                    "モデルをスキップして、その他の設定だけ適用しますか？"
                ):
                    return False
                model_title = None
            elif model_kind == "unchecked":
                if not messagebox.askyesno(
                    "モデル未確認",
                    "Forgeのモデル一覧を自動取得できませんでした。\\n"
                    "モデルをスキップして、その他の設定だけ適用しますか？"
                ):
                    return False
                model_title = None

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", char_item.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", char_item.get("negative_prompt") or "")

        if model_title:
            self._char_select_model_in_combo(model_title)

        available_loras = {a.path.stem.lower(): a.path.stem for a in getattr(self, "lora_items", [])}
        self.active_loras.clear()
        missing_loras = []

        for x in char_item.get("loras") or []:
            if not isinstance(x, dict) or not x.get("name"):
                continue
            requested = x["name"]
            actual = available_loras.get(requested.lower())
            if getattr(self, "lora_items", None) and not actual:
                missing_loras.append(requested)
                continue
            try:
                weight = float(x.get("weight", 1.0))
            except Exception:
                weight = 1.0
            self.active_loras[actual or requested] = weight

        self._refresh_lora_tree()

        gen = char_item.get("generation") or {}
        if gen.get("sampler"):
            self.sampler.set(gen["sampler"])
        if gen.get("steps") is not None:
            try:
                self.steps.set(int(gen["steps"]))
            except Exception:
                pass
        if gen.get("cfg") is not None:
            try:
                self.cfg.set(float(gen["cfg"]))
            except Exception:
                pass
        if gen.get("width") is not None:
            try:
                self.width.set(int(gen["width"]))
            except Exception:
                pass
        if gen.get("height") is not None:
            try:
                self.height.set(int(gen["height"]))
            except Exception:
                pass

        if (
            model_title
            and hasattr(self, "char_apply_model_to_forge")
            and self.char_apply_model_to_forge.get()
        ):
            current = ""
            try:
                current = self.current_model_var.get().strip()
            except Exception:
                pass
            if self._char_normalize_model_name(current) != self._char_normalize_model_name(model_title):
                self._char_apply_model_to_forge(model_title, name)

        return True

    def project_open_selected(self):
        item = self._project_selected_item()
        if not item:
            messagebox.showinfo("Project", "開くProjectを選択してください。")
            return

        character_name = item.get("character_name") or ""
        if character_name:
            char_item = self._project_find_character(character_name)
            if not char_item:
                messagebox.showwarning(
                    "Project",
                    f"紐付けCharacter「{character_name}」が見つかりません。\\n"
                    "Project自体は残っています。Character設定だけ適用できません。"
                )
            else:
                if not self._project_apply_character_item(char_item):
                    return

        # Workspace current pointers.
        # Keep runtime state first so History linkage does not depend on disk I/O.
        self.active_project_id = item.get("id") or ""
        selected_character_id = ""

        if character_name:
            char_item = self._project_find_character(character_name)
            if char_item:
                selected_character_id = char_item.get("id") or ""
                self.active_character_id = selected_character_id

        try:
            ws = self.repo.workspace()
            ws["current_project_id"] = self.active_project_id
            ws["active_project_id"] = self.active_project_id
            if selected_character_id:
                ws["current_character_id"] = selected_character_id
                ws["active_character_id"] = selected_character_id
            self.repo.save_all("workspace", ws)
        except Exception as e:
            # Runtime linkage remains valid even if workspace persistence fails.
            try:
                self.status.set(f"Projectは開きましたがWorkspace保存に失敗しました: {e}")
            except Exception:
                pass

        output_dir = item.get("output_dir") or ""

        # Build a visible summary of what was restored.
        model_text = ""
        try:
            model_text = self.model_combo.get().strip()
        except Exception:
            pass

        try:
            lora_text = compact_lora_text(self.active_loras)
        except Exception:
            lora_text = "なし"

        summary_parts = [
            f"Project: {item.get('name','')}",
            f"Character: {character_name or '未設定'}",
            f"Model: {model_text or '未取得'}",
            f"LoRA: {lora_text}",
        ]
        if output_dir:
            summary_parts.append(f"保存先: {output_dir}")

        if hasattr(self, "current_project_summary"):
            self.current_project_summary.set(" / ".join(summary_parts))

        self.status.set(
            f"Project「{item.get('name','')}」を開き、生成タブへ反映しました。"
            "生成は開始していません。"
        )
        self.project_status_var.set(f"開きました: {item.get('name','')}")

        # Move to Generate tab so the restored settings are immediately visible.
        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

    def _build_generate(self):
        # Generate画面は機能追加で縦に長くなるため、初期ウィンドウサイズでも
        # 下部の生成ボタンまでアクセスできるよう縦スクロール対応にする。
        gen_canvas = tk.Canvas(self.generate, highlightthickness=0)
        gen_scroll = ttk.Scrollbar(
            self.generate, orient="vertical", command=gen_canvas.yview
        )
        gen_canvas.configure(yscrollcommand=gen_scroll.set)

        gen_scroll.pack(side="right", fill="y")
        gen_canvas.pack(side="left", fill="both", expand=True)

        gen_body = ttk.Frame(gen_canvas)
        gen_window = gen_canvas.create_window((0, 0), window=gen_body, anchor="nw")

        def _gen_update_scrollregion(_event=None):
            gen_canvas.configure(scrollregion=gen_canvas.bbox("all"))

        def _gen_resize_body(event):
            gen_canvas.itemconfigure(gen_window, width=event.width)

        gen_body.bind("<Configure>", _gen_update_scrollregion)
        gen_canvas.bind("<Configure>", _gen_resize_body)

        def _gen_mousewheel(event):
            # Windows mouse wheel
            gen_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _gen_bind_wheel(_event=None):
            gen_canvas.bind_all("<MouseWheel>", _gen_mousewheel)

        def _gen_unbind_wheel(_event=None):
            gen_canvas.unbind_all("<MouseWheel>")

        gen_canvas.bind("<Enter>", _gen_bind_wheel)
        gen_canvas.bind("<Leave>", _gen_unbind_wheel)

        workflow_box = ttk.LabelFrame(gen_body, text="制作ナビ", padding=8)
        workflow_box.pack(fill="x", pady=(0, 8))

        self.workflow_nav_var = tk.StringVar(
            value="Project → Character → Generate → 採用"
        )
        ttk.Label(
            workflow_box,
            textvariable=self.workflow_nav_var,
            anchor="w"
        ).pack(fill="x")

        self.workflow_state_var = tk.StringVar(
            value="現在: 制作準備"
        )
        ttk.Label(
            workflow_box,
            textvariable=self.workflow_state_var,
            anchor="w"
        ).pack(fill="x", pady=(4, 0))

        project_banner = ttk.LabelFrame(gen_body, text="現在の制作内容", padding=8)
        project_banner.pack(fill="x", pady=(0, 10))

        # 既存コード互換用。画面表示は下の項目別StringVarを使う。
        self.current_project_summary = tk.StringVar(value="未選択")

        self.workflow_project_var = tk.StringVar(value="未選択")
        self.workflow_character_var = tk.StringVar(value="未選択")
        self.workflow_preset_var = tk.StringVar(value="未選択")
        self.workflow_prompt_var = tk.StringVar(value="未選択")
        self.workflow_model_var = tk.StringVar(value="未選択")
        self.workflow_lora_var = tk.StringVar(value="0件")

        summary_grid = ttk.Frame(project_banner)
        summary_grid.pack(fill="x")

        summary_rows = (
            ("Project", self.workflow_project_var, "Character", self.workflow_character_var),
            ("Preset", self.workflow_preset_var, "Prompt", self.workflow_prompt_var),
            ("Model", self.workflow_model_var, "LoRA", self.workflow_lora_var),
        )
        for row_index, (label1, var1, label2, var2) in enumerate(summary_rows):
            ttk.Label(
                summary_grid, text=label1, width=10
            ).grid(row=row_index, column=0, sticky="w", pady=2)
            ttk.Label(
                summary_grid, textvariable=var1, anchor="w"
            ).grid(row=row_index, column=1, sticky="ew", padx=(4, 18), pady=2)

            ttk.Label(
                summary_grid, text=label2, width=10
            ).grid(row=row_index, column=2, sticky="w", pady=2)
            ttk.Label(
                summary_grid, textvariable=var2, anchor="w"
            ).grid(row=row_index, column=3, sticky="ew", padx=(4, 0), pady=2)

        summary_grid.columnconfigure(1, weight=1)
        summary_grid.columnconfigure(3, weight=1)

        quick = ttk.LabelFrame(gen_body, text="制作準備", padding=8)
        quick.pack(fill="x", pady=(0, 10))

        self.quick_project = tk.StringVar(value="未選択")
        self.quick_character = tk.StringVar(value="未選択")
        self.quick_prompt = tk.StringVar(value="未選択")

        ttk.Label(quick, text="Project").grid(row=0, column=0, sticky="w")
        self.quick_project_combo = ttk.Combobox(
            quick, textvariable=self.quick_project, state="readonly", width=24
        )
        self.quick_project_combo.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self.quick_project_combo.bind(
            "<<ComboboxSelected>>", self._quick_project_selected
        )

        ttk.Label(quick, text="Character").grid(row=0, column=2, sticky="w")
        self.quick_character_combo = ttk.Combobox(
            quick, textvariable=self.quick_character, state="readonly", width=22
        )
        self.quick_character_combo.grid(row=0, column=3, sticky="ew", padx=(6, 12))

        ttk.Label(quick, text="Prompt").grid(row=0, column=4, sticky="w")
        self.quick_prompt_combo = ttk.Combobox(
            quick, textvariable=self.quick_prompt, state="readonly", width=24
        )
        self.quick_prompt_combo.grid(row=0, column=5, sticky="ew", padx=(6, 12))

        ttk.Button(
            quick, text="再読込", command=self.refresh_generate_quick_setup
        ).grid(row=0, column=6, padx=(0, 6))
        ttk.Button(
            quick,
            text="制作開始",
            command=self.start_production_prepare,
            width=12
        ).grid(row=0, column=7, columnspan=2, sticky="e", padx=(8, 0))

        self.quick_proposal_status = tk.StringVar(value="")
        ttk.Label(
            quick,
            textvariable=self.quick_proposal_status,
            anchor="w"
        ).grid(row=1, column=0, columnspan=9, sticky="ew", pady=(6, 0))

        session_row = ttk.Frame(quick)
        session_row.grid(row=2, column=0, columnspan=9, sticky="ew", pady=(8, 0))
        ttk.Label(session_row, text="制作セッション").pack(side="left")
        ttk.Button(
            session_row, text="保存",
            command=self.save_current_project_session
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            session_row, text="復元",
            command=self.restore_current_project_session
        ).pack(side="left", padx=(6, 6))
        ttk.Button(
            session_row, text="前回生成を復元",
            command=self.restore_last_generation_to_generate
        ).pack(side="left", padx=(0, 12))
        self.session_status_var = tk.StringVar(value="未保存")
        ttk.Label(
            session_row, textvariable=self.session_status_var
        ).pack(side="left")

        self.autosave_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            session_row,
            text="自動保存",
            variable=self.autosave_enabled
        ).pack(side="left", padx=(12, 0))
        self.autosave_status_var = tk.StringVar(value="")
        ttk.Label(
            session_row, textvariable=self.autosave_status_var
        ).pack(side="left", padx=(6, 0))

        detail_toggle_row = ttk.Frame(quick)
        detail_toggle_row.grid(
            row=3, column=0, columnspan=9, sticky="ew", pady=(8, 0)
        )

        self.quick_detail_visible = tk.BooleanVar(value=False)
        self.quick_detail_button_text = tk.StringVar(value="▶ 個別適用")

        partial = ttk.Frame(quick)

        def _toggle_quick_detail():
            if self.quick_detail_visible.get():
                partial.grid_remove()
                self.quick_detail_visible.set(False)
                self.quick_detail_button_text.set("▶ 個別適用")
            else:
                partial.grid(
                    row=4, column=0, columnspan=9,
                    sticky="ew", pady=(6, 0)
                )
                self.quick_detail_visible.set(True)
                self.quick_detail_button_text.set("▼ 個別適用")

        ttk.Button(
            detail_toggle_row,
            textvariable=self.quick_detail_button_text,
            command=_toggle_quick_detail
        ).pack(side="left")

        ttk.Label(
            detail_toggle_row,
            text="通常は「制作開始」だけでOK"
        ).pack(side="left", padx=(10, 0))

        ttk.Button(
            partial, text="Projectだけ適用",
            command=self.apply_quick_project_only
        ).pack(side="left")
        ttk.Button(
            partial, text="Characterだけ適用",
            command=self.apply_quick_character_only
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            partial, text="Promptだけ適用",
            command=self.apply_quick_prompt_only
        ).pack(side="left", padx=(6, 0))

        quick.columnconfigure(1, weight=1)
        quick.columnconfigure(3, weight=1)
        quick.columnconfigure(5, weight=1)

        self.quick_active_summary = tk.StringVar(
            value="現在適用中: Project=未選択 / Character=未選択 / Prompt=未選択"
        )
        ttk.Label(
            quick,
            textvariable=self.quick_active_summary,
            anchor="w",
            justify="left"
        ).grid(row=5, column=0, columnspan=9, sticky="ew", pady=(8, 0))

        self.generate_dirty_state = tk.StringVar(value="✓ 保存済み")
        ttk.Label(
            quick,
            textvariable=self.generate_dirty_state,
            anchor="w"
        ).grid(row=6, column=0, columnspan=9, sticky="ew", pady=(4, 0))

        self.production_check_var = tk.StringVar(value="制作チェック: 未実行")
        ttk.Label(
            quick,
            textvariable=self.production_check_var,
            anchor="w",
            justify="left"
        ).grid(row=7, column=0, columnspan=9, sticky="ew", pady=(4, 0))

        generation_box = ttk.LabelFrame(gen_body, text="生成設定", padding=8)
        generation_box.pack(fill="both", expand=True, pady=(0, 8))

        content = ttk.Frame(generation_box)
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(content)
        right.pack(side="right", fill="both", expand=True, padx=(12,0))

        preset_box = ttk.LabelFrame(left, text="Generateプリセット", padding=6)
        preset_box.pack(fill="x", pady=(0, 8))

        self.generate_preset_name = tk.StringVar(value="未選択")
        self.generate_preset_combo = ttk.Combobox(
            preset_box,
            textvariable=self.generate_preset_name,
            state="readonly",
            width=28
        )
        self.generate_preset_combo.pack(side="left", fill="x", expand=True)
        self.generate_preset_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_generate_preset_update_button(),
        )
        ttk.Button(
            preset_box,
            text="適用",
            command=self.apply_selected_generate_preset
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            preset_box,
            text="現在設定を保存",
            command=self.save_current_generate_preset
        ).pack(side="left", padx=(6, 0))
        self.update_generate_preset_button = ttk.Button(
            preset_box,
            text="選択中を更新",
            command=self.update_selected_generate_preset,
            state="disabled",
        )
        self.update_generate_preset_button.pack(side="left", padx=(6, 0))
        ttk.Button(
            preset_box,
            text="削除",
            command=self.delete_selected_generate_preset
        ).pack(side="left", padx=(6, 0))

        row = ttk.Frame(left)
        row.pack(fill="x")
        ttk.Label(row, text="モデル", width=10).pack(side="left")
        self.model_combo = ttk.Combobox(row, state="readonly")
        self.model_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(
            row, text="モデル適用",
            command=self.apply_selected_model
        ).pack(side="left", padx=(6, 0))

        lora_status_row = ttk.Frame(left)
        lora_status_row.pack(fill="x", pady=(5, 0))
        ttk.Label(lora_status_row, text="有効LoRA", width=10).pack(side="left")
        self.active_lora_summary = tk.StringVar(value="0件")
        ttk.Label(
            lora_status_row,
            textvariable=self.active_lora_summary
        ).pack(side="left")

        self.current_model_var = tk.StringVar(value="未取得")

        ttk.Label(left, text="プロンプト").pack(anchor="w", pady=(8,0))
        self.prompt = tk.Text(left, height=8, wrap="word")
        self.prompt.pack(fill="x")
        ttk.Label(left, text="ネガティブプロンプト").pack(anchor="w", pady=(6,0))
        self.negative = tk.Text(left, height=5, wrap="word")
        self.negative.pack(fill="x")

        # よく使う生成設定は1段にまとめる。
        grid = ttk.LabelFrame(left, text="基本生成設定", padding=6)
        grid.pack(fill="x", pady=(8, 0))

        self.steps = tk.IntVar(value=28)
        self.cfg = tk.DoubleVar(value=6.0)
        self.width = tk.IntVar(value=1024)
        self.height = tk.IntVar(value=1024)
        self.sampler = tk.StringVar(value="DPM++ 2M")
        self.seed = tk.StringVar(value="-1")
        self.scheduler = tk.StringVar(value="Automatic")

        ttk.Label(grid, text="Steps").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.steps, width=7).grid(
            row=0, column=1, sticky="w", padx=(4, 10)
        )
        ttk.Label(grid, text="CFG").grid(row=0, column=2, sticky="w")
        ttk.Entry(grid, textvariable=self.cfg, width=7).grid(
            row=0, column=3, sticky="w", padx=(4, 10)
        )
        ttk.Label(grid, text="幅").grid(row=0, column=4, sticky="w")
        ttk.Entry(grid, textvariable=self.width, width=8).grid(
            row=0, column=5, sticky="w", padx=(4, 10)
        )
        ttk.Label(grid, text="高さ").grid(row=0, column=6, sticky="w")
        ttk.Entry(grid, textvariable=self.height, width=8).grid(
            row=0, column=7, sticky="w", padx=(4, 10)
        )
        ttk.Label(grid, text="Sampler").grid(row=0, column=8, sticky="w")
        self.sampler_combo = ttk.Combobox(
            grid, textvariable=self.sampler, width=22
        )
        self.sampler_combo.grid(
            row=0, column=9, sticky="ew", padx=(4, 0)
        )
        ttk.Label(grid, text="Seed").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(grid, textvariable=self.seed, width=12).grid(
            row=1, column=1, sticky="w", padx=(4, 10), pady=(6, 0)
        )
        ttk.Label(grid, text="Scheduler").grid(
            row=1, column=2, sticky="w", pady=(6, 0)
        )
        ttk.Combobox(
            grid,
            textvariable=self.scheduler,
            values=("Automatic", "Karras"),
            state="readonly",
            width=12,
        ).grid(row=1, column=3, sticky="w", padx=(4, 10), pady=(6, 0))
        grid.columnconfigure(9, weight=1)

        # Forge向けの更新・確認操作は普段使わないので折りたたむ。
        advanced_header = ttk.Frame(left)
        advanced_header.pack(fill="x", pady=(6, 0))
        self.generate_advanced_visible = tk.BooleanVar(value=False)
        self.generate_advanced_button_text = tk.StringVar(
            value="▶ 詳細設定"
        )

        advanced = ttk.LabelFrame(left, text="詳細設定", padding=6)

        def _toggle_generate_advanced():
            if self.generate_advanced_visible.get():
                advanced.pack_forget()
                self.generate_advanced_visible.set(False)
                self.generate_advanced_button_text.set("▶ 詳細設定")
            else:
                advanced.pack(fill="x", pady=(4, 0))
                self.generate_advanced_visible.set(True)
                self.generate_advanced_button_text.set("▼ 詳細設定")

        ttk.Button(
            advanced_header,
            textvariable=self.generate_advanced_button_text,
            command=_toggle_generate_advanced
        ).pack(side="left")

        ttk.Button(
            advanced, text="モデル一覧をAPIから更新",
            command=self.load_api_models
        ).pack(side="left")
        ttk.Button(
            advanced, text="現在モデル確認",
            command=self.refresh_current_model
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            advanced, text="Sampler一覧を更新",
            command=self.load_samplers
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            advanced, text="現在モデル:"
        ).pack(side="left", padx=(12, 4))
        ttk.Label(
            advanced, textvariable=self.current_model_var
        ).pack(side="left", fill="x", expand=True)

        execute_box = ttk.LabelFrame(left, text="実行", padding=8)
        execute_box.pack(fill="x", pady=(8, 0))

        self.queue_count = tk.IntVar(value=1)
        self.queue_status = tk.StringVar(value="キュー: 待機")
        self._queue_running = False
        self._queue_stop_requested = False

        ttk.Label(
            execute_box,
            text="生成前チェック後、Forgeへ実際の生成要求を送ります。"
        ).pack(anchor="w")

        execute_row = ttk.Frame(execute_box)
        execute_row.pack(fill="x", pady=(6, 0))

        ttk.Label(execute_row, text="連続枚数").pack(side="left")
        ttk.Spinbox(
            execute_row,
            from_=1,
            to=20,
            textvariable=self.queue_count,
            width=6
        ).pack(side="left", padx=(6, 10))

        ttk.Button(
            execute_row,
            text="連続生成",
            command=self.start_generation_queue
        ).pack(side="left")
        ttk.Button(
            execute_row,
            text="停止",
            command=self.stop_generation_queue
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            execute_row,
            textvariable=self.queue_status
        ).pack(side="left", padx=(12, 0))

        ttk.Button(
            execute_row, text="1枚生成",
            command=self.generate_image
        ).pack(side="right")

        session_box = ttk.LabelFrame(right, text="セッション生成一覧", padding=8)
        session_box.pack(fill="x", pady=(0, 8))

        self._session_generation_records = []
        self._session_generation_buttons = []
        self._session_gallery_refs = []
        self._session_selected_path = ""

        self.session_gallery = ttk.Frame(session_box)
        self.session_gallery.pack(fill="x")

        session_footer = ttk.Frame(session_box)
        session_footer.pack(fill="x", pady=(6, 0))

        self.session_gallery_status = tk.StringVar(value="このセッションの生成: 0枚")
        ttk.Label(
            session_footer,
            textvariable=self.session_gallery_status,
            anchor="w"
        ).pack(side="left", fill="x", expand=True)

        ttk.Button(
            session_footer,
            text="一覧から外す",
            command=self.remove_selected_session_generation
        ).pack(side="right")

        latest_box = ttk.LabelFrame(right, text="選択画像詳細", padding=8)
        latest_box.pack(fill="both", expand=True)

        self.preview_label = ttk.Label(
            latest_box, text="生成画像プレビュー", anchor="center"
        )
        self.preview_label.pack(fill="both", expand=True)

        self.latest_generated_file = tk.StringVar(value="ファイル: 未生成")
        self.latest_generated_seed = tk.StringVar(value="Seed: -")
        self.latest_generated_model = tk.StringVar(value="Model: -")
        self.latest_generated_lora = tk.StringVar(value="LoRA: -")
        self.latest_generated_prompt = tk.StringVar(value="Prompt: -")
        self.latest_generated_time = tk.StringVar(value="生成時刻: -")

        latest_info = ttk.Frame(latest_box)
        latest_info.pack(fill="x", pady=(8, 0))
        for var in (
            self.latest_generated_file,
            self.latest_generated_seed,
            self.latest_generated_model,
            self.latest_generated_lora,
            self.latest_generated_prompt,
            self.latest_generated_time,
        ):
            ttk.Label(
                latest_info, textvariable=var, anchor="w"
            ).pack(fill="x")

        latest_actions = ttk.Frame(latest_box)
        latest_actions.pack(fill="x", pady=(8, 0))
        ttk.Button(
            latest_actions,
            text="PNGを開く",
            command=self.open_latest_generated_image
        ).pack(side="left")
        ttk.Button(
            latest_actions,
            text="選択画像を採用",
            command=self.adopt_latest_generated_image
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            latest_actions,
            text="Aに設定",
            command=lambda: self.set_latest_generated_compare_slot("A")
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            latest_actions,
            text="Bに設定",
            command=lambda: self.set_latest_generated_compare_slot("B")
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            latest_actions,
            text="A/B比較を開く",
            command=self.open_adopted_compare_viewer
        ).pack(side="right")

        workflow_actions = ttk.Frame(latest_box)
        workflow_actions.pack(fill="x", pady=(6, 0))

        ttk.Label(
            workflow_actions,
            text="次の作業:"
        ).pack(side="left")

        ttk.Button(
            workflow_actions,
            text="Prompt保存",
            command=self.save_current_prompt_from_generate
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            workflow_actions,
            text="Prompt Builder",
            command=self.open_prompt_builder_from_generate
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            workflow_actions,
            text="AI Assistant",
            command=self.open_ai_assistant_from_generate
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            workflow_actions,
            text="画像解析",
            command=self.open_image_review_for_latest
        ).pack(side="left", padx=(6, 0))

        compare_state = ttk.LabelFrame(
            latest_box, text="A/B比較", padding=6
        )
        compare_state.pack(fill="x", pady=(8, 0))

        self.generate_compare_a_status = tk.StringVar(value="A: 未設定")
        self.generate_compare_b_status = tk.StringVar(value="B: 未設定")
        ttk.Label(
            compare_state,
            textvariable=self.generate_compare_a_status,
            anchor="w"
        ).pack(fill="x")
        ttk.Label(
            compare_state,
            textvariable=self.generate_compare_b_status,
            anchor="w"
        ).pack(fill="x", pady=(2, 0))

        compare_actions = ttk.Frame(compare_state)
        compare_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(
            compare_actions,
            text="Aを採用",
            command=lambda: self.adopt_compare_slot_from_generate("A")
        ).pack(side="left")
        ttk.Button(
            compare_actions,
            text="Bを採用",
            command=lambda: self.adopt_compare_slot_from_generate("B")
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            compare_actions,
            text="比較をクリア",
            command=self.clear_generate_compare_slots
        ).pack(side="right")

        self._latest_generated_record = None

        self._generate_saved_snapshot = None
        self._generate_dirty_check_scheduled = False
        self._autosave_after_id = None
        self._autosave_delay_ms = 5000

        self.prompt.bind("<<Modified>>", self._generate_text_modified)
        self.negative.bind("<<Modified>>", self._generate_text_modified)
        for var in (self.steps, self.cfg, self.width, self.height, self.sampler):
            try:
                var.trace_add("write", lambda *_: self._schedule_generate_dirty_check())
            except Exception:
                pass

        self.refresh_generate_quick_setup()
        self.refresh_generate_presets()
        self._mark_generate_saved()
        self._refresh_generate_workflow_state()
        self.refresh_production_check()

    def refresh_generate_presets(self):
        if not hasattr(self, "generate_preset_combo"):
            return
        try:
            items = [
                x for x in self.repo.list_items("presets")
                if (x.get("category") or "") == "generate"
            ]
        except Exception:
            items = []

        items = sorted(
            items,
            key=lambda x: str(x.get("name") or "").lower()
        )
        self._generate_preset_records = {
            (item.get("name") or ""): item
            for item in items
            if item.get("name")
        }
        names = ["未選択"] + list(self._generate_preset_records.keys())
        self.generate_preset_combo["values"] = names
        if self.generate_preset_name.get() not in names:
            self.generate_preset_name.set("未選択")
        self._refresh_generate_preset_update_button()

    def _refresh_generate_preset_update_button(self):
        if not hasattr(self, "update_generate_preset_button"):
            return
        name = self.generate_preset_name.get().strip()
        selected = (
            bool(name)
            and name != "未選択"
            and name in getattr(self, "_generate_preset_records", {})
        )
        self.update_generate_preset_button.configure(
            state="normal" if selected else "disabled"
        )

    def save_current_generate_preset(
        self, preset_name=None, confirm_existing=True, update_status=False
    ):
        if preset_name is None:
            name = simpledialog.askstring(
                "Generateプリセット",
                "プリセット名を入力してください。",
                parent=self
            )
            if not name:
                return
            name = name.strip()
            if not name:
                return
        else:
            name = str(preset_name).strip()
            if not name:
                return

        existing = None
        try:
            for item in self.repo.list_items("presets"):
                if (
                    (item.get("category") or "") == "generate"
                    and (item.get("name") or "").strip() == name
                ):
                    existing = item
                    break
        except Exception:
            existing = None

        if existing and confirm_existing:
            if not messagebox.askyesno(
                "Generateプリセット",
                f"「{name}」は既に存在します。\n上書きしますか？"
            ):
                return

        loras = []
        try:
            for lora_name, weight in (self.active_loras or {}).items():
                loras.append({
                    "name": lora_name,
                    "weight": float(weight),
                })
        except Exception:
            pass

        item = dict(existing or {})
        item.update({
            "name": name,
            "category": "generate",
            "prompt": self.prompt.get("1.0", "end").strip(),
            "negative_prompt": self.negative.get("1.0", "end").strip(),
            "model": self.model_combo.get().strip() if hasattr(self, "model_combo") else "",
            "loras": loras,
            "sampler": self.sampler.get() if hasattr(self, "sampler") else "",
            "scheduler": self.scheduler.get() if hasattr(self, "scheduler") else "Automatic",
            "steps": self.steps.get() if hasattr(self, "steps") else 28,
            "cfg": self.cfg.get() if hasattr(self, "cfg") else 6.0,
            "seed": self.seed.get() if hasattr(self, "seed") else "-1",
            "width": self.width.get() if hasattr(self, "width") else 1024,
            "height": self.height.get() if hasattr(self, "height") else 1024,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

        saved = self.repo.upsert_item("presets", item)
        self.refresh_generate_presets()
        self.generate_preset_name.set(saved.get("name") or name)
        self._refresh_generate_preset_update_button()
        if update_status:
            self.status.set(f"{name} を更新しました")
        else:
            self.status.set(f"Generateプリセットを保存しました: {name}")

    def update_selected_generate_preset(self):
        name = self.generate_preset_name.get().strip()
        item = getattr(self, "_generate_preset_records", {}).get(name)
        if not name or name == "未選択" or not item:
            messagebox.showinfo(
                "Generateプリセット",
                "更新するGenerateプリセットを選択してください。",
            )
            self._refresh_generate_preset_update_button()
            return
        if not messagebox.askyesno(
            "Generateプリセット更新",
            f"{name} を現在設定で更新しますか？",
        ):
            return
        self.save_current_generate_preset(
            preset_name=name,
            confirm_existing=False,
            update_status=True,
        )

    def apply_selected_generate_preset(self):
        name = self.generate_preset_name.get().strip()
        if not name or name == "未選択":
            messagebox.showinfo(
                "Generateプリセット",
                "適用するプリセットを選択してください。"
            )
            return

        item = getattr(self, "_generate_preset_records", {}).get(name)
        if not item:
            self.refresh_generate_presets()
            item = getattr(self, "_generate_preset_records", {}).get(name)
        if not item:
            messagebox.showwarning(
                "Generateプリセット",
                "プリセットが見つかりません。"
            )
            return

        model = item.get("model") or ""
        if model:
            try:
                values = list(self.model_combo["values"])
                if model not in values:
                    self.model_combo["values"] = values + [model]
                self.model_combo.set(model)
            except Exception:
                pass

        try:
            self.steps.set(int(item.get("steps", self.steps.get())))
            self.cfg.set(float(item.get("cfg", self.cfg.get())))
            self.width.set(int(item.get("width", self.width.get())))
            self.height.set(int(item.get("height", self.height.get())))
        except Exception:
            pass

        sampler = item.get("sampler") or ""
        if sampler:
            self.sampler.set(sampler)

        if "scheduler" in item and item.get("scheduler") not in (None, ""):
            self.scheduler.set(str(item.get("scheduler")))
        if "seed" in item and item.get("seed") not in (None, ""):
            self.seed.set(str(item.get("seed")))

        if "prompt" in item:
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", str(item.get("prompt") or ""))
        if "negative_prompt" in item:
            self.negative.delete("1.0", "end")
            self.negative.insert(
                "1.0", str(item.get("negative_prompt") or "")
            )

        try:
            self.active_loras = {}
            for entry in item.get("loras") or []:
                if not isinstance(entry, dict):
                    continue
                lora_name = entry.get("name") or ""
                if not lora_name:
                    continue
                self.active_loras[lora_name] = float(entry.get("weight", 1.0))
            if hasattr(self, "_refresh_active_lora_summary"):
                self._refresh_active_lora_summary()
            elif hasattr(self, "_refresh_lora_tree"):
                self._refresh_lora_tree()
        except Exception:
            pass

        self._schedule_generate_dirty_check()
        self._refresh_generate_workflow_state()
        self.status.set(
            f"Generateプリセット「{name}」を適用しました。生成は開始していません。"
        )

    def delete_selected_generate_preset(self):
        name = self.generate_preset_name.get().strip()
        if not name or name == "未選択":
            messagebox.showinfo(
                "Generateプリセット",
                "削除するプリセットを選択してください。"
            )
            return

        item = getattr(self, "_generate_preset_records", {}).get(name)
        if not item:
            return

        if not messagebox.askyesno(
            "Generateプリセット",
            f"「{name}」を削除しますか？"
        ):
            return

        item_id = str(item.get("id") or "")
        if not item_id:
            messagebox.showwarning(
                "Generateプリセット",
                "プリセットIDを確認できません。"
            )
            return

        try:
            self.repo.delete_item("presets", item_id)
        except Exception as e:
            messagebox.showerror(
                "Generateプリセット",
                f"削除に失敗しました。\n{e}"
            )
            return

        self.generate_preset_name.set("未選択")
        self.refresh_generate_presets()
        self.status.set(f"Generateプリセットを削除しました: {name}")

    def _refresh_generate_workflow_state(self):
        project_name = getattr(self, "_quick_active_project_name", "未選択")
        character_name = getattr(self, "_quick_active_character_name", "未選択")
        prompt_name = getattr(self, "_quick_active_prompt_name", "未選択")

        try:
            preset_name = self.generate_preset_name.get().strip()
        except Exception:
            preset_name = "未選択"
        if not preset_name:
            preset_name = "未選択"

        try:
            model_name = self.model_combo.get().strip()
        except Exception:
            model_name = ""

        try:
            loras = dict(self.active_loras)
        except Exception:
            loras = {}

        try:
            prompt_text = self.prompt.get("1.0", "end").strip()
        except Exception:
            prompt_text = ""

        # Keep the old one-line summary populated for compatibility with
        # existing restore/project code, while the UI uses the itemized vars.
        parts = [
            f"Project: {project_name}",
            f"Character: {character_name}",
            f"Preset: {preset_name}",
            f"Prompt: {prompt_name}",
            f"Model: {model_name or '未選択'}",
            f"LoRA: {len(loras)}件",
        ]
        if hasattr(self, "current_project_summary"):
            self.current_project_summary.set(" / ".join(parts))

        if hasattr(self, "workflow_project_var"):
            self.workflow_project_var.set(project_name or "未選択")
        if hasattr(self, "workflow_character_var"):
            self.workflow_character_var.set(character_name or "未選択")
        if hasattr(self, "workflow_preset_var"):
            self.workflow_preset_var.set(preset_name)
        if hasattr(self, "workflow_prompt_var"):
            self.workflow_prompt_var.set(prompt_name or "未選択")
        if hasattr(self, "workflow_model_var"):
            # Forge model titles may contain a hash; keep the full value but
            # remove accidental leading/trailing whitespace.
            self.workflow_model_var.set(model_name or "未選択")
        if hasattr(self, "workflow_lora_var"):
            if loras:
                names = ", ".join(list(loras.keys())[:3])
                if len(loras) > 3:
                    names += f" 他{len(loras)-3}件"
                self.workflow_lora_var.set(f"{len(loras)}件 / {names}")
            else:
                self.workflow_lora_var.set("0件")

        if project_name in {"", "未選択"}:
            state = "○ Projectを選択"
        elif character_name in {"", "未選択"}:
            state = "○ Characterを選択"
        elif not model_name:
            state = "○ Modelを確認"
        elif not prompt_text and not loras:
            state = "○ Promptを設定"
        else:
            state = "● 生成準備完了"

        if hasattr(self, "workflow_state_var"):
            self.workflow_state_var.set(state)

    def _generate_snapshot(self):
        try:
            prompt = self.prompt.get("1.0", "end").strip()
        except Exception:
            prompt = ""
        try:
            negative = self.negative.get("1.0", "end").strip()
        except Exception:
            negative = ""

        snapshot = {
            "prompt": prompt,
            "negative": negative,
            "steps": str(self.steps.get()) if hasattr(self, "steps") else "",
            "cfg": str(self.cfg.get()) if hasattr(self, "cfg") else "",
            "width": str(self.width.get()) if hasattr(self, "width") else "",
            "height": str(self.height.get()) if hasattr(self, "height") else "",
            "sampler": str(self.sampler.get()) if hasattr(self, "sampler") else "",
        }
        return snapshot

    def _mark_generate_saved(self):
        self._generate_saved_snapshot = self._generate_snapshot()
        if hasattr(self, "generate_dirty_state"):
            self.generate_dirty_state.set("✓ 保存済み")

        try:
            self.prompt.edit_modified(False)
            self.negative.edit_modified(False)
        except Exception:
            pass

    def _generate_text_modified(self, _event=None):
        try:
            self.prompt.edit_modified(False)
            self.negative.edit_modified(False)
        except Exception:
            pass
        self._schedule_generate_dirty_check()

    def _schedule_generate_dirty_check(self):
        if hasattr(self, "workflow_state_var"):
            try:
                self._refresh_generate_workflow_state()
            except Exception:
                pass
        if not getattr(self, "_generate_dirty_check_scheduled", False):
            self._generate_dirty_check_scheduled = True
            self.after(80, self._refresh_generate_dirty_state)
        self._schedule_project_autosave()

    def _schedule_project_autosave(self):
        if not hasattr(self, "autosave_enabled") or not self.autosave_enabled.get():
            return

        project = self._current_project_item()
        if not project:
            return

        if getattr(self, "_autosave_after_id", None):
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass

        if hasattr(self, "autosave_status_var"):
            self.autosave_status_var.set("変更待機中")

        self._autosave_after_id = self.after(
            getattr(self, "_autosave_delay_ms", 5000),
            self._autosave_current_project_session
        )

    def _autosave_current_project_session(self):
        self._autosave_after_id = None

        if not hasattr(self, "autosave_enabled") or not self.autosave_enabled.get():
            return

        project = self._current_project_item()
        if not project:
            return

        try:
            prompt_text = self.prompt.get("1.0", "end").strip()
            negative_text = self.negative.get("1.0", "end").strip()
        except Exception:
            prompt_text = ""
            negative_text = ""

        loras = []
        try:
            for name, weight in self.active_loras.items():
                loras.append({"name": name, "weight": float(weight)})
        except Exception:
            pass

        session = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "character_id": getattr(self, "active_character_id", "") or "",
            "prompt_library_name": getattr(self, "_quick_active_prompt_name", "未選択"),
            "model": self.model_combo.get().strip() if hasattr(self, "model_combo") else "",
            "loras": loras,
            "prompt": prompt_text,
            "negative_prompt": negative_text,
            "sampler": self.sampler.get() if hasattr(self, "sampler") else "",
            "steps": self.steps.get() if hasattr(self, "steps") else "",
            "cfg": self.cfg.get() if hasattr(self, "cfg") else "",
            "width": self.width.get() if hasattr(self, "width") else "",
            "height": self.height.get() if hasattr(self, "height") else "",
        }

        updated = dict(project)
        updated["session"] = session
        updated["last_opened"] = session["saved_at"]
        saved_project = self.repo.upsert_item("projects", updated)

        if hasattr(self, "session_status_var"):
            self.session_status_var.set(
                f"自動保存済み {session['saved_at'].replace('T', ' ')}"
            )
        if hasattr(self, "autosave_status_var"):
            self.autosave_status_var.set("")

        try:
            if (
                hasattr(self, "_project_selected_id")
                and self._project_selected_id == saved_project.get("id")
                and hasattr(self, "_refresh_project_production_status")
            ):
                self._refresh_project_production_status(saved_project)
        except Exception:
            pass

        self._mark_generate_saved()

    def _refresh_generate_dirty_state(self):
        self._generate_dirty_check_scheduled = False
        baseline = getattr(self, "_generate_saved_snapshot", None)
        current = self._generate_snapshot()

        if baseline is None:
            self._generate_saved_snapshot = current
            if hasattr(self, "generate_dirty_state"):
                self.generate_dirty_state.set("✓ 保存済み")
            return

        changed = [
            key for key, value in current.items()
            if value != baseline.get(key)
        ]

        if not changed:
            text = "✓ 保存済み"
        else:
            label_map = {
                "prompt": "Prompt",
                "negative": "Negative",
                "steps": "Steps",
                "cfg": "CFG",
                "width": "Width",
                "height": "Height",
                "sampler": "Sampler",
            }
            changed_text = ", ".join(label_map.get(x, x) for x in changed)
            text = f"● 未保存: {changed_text}"

        if hasattr(self, "generate_dirty_state"):
            self.generate_dirty_state.set(text)

    def _current_project_item(self):
        project_id = getattr(self, "active_project_id", "") or ""
        if not project_id:
            try:
                ws = self.repo.workspace()
                project_id = (
                    ws.get("current_project_id")
                    or ws.get("active_project_id")
                    or ""
                )
            except Exception:
                project_id = ""
        if not project_id:
            return None
        return self.repo.get_item("projects", project_id)

    def restore_last_generation_to_generate(self):
        items = self.repo.list_items("history")
        if not items:
            messagebox.showinfo(
                "前回生成を復元",
                "Historyに生成履歴がありません。"
            )
            return

        # Prefer the newest History record by created/time fields.
        latest = sorted(
            items,
            key=lambda x: str(
                x.get("created_at")
                or x.get("time")
                or x.get("timestamp")
                or ""
            ),
            reverse=True,
        )[0]

        # Reuse adopted/history restore-compatible fields directly.
        restored = {
            "image_path": latest.get("image_path") or "",
            "prompt": latest.get("prompt") or "",
            "negative_prompt": latest.get("negative_prompt") or "",
            "model": latest.get("model") or "",
            "loras": latest.get("loras") or [],
            "sampler": latest.get("sampler") or "",
            "steps": latest.get("steps", ""),
            "cfg": latest.get("cfg", ""),
            "width": latest.get("width", ""),
            "height": latest.get("height", ""),
            "seed": latest.get("seed", ""),
        }

        try:
            self._restore_adopted_to_generate(restored)
        except Exception as e:
            messagebox.showerror(
                "前回生成を復元",
                f"復元に失敗しました。\n{e}"
            )
            return

        project_id = latest.get("project_id") or ""
        character_id = latest.get("character_id") or ""

        if project_id:
            self.active_project_id = project_id
            p = self.repo.get_item("projects", project_id)
            if p:
                self._quick_active_project_name = p.get("name") or "未選択"
                try:
                    self.quick_project.set(self._quick_active_project_name)
                except Exception:
                    pass

        if character_id:
            self.active_character_id = character_id
            c = self.repo.get_item("characters", character_id)
            if c:
                self._quick_active_character_name = c.get("name") or "未選択"
                try:
                    self.quick_character.set(self._quick_active_character_name)
                except Exception:
                    pass

        # Keep Prompt label conservative because History may not preserve the Library item name.
        self._quick_active_prompt_name = "前回生成"

        if hasattr(self, "quick_active_summary"):
            self.quick_active_summary.set(
                f"現在適用中: "
                f"Project={getattr(self, '_quick_active_project_name', '未選択')} / "
                f"Character={getattr(self, '_quick_active_character_name', '未選択')} / "
                f"Prompt=前回生成"
            )

        self._mark_generate_saved()

        image_name = Path(latest.get("image_path") or "").name
        self.status.set(
            f"前回生成「{image_name}」の設定をGenerateへ復元しました。"
            "生成は開始していません。"
        )

    def save_current_project_session(self):
        if getattr(self, "_autosave_after_id", None):
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None
        if hasattr(self, "autosave_status_var"):
            self.autosave_status_var.set("")

        project = self._current_project_item()
        if not project:
            messagebox.showinfo(
                "制作セッション",
                "先にProjectを開くか、制作準備でProjectを適用してください。"
            )
            return

        try:
            prompt_text = self.prompt.get("1.0", "end").strip()
            negative_text = self.negative.get("1.0", "end").strip()
        except Exception:
            prompt_text = ""
            negative_text = ""

        loras = []
        try:
            for name, weight in self.active_loras.items():
                loras.append({"name": name, "weight": float(weight)})
        except Exception:
            pass

        session = {
            "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "character_id": getattr(self, "active_character_id", "") or "",
            "prompt_library_name": getattr(self, "_quick_active_prompt_name", "未選択"),
            "model": self.model_combo.get().strip() if hasattr(self, "model_combo") else "",
            "loras": loras,
            "prompt": prompt_text,
            "negative_prompt": negative_text,
            "sampler": self.sampler.get() if hasattr(self, "sampler") else "",
            "steps": self.steps.get() if hasattr(self, "steps") else "",
            "cfg": self.cfg.get() if hasattr(self, "cfg") else "",
            "width": self.width.get() if hasattr(self, "width") else "",
            "height": self.height.get() if hasattr(self, "height") else "",
        }

        updated = dict(project)
        updated["session"] = session
        updated["last_opened"] = session["saved_at"]
        saved_project = self.repo.upsert_item("projects", updated)

        try:
            if (
                hasattr(self, "_project_selected_id")
                and self._project_selected_id == saved_project.get("id")
                and hasattr(self, "_refresh_project_production_status")
            ):
                self._refresh_project_production_status(saved_project)
        except Exception:
            pass

        if hasattr(self, "session_status_var"):
            self.session_status_var.set(f"保存済み {session['saved_at'].replace('T', ' ')}")
        self._mark_generate_saved()
        self.status.set(
            f"Project「{project.get('name','')}」の制作セッションを保存しました。"
        )

    def restore_current_project_session(self):
        project = self._current_project_item()
        if not project:
            messagebox.showinfo(
                "制作セッション",
                "復元するProjectが選択されていません。"
            )
            return

        session = project.get("session") or {}
        if not session:
            messagebox.showinfo(
                "制作セッション",
                "このProjectには保存済み制作セッションがありません。"
            )
            return

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", session.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", session.get("negative_prompt") or "")

        try:
            self.active_loras.clear()
            for x in session.get("loras") or []:
                if isinstance(x, dict) and x.get("name"):
                    try:
                        weight = float(x.get("weight", 1.0))
                    except Exception:
                        weight = 1.0
                    self.active_loras[x["name"]] = weight
            self._refresh_lora_tree()
        except Exception:
            pass

        for key, var_name, caster in [
            ("steps", "steps", int),
            ("cfg", "cfg", float),
            ("width", "width", int),
            ("height", "height", int),
        ]:
            value = session.get(key, "")
            if value in ("", None):
                continue
            try:
                getattr(self, var_name).set(caster(value))
            except Exception:
                pass

        sampler = session.get("sampler") or ""
        if sampler:
            try:
                self.sampler.set(sampler)
            except Exception:
                pass

        model = (session.get("model") or "").strip()
        if model:
            try:
                values = list(self.model_combo["values"])
                norm_model = self._char_normalize_model_name(model)
                for i, title in enumerate(values):
                    if title == model or self._char_normalize_model_name(title) == norm_model:
                        self.model_combo.current(i)
                        break
            except Exception:
                pass

        character_id = session.get("character_id") or ""
        if character_id:
            self.active_character_id = character_id

        prompt_name = session.get("prompt_library_name") or "未選択"
        self._quick_active_project_name = project.get("name") or "未選択"
        self._quick_active_prompt_name = prompt_name

        char_name = "未選択"
        if self.active_character_id:
            char_item = self.repo.get_item("characters", self.active_character_id)
            if char_item:
                char_name = char_item.get("name") or "未選択"
        self._quick_active_character_name = char_name

        if hasattr(self, "quick_project"):
            self.quick_project.set(self._quick_active_project_name)
        if hasattr(self, "quick_character") and char_name != "未選択":
            self.quick_character.set(char_name)
        if hasattr(self, "quick_prompt") and prompt_name != "未選択":
            self.quick_prompt.set(prompt_name)

        if hasattr(self, "quick_active_summary"):
            self.quick_active_summary.set(
                f"現在適用中: Project={self._quick_active_project_name} / "
                f"Character={char_name} / Prompt={prompt_name}"
            )

        saved_at = session.get("saved_at") or ""
        if hasattr(self, "session_status_var"):
            self.session_status_var.set(
                f"復元済み {saved_at.replace('T', ' ')}" if saved_at else "復元済み"
            )

        self._mark_generate_saved()
        self.status.set(
            f"Project「{project.get('name','')}」の制作セッションを復元しました。"
            "生成は開始していません。"
        )

    def _quick_project_selected(self, _event=None):
        project = self._quick_find_named_item("projects", self.quick_project.get())
        if not project:
            if hasattr(self, "quick_proposal_status"):
                self.quick_proposal_status.set("")
            return

        suggestions = []

        # Project-linked Character.
        linked_character = (project.get("character_name") or "").strip()
        if linked_character:
            character_names = list(self.quick_character_combo["values"])
            if linked_character in character_names:
                self.quick_character.set(linked_character)
                suggestions.append(f"Character={linked_character}")

        # Last saved session can propose the last Prompt name.
        session = project.get("session") or {}
        prompt_name = (session.get("prompt_library_name") or "").strip()
        if prompt_name and prompt_name != "未選択":
            prompt_names = list(self.quick_prompt_combo["values"])
            if prompt_name in prompt_names:
                self.quick_prompt.set(prompt_name)
                suggestions.append(f"Prompt={prompt_name}")

        if hasattr(self, "quick_proposal_status"):
            if suggestions:
                self.quick_proposal_status.set(
                    "Projectから候補を反映: " + " / ".join(suggestions)
                )
            else:
                self.quick_proposal_status.set(
                    "このProjectには自動提案できるCharacter / Prompt情報がありません。"
                )

    def refresh_generate_quick_setup(self):
        if not hasattr(self, "quick_project_combo"):
            return

        projects = self.repo.list_items("projects")
        characters = self.repo.list_items("characters")
        prompts = self.repo.list_items("prompt_library")

        project_names = ["未選択"] + [
            x.get("name") for x in projects if x.get("name")
        ]
        character_names = ["未選択"] + [
            x.get("name") for x in characters if x.get("name")
        ]
        prompt_names = ["未選択"] + [
            x.get("name") for x in prompts if x.get("name")
        ]

        self.quick_project_combo["values"] = project_names
        self.quick_character_combo["values"] = character_names
        self.quick_prompt_combo["values"] = prompt_names

        if self.quick_project.get() not in project_names:
            self.quick_project.set("未選択")
        if self.quick_character.get() not in character_names:
            self.quick_character.set("未選択")
        if self.quick_prompt.get() not in prompt_names:
            self.quick_prompt.set("未選択")

        if self.quick_project.get() != "未選択":
            self._quick_project_selected()

    def _quick_find_named_item(self, db_name, name):
        name = (name or "").strip()
        if not name or name == "未選択":
            return None
        for item in self.repo.list_items(db_name):
            if (item.get("name") or "").strip() == name:
                return item
        return None

    def apply_quick_project_only(self):
        project = self._quick_find_named_item("projects", self.quick_project.get())
        if not project:
            messagebox.showinfo("制作準備", "適用するProjectを選択してください。")
            return

        self.active_project_id = project.get("id") or ""
        try:
            ws = self.repo.workspace()
            ws["current_project_id"] = self.active_project_id
            ws["active_project_id"] = self.active_project_id
            self.repo.save_all("workspace", ws)
        except Exception:
            pass

        self._quick_active_project_name = project.get("name") or "未選択"
        self.quick_active_summary.set(
            f"現在適用中: Project={self._quick_active_project_name} / "
            f"Character={getattr(self, '_quick_active_character_name', '未選択')} / "
            f"Prompt={getattr(self, '_quick_active_prompt_name', '未選択')}"
        )

        output_dir = project.get("output_dir") or ""
        summary_parts = [
            f"Project: {self._quick_active_project_name}",
            f"Character: {getattr(self, '_quick_active_character_name', '未選択')}",
        ]
        if output_dir:
            summary_parts.append(f"保存先: {output_dir}")
        if hasattr(self, "current_project_summary"):
            self.current_project_summary.set(" / ".join(summary_parts))

        self._refresh_generate_workflow_state()
        self.refresh_production_check()
        self.status.set(
            f"Project「{self._quick_active_project_name}」だけ適用しました。"
            "生成は開始していません。"
        )

    def apply_quick_character_only(self):
        character = self._quick_find_named_item("characters", self.quick_character.get())
        if not character:
            messagebox.showinfo("制作準備", "適用するCharacterを選択してください。")
            return

        if not self._project_apply_character_item(character):
            return

        self.active_character_id = character.get("id") or ""
        try:
            ws = self.repo.workspace()
            ws["current_character_id"] = self.active_character_id
            ws["active_character_id"] = self.active_character_id
            self.repo.save_all("workspace", ws)
        except Exception:
            pass

        self._quick_active_character_name = character.get("name") or "未選択"
        self._refresh_lora_tree()
        self._refresh_model_tree()
        self.quick_active_summary.set(
            f"現在適用中: Project={getattr(self, '_quick_active_project_name', '未選択')} / "
            f"Character={self._quick_active_character_name} / "
            f"Prompt={getattr(self, '_quick_active_prompt_name', '未選択')}"
        )
        self._mark_generate_saved()
        self._refresh_generate_workflow_state()
        self.refresh_production_check()
        self.status.set(
            f"Character「{self._quick_active_character_name}」だけ適用しました。"
            "生成は開始していません。"
        )

    def apply_quick_prompt_only(self):
        prompt_item = self._quick_find_named_item("prompt_library", self.quick_prompt.get())
        if not prompt_item:
            messagebox.showinfo("制作準備", "適用するPromptを選択してください。")
            return

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", prompt_item.get("prompt") or "")
        self.negative.delete("1.0", "end")
        self.negative.insert("1.0", prompt_item.get("negative_prompt") or "")

        self._quick_active_prompt_name = prompt_item.get("name") or "未選択"
        self.quick_active_summary.set(
            f"現在適用中: Project={getattr(self, '_quick_active_project_name', '未選択')} / "
            f"Character={getattr(self, '_quick_active_character_name', '未選択')} / "
            f"Prompt={self._quick_active_prompt_name}"
        )
        self._mark_generate_saved()
        self._refresh_generate_workflow_state()
        self.refresh_production_check()
        self.status.set(
            f"Prompt「{self._quick_active_prompt_name}」だけ適用しました。"
            "生成は開始していません。"
        )

    def _production_check_items(self):
        project = self._quick_find_named_item(
            "projects", self.quick_project.get()
        )
        character = self._quick_find_named_item(
            "characters", self.quick_character.get()
        )
        prompt_item = self._quick_find_named_item(
            "prompt_library", self.quick_prompt.get()
        )

        if project and character is None:
            linked_name = (project.get("character_name") or "").strip()
            if linked_name:
                character = self._quick_find_named_item(
                    "characters", linked_name
                )

        prompt_text = ""
        try:
            prompt_text = self.prompt.get("1.0", "end").strip()
        except Exception:
            pass

        model_name = ""
        try:
            model_name = self.model_combo.get().strip()
        except Exception:
            pass

        try:
            lora_ok = bool(self.active_loras)
        except Exception:
            lora_ok = False

        preset_name = ""
        try:
            preset_name = self.generate_preset_name.get().strip()
        except Exception:
            pass

        return [
            ("Project", bool(project)),
            ("Character", bool(character)),
            ("Prompt", bool(prompt_item or prompt_text)),
            ("Model", bool(model_name)),
            ("LoRA", lora_ok),
            ("Preset", bool(preset_name and preset_name != "未選択")),
        ]

    def refresh_production_check(self):
        if not hasattr(self, "production_check_var"):
            return
        checks = self._production_check_items()
        self.production_check_var.set(
            production_check_summary(checks)
        )

    def start_production_prepare(self):
        # Existing one-click application already owns the safe apply order:
        # Character defaults -> Project/workspace -> explicit Prompt.
        self.apply_generate_quick_setup()

        preset_name = ""
        try:
            preset_name = self.generate_preset_name.get().strip()
        except Exception:
            pass

        if preset_name and preset_name != "未選択":
            self.apply_selected_generate_preset()

        self.refresh_production_check()
        self._refresh_generate_workflow_state()

        checks = self._production_check_items()
        hard_missing, optional_missing = split_missing_checks(
            checks,
            {"Project", "Character", "Prompt", "Model"},
        )

        if hard_missing:
            self.status.set(
                "制作準備に不足があります: "
                + ", ".join(hard_missing)
                + "。生成は開始していません。"
            )
        else:
            msg = "制作準備が完了しました。生成は開始していません。"
            if optional_missing:
                msg += " 任意項目未設定: " + ", ".join(optional_missing)
            self.status.set(msg)

    def apply_generate_quick_setup(self):
        project = self._quick_find_named_item("projects", self.quick_project.get())
        character = self._quick_find_named_item("characters", self.quick_character.get())
        prompt_item = self._quick_find_named_item("prompt_library", self.quick_prompt.get())

        # If Project is selected and Character is not explicitly selected,
        # use the Character linked to that Project.
        if project and character is None:
            linked_name = (project.get("character_name") or "").strip()
            if linked_name:
                character = self._quick_find_named_item("characters", linked_name)
                if character:
                    self.quick_character.set(character.get("name") or linked_name)

        if not any((project, character, prompt_item)):
            messagebox.showinfo(
                "制作準備",
                "Project / Character / Prompt のいずれかを選択してください。"
            )
            return

        # Apply Character generation defaults first.
        if character:
            if not self._project_apply_character_item(character):
                return
            self.active_character_id = character.get("id") or ""

        # Project controls workspace/output linkage, but must not overwrite
        # the explicitly selected Character/Prompt after this point.
        if project:
            self.active_project_id = project.get("id") or ""

            if not character:
                linked_name = (project.get("character_name") or "").strip()
                linked_char = self._quick_find_named_item("characters", linked_name)
                if linked_char:
                    self.active_character_id = linked_char.get("id") or ""

            try:
                ws = self.repo.workspace()
                ws["current_project_id"] = self.active_project_id
                ws["active_project_id"] = self.active_project_id
                if self.active_character_id:
                    ws["current_character_id"] = self.active_character_id
                    ws["active_character_id"] = self.active_character_id
                self.repo.save_all("workspace", ws)
            except Exception:
                pass

        # Prompt Library is applied last so the explicitly chosen prompt wins.
        if prompt_item:
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", prompt_item.get("prompt") or "")
            self.negative.delete("1.0", "end")
            self.negative.insert("1.0", prompt_item.get("negative_prompt") or "")
        elif project:
            session = project.get("session") or {}
            if session:
                session_prompt = session.get("prompt") or ""
                session_negative = session.get("negative_prompt") or ""
                if session_prompt:
                    self.prompt.delete("1.0", "end")
                    self.prompt.insert("1.0", session_prompt)
                if session_negative:
                    self.negative.delete("1.0", "end")
                    self.negative.insert("1.0", session_negative)

        project_name = project.get("name") if project else "未選択"
        character_name = character.get("name") if character else "未選択"
        prompt_name = prompt_item.get("name") if prompt_item else "未選択"

        output_dir = project.get("output_dir") if project else ""
        model_text = ""
        try:
            model_text = self.model_combo.get().strip()
        except Exception:
            pass

        lora_text = "なし"
        try:
            if self.active_loras:
                lora_text = ", ".join(
                    f"{name}:{float(weight):g}"
                    for name, weight in self.active_loras.items()
                )
        except Exception:
            pass

        summary_parts = [
            f"Project: {project_name}",
            f"Character: {character_name}",
            f"Model: {model_text or '未取得'}",
            f"LoRA: {lora_text}",
        ]
        if output_dir:
            summary_parts.append(f"保存先: {output_dir}")
        self.current_project_summary.set(" / ".join(summary_parts))

        self._quick_active_project_name = project_name
        self._quick_active_character_name = character_name
        self._quick_active_prompt_name = prompt_name
        self._refresh_lora_tree()
        self._refresh_model_tree()

        if hasattr(self, "quick_active_summary"):
            self.quick_active_summary.set(
                f"現在適用中: Project={project_name} / "
                f"Character={character_name} / Prompt={prompt_name}"
            )

        self._mark_generate_saved()
        self._refresh_generate_workflow_state()
        self.refresh_production_check()

        self.status.set(
            f"制作準備を一括適用しました: Project={project_name} / "
            f"Character={character_name} / Prompt={prompt_name}。"
            "生成は開始していません。"
        )

    def _build_history(self):
        top = ttk.Frame(self.history)
        top.pack(fill="x")
        ttk.Button(top, text="更新", command=self.refresh_history).pack(side="left")
        self.history_info = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.history_info).pack(side="left", padx=10)

        cols = ("time","name","path")
        self.history_tree = ttk.Treeview(self.history, columns=cols, show="headings")
        self.history_tree.heading("time", text="更新日時")
        self.history_tree.heading("name", text="ファイル")
        self.history_tree.heading("path", text="保存場所")
        self.history_tree.column("time", width=150)
        self.history_tree.column("name", width=260)
        self.history_tree.column("path", width=700)
        self.history_tree.pack(fill="both", expand=True, pady=10)
        self.history_tree.bind("<Double-1>", self._open_history_item)


    def _build_studio_history(self):
        top = ttk.Frame(self.studio_history)
        top.pack(fill="x")

        ttk.Label(top, text="Project").pack(side="left")
        self.history_project_filter = tk.StringVar(value="すべて")
        self.history_project_combo = ttk.Combobox(
            top, textvariable=self.history_project_filter, state="readonly", width=22
        )
        self.history_project_combo.pack(side="left", padx=(6, 12))
        self.history_project_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_studio_history())

        ttk.Label(top, text="Character").pack(side="left")
        self.history_character_filter = tk.StringVar(value="すべて")
        self.history_character_combo = ttk.Combobox(
            top, textvariable=self.history_character_filter, state="readonly", width=18
        )
        self.history_character_combo.pack(side="left", padx=(6, 12))
        self.history_character_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_studio_history())

        ttk.Label(top, text="評価").pack(side="left")
        self.history_status_filter = tk.StringVar(value="すべて")
        self.history_status_combo = ttk.Combobox(
            top, textvariable=self.history_status_filter, state="readonly", width=12,
            values=("すべて", "未評価", "採用", "仮採用", "作業中", "不採用")
        )
        self.history_status_combo.pack(side="left", padx=(6, 12))
        self.history_status_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_studio_history())

        self.history_search = tk.StringVar()
        ttk.Label(top, text="検索").pack(side="left")
        ttk.Entry(top, textvariable=self.history_search, width=24).pack(side="left", padx=(6, 12))
        self.history_search.trace_add("write", lambda *_: self.refresh_studio_history())

        ttk.Button(top, text="更新", command=self.refresh_studio_history).pack(side="right")
        ttk.Button(
            top, text="選択画像を採用DBへ登録",
            command=self.history_adopt_selected
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            top, text="既存Studio生成をDB登録",
            command=self.history_import_existing_studio_outputs
        ).pack(side="right", padx=(0, 6))

        pane, left, right = make_list_detail_pane(
            self.studio_history,
            left_weight=3,
            right_weight=2,
            pady=(10, 6),
        )

        cols = ("status", "time", "project", "character", "model", "sampler", "size", "image")
        self.studio_history_tree = ttk.Treeview(
            left, columns=cols, show="headings", selectmode="browse"
        )
        for col, title, width in [
            ("status", "評価", 75),
            ("time", "日時", 135),
            ("project", "プロジェクト", 170),
            ("character", "キャラクター", 150),
            ("model", "モデル", 215),
            ("sampler", "Sampler", 120),
            ("size", "サイズ", 95),
            ("image", "画像", 220),
        ]:
            self.studio_history_tree.heading(col, text=title)
            self.studio_history_tree.column(col, width=width)
        self.studio_history_tree.pack(fill="both", expand=True)
        self.studio_history_tree.bind("<<TreeviewSelect>>", self._show_history_detail)
        self.studio_history_tree.bind("<Double-1>", lambda _e: self.open_selected_studio_image())

        self.studio_history_info = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.studio_history_info).pack(anchor="w", pady=(6, 0))

        edit = make_detail_box(right, "選択画像の管理", padding=8)

        self.history_preview_image = None
        self.history_preview_label = ttk.Label(
            edit, text="画像プレビュー", anchor="center"
        )
        self.history_preview_label.pack(fill="x", pady=(0, 8))

        self.history_status_edit = tk.StringVar(value="未評価")
        ttk.Label(edit, text="評価").pack(anchor="w")
        ttk.Combobox(
            edit, textvariable=self.history_status_edit, state="readonly",
            values=("未評価", "採用", "仮採用", "作業中", "不採用")
        ).pack(fill="x", pady=(2, 8))

        self.history_tags_edit = tk.StringVar()
        ttk.Label(edit, text="タグ（カンマ区切り）").pack(anchor="w")
        ttk.Entry(edit, textvariable=self.history_tags_edit).pack(fill="x", pady=(2, 8))

        ttk.Label(edit, text="メモ").pack(anchor="w")
        self.history_notes_edit = tk.Text(edit, height=4, wrap="word")
        self.history_notes_edit.pack(fill="x", pady=(2, 8))

        # 操作ボタンはPrompt欄より上に固定し、初期サイズでも常に見えるようにする。
        actions = ttk.Frame(edit)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(
            actions, text="保存",
            command=self.history_save_selected_metadata
        ).pack(side="left")
        ttk.Button(
            actions, text="Generateへ復元",
            command=self.load_selected_history
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Generateへ画像を送る",
            command=self.send_selected_history_image_to_generate
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions, text="採用DBへ登録",
            command=self.history_adopt_selected
        ).pack(side="left")
        ttk.Button(
            actions, text="画像を開く",
            command=self.open_selected_studio_image
        ).pack(side="right")

        ttk.Label(edit, text="Prompt / Negative").pack(anchor="w")
        self.studio_history_prompt = tk.Text(
            edit, height=9, wrap="word", state="disabled"
        )
        self.studio_history_prompt.pack(fill="both", expand=True, pady=(2, 0))

        self._history_db_records = []
        self._history_refresh_project_choices()
        self.refresh_studio_history()

    def _build_adopted(self):
        top = ttk.Frame(self.adopted_tab)
        top.pack(fill="x")

        ttk.Label(top, text="採用画像一覧", font=("", 13, "bold")).pack(side="left")

        ttk.Label(top, text="Project").pack(side="left", padx=(18, 0))
        self.adopted_project_filter = tk.StringVar(value="すべて")
        self.adopted_project_combo = ttk.Combobox(
            top, textvariable=self.adopted_project_filter,
            state="readonly", width=24
        )
        self.adopted_project_combo.pack(side="left", padx=(6, 12))
        self.adopted_project_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_adopted()
        )

        ttk.Label(top, text="Character").pack(side="left")
        self.adopted_character_filter = tk.StringVar(value="すべて")
        self.adopted_character_combo = ttk.Combobox(
            top, textvariable=self.adopted_character_filter,
            state="readonly", width=22
        )
        self.adopted_character_combo.pack(side="left", padx=(6, 12))
        self.adopted_character_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_adopted()
        )

        ttk.Label(top, text="検索").pack(side="left")
        self.adopted_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.adopted_search, width=24).pack(
            side="left", padx=(6, 12)
        )
        self.adopted_search.trace_add(
            "write", lambda *_: self.refresh_adopted()
        )

        ttk.Label(top, text="並び替え").pack(side="left")
        self.adopted_sort = tk.StringVar(value="採用日（新しい順）")
        self.adopted_sort_combo = ttk.Combobox(
            top,
            textvariable=self.adopted_sort,
            state="readonly",
            width=18,
            values=(
                "採用日（新しい順）",
                "採用日（古い順）",
                "名前順",
                "評価順",
                "お気に入り順",
                "Project順",
                "Character順",
            ),
        )
        self.adopted_sort_combo.pack(side="left", padx=(6, 12))
        self.adopted_sort_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_adopted()
        )

        ttk.Button(top, text="更新", command=self.refresh_adopted).pack(side="right")

        library_filters = ttk.Frame(self.adopted_tab)
        library_filters.pack(fill="x", pady=(6, 0))

        ttk.Label(library_filters, text="作品評価").pack(side="left")
        self.adopted_rating_filter = tk.StringVar(value="すべて")
        self.adopted_rating_combo = ttk.Combobox(
            library_filters,
            textvariable=self.adopted_rating_filter,
            state="readonly",
            width=12,
            values=("すべて", "マスター", "採用", "仮採用", "保留", "不採用"),
        )
        self.adopted_rating_combo.pack(side="left", padx=(6, 12))
        self.adopted_rating_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self.refresh_adopted()
        )

        self.adopted_favorite_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            library_filters,
            text="お気に入りのみ",
            variable=self.adopted_favorite_only,
            command=self.refresh_adopted
        ).pack(side="left")

        ttk.Label(
            library_filters,
            text="検索は空白区切りでAND検索（例: 壁紙 ゴシック）"
        ).pack(side="left", padx=(16, 0))

        pane, left, right = make_list_detail_pane(
            self.adopted_tab,
            left_weight=3,
            right_weight=2,
            pady=(10, 6),
        )

        cols = ("fav", "rating", "adopted_at", "project", "character", "tags", "image")
        self.adopted_tree = ttk.Treeview(
            left, columns=cols, show="headings", selectmode="browse"
        )
        for col, title, width in [
            ("fav", "★", 40),
            ("rating", "評価", 75),
            ("adopted_at", "採用日時", 135),
            ("project", "Project", 150),
            ("character", "Character", 130),
            ("tags", "タグ", 180),
            ("image", "画像", 250),
        ]:
            self.adopted_tree.heading(col, text=title)
            self.adopted_tree.column(col, width=width)
        self.adopted_tree.pack(fill="both", expand=True)
        self.adopted_tree.bind("<<TreeviewSelect>>", self._show_adopted_detail)
        self.adopted_tree.bind("<Double-1>", lambda _e: self.open_selected_adopted_image())

        self.adopted_info = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.adopted_info).pack(anchor="w", pady=(6, 0))

        detail = make_detail_box(right, "採用画像の詳細", padding=8)

        # プレビューは常時固定。下側だけタブで切り替える。
        preview_box = ttk.Frame(detail)
        preview_box.pack(fill="x", pady=(0, 8))

        self.adopted_preview_image = None
        self.adopted_preview_label = ttk.Label(
            preview_box,
            text="画像プレビュー",
            anchor="center",
            cursor="hand2"
        )
        self.adopted_preview_label.pack(fill="x")
        self.adopted_preview_label.bind(
            "<Double-1>",
            lambda _e: self.open_selected_adopted_viewer()
        )

        preview_actions = ttk.Frame(preview_box)
        preview_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(
            preview_actions, text="ビューア",
            command=self.open_selected_adopted_viewer
        ).pack(side="right")
        ttk.Button(
            preview_actions, text="画像を開く",
            command=self.open_selected_adopted_image
        ).pack(side="right", padx=(0, 6))

        detail_tabs = ttk.Notebook(detail)
        detail_tabs.pack(fill="both", expand=True)

        detail_main = ttk.Frame(detail_tabs, padding=10)
        detail_compare = ttk.Frame(detail_tabs, padding=10)
        detail_info = ttk.Frame(detail_tabs, padding=10)

        detail_tabs.add(detail_main, text="詳細")
        detail_tabs.add(detail_compare, text="比較")
        detail_tabs.add(detail_info, text="生成情報")

        # --- 詳細タブ ---
        self.adopted_name_edit = tk.StringVar()
        ttk.Label(detail_main, text="正式名称").pack(anchor="w")
        ttk.Entry(
            detail_main, textvariable=self.adopted_name_edit
        ).pack(fill="x", pady=(2, 8))

        meta_row = ttk.Frame(detail_main)
        meta_row.pack(fill="x", pady=(0, 8))

        ttk.Label(meta_row, text="作品評価").pack(side="left")
        self.adopted_rating_edit = tk.StringVar(value="採用")
        ttk.Combobox(
            meta_row,
            textvariable=self.adopted_rating_edit,
            state="readonly",
            width=10,
            values=("マスター", "採用", "仮採用", "保留", "不採用"),
        ).pack(side="left", padx=(6, 18))

        self.adopted_favorite_edit = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            meta_row,
            text="お気に入り",
            variable=self.adopted_favorite_edit
        ).pack(side="left")

        self.adopted_tags_edit = tk.StringVar()
        ttk.Label(detail_main, text="タグ（カンマ区切り）").pack(anchor="w")
        ttk.Entry(
            detail_main, textvariable=self.adopted_tags_edit
        ).pack(fill="x", pady=(2, 8))

        ttk.Label(detail_main, text="メモ").pack(anchor="w")
        self.adopted_notes_edit = tk.Text(detail_main, height=5, wrap="word")
        self.adopted_notes_edit.pack(fill="both", expand=True, pady=(2, 8))

        basic_actions = ttk.Frame(detail_main)
        basic_actions.pack(fill="x")
        ttk.Button(
            basic_actions, text="保存",
            command=self.save_selected_adopted_metadata
        ).pack(side="left")
        ttk.Button(
            basic_actions, text="採用解除",
            command=self.unadopt_selected_image
        ).pack(side="left", padx=(6, 0))

        # --- 比較タブ ---
        self.adopted_compare_a_status = tk.StringVar(value="A: 未設定")
        self.adopted_compare_b_status = tk.StringVar(value="B: 未設定")

        ttk.Label(
            detail_compare,
            textvariable=self.adopted_compare_a_status,
            anchor="w"
        ).pack(fill="x", pady=(0, 4))
        ttk.Label(
            detail_compare,
            textvariable=self.adopted_compare_b_status,
            anchor="w"
        ).pack(fill="x", pady=(0, 10))

        compare_select = ttk.Frame(detail_compare)
        compare_select.pack(fill="x")
        ttk.Button(
            compare_select, text="Aに設定",
            command=lambda: self.set_adopted_compare_slot("A")
        ).pack(side="left")
        ttk.Button(
            compare_select, text="Bに設定",
            command=lambda: self.set_adopted_compare_slot("B")
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            compare_select,
            text="A/B比較を開く",
            command=self.open_adopted_compare_viewer
        ).pack(side="right")

        ttk.Label(
            detail_compare,
            text="比較A/Bを設定後、左右比較ビューアを開けます。",
            justify="left"
        ).pack(anchor="w", pady=(12, 0))

        # --- 生成情報タブ ---
        self.adopted_detail_text = tk.Text(
            detail_info, height=16, wrap="word", state="disabled"
        )
        self.adopted_detail_text.pack(fill="both", expand=True)

        self._adopted_records = {}
        self._adopted_compare_a = None
        self._adopted_compare_b = None
        self.refresh_adopted()

    def _selected_adopted_record(self):
        sel = self.adopted_tree.selection() if hasattr(self, "adopted_tree") else ()
        if not sel:
            return None
        return self._adopted_records.get(sel[0])

    def _show_adopted_detail(self, _event=None):
        item = self._selected_adopted_record()
        if not item:
            return

        image_path = Path(item.get("image_path") or "")
        if not image_path.exists():
            self.adopted_preview_image = None
            self.adopted_preview_label.configure(
                image="", text=f"画像が見つかりません\n{image_path}"
            )
        else:
            try:
                img = tk.PhotoImage(file=str(image_path))
                w, h = img.width(), img.height()

                max_w, max_h = 360, 260
                factor = max(
                    1,
                    (w + max_w - 1) // max_w,
                    (h + max_h - 1) // max_h
                )
                if factor > 1:
                    img = img.subsample(factor, factor)

                self.adopted_preview_image = img
                self.adopted_preview_label.configure(image=img, text="")
            except Exception:
                self.adopted_preview_image = None
                self.adopted_preview_label.configure(
                    image="", text=f"プレビューを表示できません\n{image_path.name}"
                )

        project_name = self._adopted_entity_name("projects", item.get("project_id") or "")
        character_name = self._adopted_entity_name("characters", item.get("character_id") or "")
        tags = ", ".join(item.get("tags") or [])

        self.adopted_name_edit.set(item.get("name") or "")
        if hasattr(self, "adopted_rating_edit"):
            self.adopted_rating_edit.set(item.get("library_rating") or "採用")
        if hasattr(self, "adopted_favorite_edit"):
            self.adopted_favorite_edit.set(bool(item.get("favorite", False)))
        self.adopted_tags_edit.set(tags)
        self.adopted_notes_edit.delete("1.0", "end")
        self.adopted_notes_edit.insert("1.0", item.get("notes") or "")

        display_name = item.get("name") or image_path.name
        lines = [
            f"正式名称: {display_name}",
            f"画像: {image_path.name}",
            f"採用日時: {item.get('adopted_at') or ''}",
            f"Project: {project_name}",
            f"Character: {character_name}",
            f"モデル: {item.get('model') or ''}",
            f"Sampler: {item.get('sampler') or ''}",
            f"Steps: {item.get('steps', '')}",
            f"CFG: {item.get('cfg', '')}",
            f"Seed: {item.get('seed', '')}",
            "",
            "Prompt:",
            item.get("prompt") or "",
            "",
            "Negative:",
            item.get("negative_prompt") or "",
        ]
        self.adopted_detail_text.configure(state="normal")
        self.adopted_detail_text.delete("1.0", "end")
        self.adopted_detail_text.insert("1.0", "\n".join(lines))
        self.adopted_detail_text.configure(state="disabled")

    def save_selected_adopted_metadata(self):
        item = self._selected_adopted_record()
        if not item:
            messagebox.showinfo("採用DB", "保存する画像を選択してください。")
            return

        updated = dict(item)
        updated["name"] = self.adopted_name_edit.get().strip()
        updated["library_rating"] = (
            self.adopted_rating_edit.get()
            if hasattr(self, "adopted_rating_edit")
            else "採用"
        )
        updated["favorite"] = (
            bool(self.adopted_favorite_edit.get())
            if hasattr(self, "adopted_favorite_edit")
            else bool(updated.get("favorite", False))
        )
        updated["tags"] = [
            x.strip() for x in self.adopted_tags_edit.get().split(",")
            if x.strip()
        ]
        updated["notes"] = self.adopted_notes_edit.get("1.0", "end").strip()

        try:
            saved = self.repo.upsert_item("adopted", updated)
        except Exception as e:
            messagebox.showerror("採用DB", f"保存に失敗しました。\n{e}")
            return

        selected_id = saved.get("id") or updated.get("id")
        self.refresh_adopted()
        if selected_id and selected_id in self.adopted_tree.get_children():
            self.adopted_tree.selection_set(selected_id)
            self.adopted_tree.focus(selected_id)
            self._show_adopted_detail()

        self.status.set("作品ライブラリの評価・お気に入り・名称・タグ・メモを保存しました。")
        messagebox.showinfo("採用DB", "保存しました。")

    def unadopt_selected_image(self):
        item = self._selected_adopted_record()
        if not item:
            messagebox.showinfo("採用DB", "解除する画像を選択してください。")
            return

        image_name = Path(item.get("image_path") or "").name
        ok = messagebox.askyesno(
            "採用解除",
            "この画像を採用DBから外しますか？\n\n"
            f"{image_name}\n\n"
            "Historyと元画像ファイルは削除されません。"
        )
        if not ok:
            return

        adopted_id = str(item.get("id") or "")
        try:
            deleted = self.repo.delete_item("adopted", adopted_id)
        except Exception as e:
            messagebox.showerror("採用DB", f"採用解除に失敗しました。\n{e}")
            return

        if not deleted:
            messagebox.showwarning("採用DB", "対象データが見つかりませんでした。")
            self.refresh_adopted()
            return

        self.refresh_adopted()
        self.adopted_preview_image = None
        self.adopted_preview_label.configure(image="", text="画像プレビュー")
        self.adopted_name_edit.set("")
        if hasattr(self, "adopted_rating_edit"):
            self.adopted_rating_edit.set("採用")
        if hasattr(self, "adopted_favorite_edit"):
            self.adopted_favorite_edit.set(False)
        self.adopted_tags_edit.set("")
        self.adopted_notes_edit.delete("1.0", "end")
        self.adopted_detail_text.configure(state="normal")
        self.adopted_detail_text.delete("1.0", "end")
        self.adopted_detail_text.configure(state="disabled")

        self.status.set(f"採用解除しました: {image_name}")
        messagebox.showinfo(
            "採用解除",
            "採用DBから外しました。\n"
            "Historyと元画像ファイルは残っています。"
        )

    def set_adopted_compare_slot(self, slot):
        item = self._selected_adopted_record()
        if not item:
            messagebox.showinfo(
                "A/B比較",
                f"比較{slot}に設定する画像を選択してください。"
            )
            return

        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "A/B比較",
                f"画像ファイルが見つかりません。\n\n{path}"
            )
            return

        if slot == "A":
            self._adopted_compare_a = dict(item)
        else:
            self._adopted_compare_b = dict(item)

        self._refresh_generate_compare_status()
        self.status.set(
            f"比較{slot}に設定しました: {item.get('name') or path.name}"
        )

    def open_adopted_compare_viewer(self):
        a = getattr(self, "_adopted_compare_a", None)
        b = getattr(self, "_adopted_compare_b", None)

        if not a or not b:
            messagebox.showinfo(
                "A/B比較",
                "比較Aと比較Bの両方を設定してください。"
            )
            return

        path_a = Path(a.get("image_path") or "")
        path_b = Path(b.get("image_path") or "")
        if not path_a.exists() or not path_b.exists():
            messagebox.showwarning(
                "A/B比較",
                "比較対象の画像ファイルが見つかりません。"
            )
            return

        try:
            orig_a = tk.PhotoImage(file=str(path_a))
            orig_b = tk.PhotoImage(file=str(path_b))
        except Exception as e:
            messagebox.showerror(
                "A/B比較",
                f"画像を読み込めませんでした。\n{e}"
            )
            return

        win = tk.Toplevel(self)
        win.title("A/B比較ビューア")
        win.geometry("1450x820")
        win.transient(self)

        toolbar = ttk.Frame(win, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")

        zoom_var = tk.StringVar(value="Fit")
        sync_var = tk.BooleanVar(value=True)

        ttk.Label(toolbar, textvariable=zoom_var, width=12).pack(side="left")
        ttk.Button(toolbar, text="Fit", command=lambda: _draw_all(fit=True)).pack(side="left", padx=(0, 4))
        for p in (25, 50, 100, 200, 400):
            ttk.Button(
                toolbar,
                text=f"{p}%",
                command=lambda value=p: _draw_all(percent=value)
            ).pack(side="left", padx=(0, 4))

        ttk.Checkbutton(
            toolbar,
            text="同期ズーム",
            variable=sync_var
        ).pack(side="left", padx=(12, 0))

        ttk.Label(
            toolbar,
            text="ホイール: ズーム / 左ドラッグ: 移動"
        ).pack(side="left", padx=(12, 0))

        ttk.Button(
            toolbar, text="閉じる", command=win.destroy
        ).pack(side="right")
        ttk.Button(
            toolbar,
            text="Bを採用",
            command=lambda: self.adopt_compare_slot_from_generate("B")
        ).pack(side="right", padx=(0, 6))
        ttk.Button(
            toolbar,
            text="Aを採用",
            command=lambda: self.adopt_compare_slot_from_generate("A")
        ).pack(side="right", padx=(0, 6))

        pane = ttk.Panedwindow(win, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        left_box = ttk.LabelFrame(
            pane, text=f"A: {a.get('name') or path_a.name}", padding=4
        )
        right_box = ttk.LabelFrame(
            pane, text=f"B: {b.get('name') or path_b.name}", padding=4
        )
        pane.add(left_box, weight=1)
        pane.add(right_box, weight=1)

        state = {
            "percent": 100,
            "fit_mode": True,
            "a_display": None,
            "b_display": None,
        }
        zoom_steps = [25, 50, 100, 200, 400]

        def make_canvas(parent):
            frame = ttk.Frame(parent)
            frame.pack(fill="both", expand=True)
            canvas = tk.Canvas(frame, background="#202020", highlightthickness=0)
            xs = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
            ys = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            canvas.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            ys.grid(row=0, column=1, sticky="ns")
            xs.grid(row=1, column=0, sticky="ew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            return canvas

        canvas_a = make_canvas(left_box)
        canvas_b = make_canvas(right_box)

        def scaled_photo(src_img, percent):
            percent = max(25, min(400, int(percent)))
            if percent == 25:
                return src_img.subsample(4, 4)
            if percent == 50:
                return src_img.subsample(2, 2)
            if percent == 100:
                return src_img.copy()
            if percent == 200:
                return src_img.zoom(2, 2)
            if percent == 400:
                return src_img.zoom(4, 4)
            nearest = min(zoom_steps, key=lambda x: abs(x - percent))
            return scaled_photo(src_img, nearest)

        def draw_one(canvas, src_img, key, percent):
            canvas.delete("all")
            display = scaled_photo(src_img, percent)
            state[key] = display

            iw, ih = display.width(), display.height()
            vw, vh = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
            rw, rh = max(iw, vw), max(ih, vh)

            canvas.configure(scrollregion=(0, 0, rw, rh))
            canvas.create_image(
                rw / 2 if iw < vw else iw / 2,
                rh / 2 if ih < vh else ih / 2,
                image=display,
                anchor="center"
            )

        def fit_percent_for(src_img, canvas):
            cw = max(1, canvas.winfo_width() - 30)
            ch = max(1, canvas.winfo_height() - 30)
            ow = max(1, src_img.width())
            oh = max(1, src_img.height())
            candidates = [
                p for p in zoom_steps
                if ow * p / 100 <= cw and oh * p / 100 <= ch
            ]
            return max(candidates) if candidates else 25

        def _draw_all(percent=None, fit=False):
            win.update_idletasks()
            if fit:
                pa = fit_percent_for(orig_a, canvas_a)
                pb = fit_percent_for(orig_b, canvas_b)
                percent = min(pa, pb) if sync_var.get() else None
                state["fit_mode"] = True
            else:
                state["fit_mode"] = False

            if sync_var.get():
                p = percent if percent is not None else state["percent"]
                state["percent"] = p
                draw_one(canvas_a, orig_a, "a_display", p)
                draw_one(canvas_b, orig_b, "b_display", p)
                zoom_var.set(f"Fit ({p}%)" if fit else f"{p}%")
            else:
                if fit:
                    pa = fit_percent_for(orig_a, canvas_a)
                    pb = fit_percent_for(orig_b, canvas_b)
                    draw_one(canvas_a, orig_a, "a_display", pa)
                    draw_one(canvas_b, orig_b, "b_display", pb)
                    zoom_var.set(f"Fit (A:{pa}% / B:{pb}%)")
                else:
                    p = percent if percent is not None else state["percent"]
                    state["percent"] = p
                    draw_one(canvas_a, orig_a, "a_display", p)
                    draw_one(canvas_b, orig_b, "b_display", p)
                    zoom_var.set(f"{p}%")

        def next_zoom(current, direction):
            if direction > 0:
                larger = [p for p in zoom_steps if p > current]
                return min(larger) if larger else zoom_steps[-1]
            smaller = [p for p in zoom_steps if p < current]
            return max(smaller) if smaller else zoom_steps[0]

        def wheel(event):
            target = next_zoom(state["percent"], 1 if event.delta > 0 else -1)
            _draw_all(percent=target)

        def pan_start(event):
            event.widget.scan_mark(event.x, event.y)

        def pan_move(event):
            event.widget.scan_dragto(event.x, event.y, gain=1)

        for canvas in (canvas_a, canvas_b):
            canvas.bind("<MouseWheel>", wheel)
            canvas.bind("<ButtonPress-1>", pan_start)
            canvas.bind("<B1-Motion>", pan_move)

        win.after(100, lambda: _draw_all(fit=True))

    def open_selected_adopted_viewer(self):
        item = self._selected_adopted_record()
        if not item:
            messagebox.showinfo("採用DB", "ビューアで見る画像を選択してください。")
            return

        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "採用DB",
                f"画像ファイルが見つかりません。\n\n{path}"
            )
            return

        try:
            original = tk.PhotoImage(file=str(path))
        except Exception as e:
            messagebox.showerror(
                "画像ビューア",
                f"画像を読み込めませんでした。\n{e}"
            )
            return

        win = tk.Toplevel(self)
        win.title(f"画像ビューア - {item.get('name') or path.name}")
        win.geometry("1100x760")
        win.transient(self)

        toolbar = ttk.Frame(win, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")

        zoom_var = tk.StringVar(value="Fit")
        ttk.Label(toolbar, textvariable=zoom_var, width=12).pack(side="left")

        canvas_frame = ttk.Frame(win)
        canvas_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        canvas = tk.Canvas(
            canvas_frame,
            background="#202020",
            highlightthickness=0
        )
        xscroll = ttk.Scrollbar(
            canvas_frame, orient="horizontal", command=canvas.xview
        )
        yscroll = ttk.Scrollbar(
            canvas_frame, orient="vertical", command=canvas.yview
        )
        canvas.configure(
            xscrollcommand=xscroll.set,
            yscrollcommand=yscroll.set
        )

        canvas.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        state = {
            "original": original,
            "display": None,
            "percent": 100,
            "fit_mode": True,
            "image_id": None,
        }
        zoom_steps = [25, 50, 100, 200, 400]

        def _scaled_photo(percent):
            percent = max(25, min(400, int(percent)))
            src_img = state["original"]

            # Tk PhotoImage supports exact integer zoom/subsample.
            if percent == 25:
                return src_img.subsample(4, 4)
            if percent == 50:
                return src_img.subsample(2, 2)
            if percent == 100:
                return src_img.copy()
            if percent == 200:
                return src_img.zoom(2, 2)
            if percent == 400:
                return src_img.zoom(4, 4)

            # Fallback to nearest supported level.
            nearest = min(zoom_steps, key=lambda x: abs(x - percent))
            return _scaled_photo(nearest)

        def _draw(percent=None, fit=False):
            canvas.delete("all")

            if fit:
                cw = max(1, canvas.winfo_width() - 30)
                ch = max(1, canvas.winfo_height() - 30)
                ow = max(1, state["original"].width())
                oh = max(1, state["original"].height())

                # Choose the largest discrete scale that fits.
                candidates = [
                    p for p in zoom_steps
                    if ow * p / 100 <= cw and oh * p / 100 <= ch
                ]
                percent = max(candidates) if candidates else 25
                state["fit_mode"] = True
            else:
                percent = percent or state["percent"]
                state["fit_mode"] = False

            state["percent"] = percent
            display = _scaled_photo(percent)
            state["display"] = display

            iw, ih = display.width(), display.height()
            canvas.configure(scrollregion=(0, 0, iw, ih))

            cx = max(iw / 2, canvas.winfo_width() / 2)
            cy = max(ih / 2, canvas.winfo_height() / 2)
            image_id = canvas.create_image(
                cx, cy, image=display, anchor="center"
            )
            state["image_id"] = image_id

            # Include viewport in scroll region so small images stay centered.
            region_w = max(iw, canvas.winfo_width())
            region_h = max(ih, canvas.winfo_height())
            canvas.configure(scrollregion=(0, 0, region_w, region_h))

            zoom_var.set(
                f"Fit ({percent}%)" if state["fit_mode"] else f"{percent}%"
            )

        def _fit():
            win.update_idletasks()
            _draw(fit=True)

        def _actual():
            _draw(percent=100)

        def _zoom_to(percent):
            _draw(percent=percent)

        def _wheel(event):
            current = state["percent"]
            if event.delta > 0:
                larger = [p for p in zoom_steps if p > current]
                target = min(larger) if larger else zoom_steps[-1]
            else:
                smaller = [p for p in zoom_steps if p < current]
                target = max(smaller) if smaller else zoom_steps[0]
            _zoom_to(target)

        def _pan_start(event):
            canvas.scan_mark(event.x, event.y)

        def _pan_move(event):
            canvas.scan_dragto(event.x, event.y, gain=1)

        ttk.Button(toolbar, text="Fit", command=_fit).pack(side="left", padx=(0, 4))
        ttk.Button(toolbar, text="100%", command=_actual).pack(side="left", padx=(0, 10))

        for p in zoom_steps:
            ttk.Button(
                toolbar,
                text=f"{p}%",
                command=lambda value=p: _zoom_to(value)
            ).pack(side="left", padx=(0, 4))

        ttk.Label(
            toolbar,
            text="ホイール: ズーム / 左ドラッグ: 移動"
        ).pack(side="left", padx=(12, 0))

        ttk.Button(
            toolbar,
            text="閉じる",
            command=win.destroy
        ).pack(side="right")

        canvas.bind("<MouseWheel>", _wheel)
        canvas.bind("<ButtonPress-1>", _pan_start)
        canvas.bind("<B1-Motion>", _pan_move)
        canvas.bind(
            "<Configure>",
            lambda _e: _fit() if state["fit_mode"] else None
        )

        win.after(80, _fit)

    def open_selected_adopted_image(self):
        item = self._selected_adopted_record()
        if not item:
            messagebox.showinfo("採用DB", "開く画像を選択してください。")
            return
        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning("採用DB", f"画像ファイルが見つかりません。\n\n{path}")
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("採用DB", f"画像を開けませんでした。\n{e}")

    def _adopted_entity_name(self, kind, item_id):
        if not item_id:
            return "未設定"
        item = self.repo.get_item(kind, item_id)
        if not item:
            return "不明"
        return item.get("name") or item.get("display_name") or "名称なし"

    def refresh_adopted(self):
        if not hasattr(self, "adopted_tree"):
            return
        for iid in self.adopted_tree.get_children():
            self.adopted_tree.delete(iid)

        items = self.repo.list_items("adopted")
        self._adopted_records = {}

        # Refresh Project filter choices from currently known Projects.
        project_choices = ["すべて"]
        project_map = {}
        try:
            for p in self.repo.list_items("projects"):
                pid = str(p.get("id") or "")
                name = p.get("name") or p.get("display_name") or "名称なし"
                if pid:
                    project_map[pid] = name
                    if name not in project_choices:
                        project_choices.append(name)
        except Exception:
            pass

        if hasattr(self, "adopted_project_combo"):
            self.adopted_project_combo["values"] = project_choices
            if self.adopted_project_filter.get() not in project_choices:
                self.adopted_project_filter.set("すべて")

        character_choices = ["すべて"]
        character_map = {}
        try:
            for c in self.repo.list_items("characters"):
                cid = str(c.get("id") or "")
                name = c.get("name") or c.get("display_name") or "名称なし"
                if cid:
                    character_map[cid] = name
                    if name not in character_choices:
                        character_choices.append(name)
        except Exception:
            pass

        if hasattr(self, "adopted_character_combo"):
            self.adopted_character_combo["values"] = character_choices
            if self.adopted_character_filter.get() not in character_choices:
                self.adopted_character_filter.set("すべて")

        selected_project = (
            self.adopted_project_filter.get()
            if hasattr(self, "adopted_project_filter")
            else "すべて"
        )
        if selected_project != "すべて":
            items = [
                x for x in items
                if project_map.get(str(x.get("project_id") or ""), "未設定") == selected_project
            ]

        selected_character = (
            self.adopted_character_filter.get()
            if hasattr(self, "adopted_character_filter")
            else "すべて"
        )
        if selected_character != "すべて":
            items = [
                x for x in items
                if character_map.get(str(x.get("character_id") or ""), "未設定") == selected_character
            ]

        selected_rating = (
            self.adopted_rating_filter.get()
            if hasattr(self, "adopted_rating_filter")
            else "すべて"
        )
        if selected_rating != "すべて":
            items = [
                x for x in items
                if str(x.get("library_rating") or "採用") == selected_rating
            ]

        favorite_only = (
            self.adopted_favorite_only.get()
            if hasattr(self, "adopted_favorite_only")
            else False
        )
        if favorite_only:
            items = [x for x in items if bool(x.get("favorite", False))]

        query = (
            self.adopted_search.get().strip().lower()
            if hasattr(self, "adopted_search")
            else ""
        )
        if query:
            terms = [x for x in re.split(r"\s+", query) if x]

            def _adopted_search_text(x):
                tags = x.get("tags") or []
                if isinstance(tags, list):
                    tags = " ".join(str(v) for v in tags)
                return " ".join([
                    str(x.get("name") or ""),
                    str(tags or ""),
                    str(x.get("notes") or ""),
                    str(x.get("prompt") or ""),
                    str(x.get("negative_prompt") or ""),
                    str(x.get("library_rating") or "採用"),
                    str(Path(x.get("image_path") or "").name),
                ]).lower()

            items = [
                x for x in items
                if all(term in _adopted_search_text(x) for term in terms)
            ]

        sort_mode = (
            self.adopted_sort.get()
            if hasattr(self, "adopted_sort")
            else "採用日（新しい順）"
        )

        if sort_mode == "採用日（古い順）":
            items = sorted(
                items,
                key=lambda x: str(x.get("adopted_at") or x.get("created_at") or ""),
            )
        elif sort_mode == "名前順":
            items = sorted(
                items,
                key=lambda x: str(x.get("name") or Path(x.get("image_path") or "").name).lower(),
            )
        elif sort_mode == "評価順":
            rating_order = {
                "マスター": 5,
                "採用": 4,
                "仮採用": 3,
                "保留": 2,
                "不採用": 1,
            }
            items = sorted(
                items,
                key=lambda x: (
                    rating_order.get(str(x.get("library_rating") or "採用"), 0),
                    bool(x.get("favorite", False)),
                    str(x.get("adopted_at") or x.get("created_at") or ""),
                ),
                reverse=True,
            )
        elif sort_mode == "お気に入り順":
            items = sorted(
                items,
                key=lambda x: (
                    bool(x.get("favorite", False)),
                    str(x.get("adopted_at") or x.get("created_at") or ""),
                ),
                reverse=True,
            )
        elif sort_mode == "Project順":
            items = sorted(
                items,
                key=lambda x: project_map.get(str(x.get("project_id") or ""), "未設定").lower(),
            )
        elif sort_mode == "Character順":
            items = sorted(
                items,
                key=lambda x: character_map.get(str(x.get("character_id") or ""), "未設定").lower(),
            )
        else:
            items = sorted(
                items,
                key=lambda x: str(x.get("adopted_at") or x.get("created_at") or ""),
                reverse=True,
            )

        for item in items:
            iid = str(item.get("id") or "")
            if not iid:
                continue
            image_name = Path(item.get("image_path") or "").name
            self._adopted_records[iid] = item
            self.adopted_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    "★" if item.get("favorite", False) else "",
                    item.get("library_rating") or "採用",
                    item.get("adopted_at") or "",
                    self._adopted_entity_name("projects", item.get("project_id") or ""),
                    self._adopted_entity_name("characters", item.get("character_id") or ""),
                    ", ".join(item.get("tags") or []),
                    image_name,
                ),
            )

        self.adopted_info.set(f"表示 {len(items)} 件")

    def _history_items(self):
        return self.repo.list_items("history")

    def _history_project_name(self, project_id):
        if not project_id:
            return "未設定"
        item = self.repo.get_item("projects", project_id)
        return (item or {}).get("name") or "不明Project"

    def _history_current_project(self):
        pid = getattr(self, "active_project_id", "") or ""
        if not pid:
            try:
                ws = self.repo.workspace()
            except Exception:
                ws = {}
            pid = (
                ws.get("current_project_id")
                or ws.get("active_project_id")
                or ""
            )
        item = self.repo.get_item("projects", pid) if pid else None
        return pid, ((item or {}).get("name") or "")

    def _history_refresh_project_choices(self):
        if not hasattr(self, "history_project_combo"):
            return
        names = ["すべて", "未設定"]
        names.extend(
            x.get("name") for x in self.repo.list_items("projects")
            if x.get("name")
        )
        values = tuple(dict.fromkeys(names))
        self.history_project_combo["values"] = values
        if self.history_project_filter.get() not in values:
            self.history_project_filter.set("すべて")

    def _history_refresh_character_choices(self):
        if not hasattr(self, "history_character_combo"):
            return
        names = ["すべて", "未設定"]
        for character in self.repo.list_items("characters"):
            display_name = character.get("display_name") or character.get("name")
            if display_name:
                names.append(display_name)
        values = tuple(dict.fromkeys(names))
        self.history_character_combo["values"] = values
        if self.history_character_filter.get() not in values:
            self.history_character_filter.set("すべて")
    def _history_character_name(self, character_id):
        if not character_id:
            return "未設定"
        item = self.repo.get_item("characters", character_id)
        if not item:
            return "不明Character"
        return item.get("display_name") or item.get("name") or "名称なし"

    def _history_sha256(self, image_path):
        try:
            h = hashlib.sha256()
            with open(image_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    def _history_find_by_hash_or_path(self, image_hash, image_path):
        path_text = str(image_path)
        for item in self._history_items():
            if image_hash and item.get("image_hash") == image_hash:
                return item
            if item.get("image_path") == path_text:
                return item
        return None

    def _history_register_record(self, *, image_path, json_path=None, request=None,
                                 model="", info=None, project_id=None, character_id=None,
                                 created_at=None):
        image_path = Path(image_path)
        if not image_path.exists():
            return None, False

        image_hash = self._history_sha256(image_path)
        existing = self._history_find_by_hash_or_path(image_hash, image_path)
        if existing:
            return existing, False

        request = request or {}
        if project_id is None:
            project_id, _ = self._history_current_project()

        if character_id is None:
            character_id = getattr(self, "active_character_id", "") or ""
            if not character_id:
                try:
                    ws = self.repo.workspace()
                    character_id = (
                        ws.get("current_character_id")
                        or ws.get("active_character_id")
                        or ""
                    )
                except Exception:
                    character_id = ""

        active_character_name = ""
        if character_id:
            character_item = self.repo.get_item("characters", character_id)
            active_character_name = (
                (character_item or {}).get("display_name")
                or (character_item or {}).get("name")
                or ""
            )

        master = self._get_active_character_master()
        master_path = master.get("image_path") if isinstance(master, dict) else ""
        master_type = master.get("master_type") if isinstance(master, dict) else ""

        import time
        created_at = created_at or image_path.stat().st_mtime or time.time()

        item = {
            "id": "",
            "image_path": str(image_path),
            "json_path": str(json_path) if json_path else "",
            "image_hash": image_hash,
            "project_id": project_id or "",
            "character_id": character_id or "",
            "character_name": active_character_name or "",
            "status": "未評価",
            "tags": [],
            "notes": "",
            "favorite": False,
            "created_at": created_at,
            "prompt": request.get("prompt") or "",
            "negative_prompt": request.get("negative_prompt") or "",
            "model": model or "",
            "loras": [
                {"name": name, "weight": weight}
                for name, weight in self.active_loras.items()
            ] if hasattr(self, "active_loras") else [],
            "sampler": request.get("sampler_name") or "",
            "steps": request.get("steps", ""),
            "cfg": request.get("cfg_scale", ""),
            "width": request.get("width", ""),
            "height": request.get("height", ""),
            "seed": "",
            "master_image_path": master_path or "",
            "master_type": master_type or "",
            "master_character_name": active_character_name or "",
            "info": info if isinstance(info, (str, dict, list, int, float, type(None))) else str(info),
        }
        saved = self.repo.upsert_item("history", item)
        return saved, True

    def history_import_existing_studio_outputs(self):
        records = self._studio_history_records()
        added = 0
        for r in records:
            saved, created = self._history_register_record(
                image_path=r["image_path"],
                json_path=r["json_path"],
                request={
                    "prompt": r.get("prompt") or "",
                    "negative_prompt": r.get("negative_prompt") or "",
                    "sampler_name": r.get("sampler") or "",
                    "steps": r.get("steps", ""),
                    "cfg_scale": r.get("cfg", ""),
                    "width": r.get("width", ""),
                    "height": r.get("height", ""),
                },
                model=r.get("model") or "",
                created_at=r.get("mtime"),
            )
            if created:
                added += 1
        self.refresh_studio_history()
        messagebox.showinfo(
            "History",
            f"既存Studio生成から {added} 件を新規登録しました。\n"
            "同じ画像はハッシュまたはパスで重複登録していません。"
        )

    def _history_status_display(self, status):
        return {
            "未評価": "○ 未評価",
            "採用": "★ 採用",
            "仮採用": "△ 仮採用",
            "保留": "● 保留",
            "作業中": "● 作業中",
            "不採用": "× 不採用",
        }.get(status or "未評価", status or "未評価")

    def refresh_studio_history(self):
        if not hasattr(self, "studio_history_tree"):
            return

        self._history_refresh_project_choices()
        self._history_refresh_character_choices()

        project_filter = self.history_project_filter.get()
        character_filter = self.history_character_filter.get() if hasattr(self, "history_character_filter") else "すべて"
        status_filter = self.history_status_filter.get()
        q = self.history_search.get().strip().lower()

        items = list(self._history_items())
        items.sort(key=lambda x: float(x.get("created_at") or 0), reverse=True)

        visible = []
        for item in items:
            project_name = self._history_project_name(item.get("project_id"))
            status = item.get("status") or "未評価"
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [x.strip() for x in tags.split(",") if x.strip()]

            if project_filter != "すべて" and project_name != project_filter:
                continue
            if character_filter != "すべて" and self._history_character_name(item.get("character_id")) != character_filter:
                continue
            if status_filter != "すべて" and status != status_filter:
                continue

            hay = " ".join([
                project_name,
                status,
                item.get("model") or "",
                item.get("sampler") or "",
                item.get("prompt") or "",
                item.get("notes") or "",
                " ".join(tags),
                Path(item.get("image_path") or "").name,
            ]).lower()
            if q and q not in hay:
                continue

            visible.append(item)

        self._history_db_records = visible
        self.studio_history_tree.delete(*self.studio_history_tree.get_children())

        from datetime import datetime as _dt
        for item in visible:
            try:
                stamp = _dt.fromtimestamp(float(item.get("created_at") or 0)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                stamp = ""
            size_text = ""
            if item.get("width") or item.get("height"):
                size_text = f"{item.get('width','')}×{item.get('height','')}".strip("×")

            self.studio_history_tree.insert(
                "", "end", iid=item.get("id"),
                values=(
                    self._history_status_display(item.get("status") or "未評価"),
                    stamp,
                    self._history_project_name(item.get("project_id")),
                    self._history_character_name(item.get("character_id")),
                    item.get("model") or "",
                    item.get("sampler") or "",
                    size_text or "",
                    Path(item.get("image_path") or "").name,
                )
            )

        self.studio_history_info.set(f"表示 {len(visible)} / 全 {len(items)} 件")

        if visible:
            first_id = visible[0].get("id")
            if first_id in self.studio_history_tree.get_children():
                self.studio_history_tree.selection_set(first_id)
                self.studio_history_tree.focus(first_id)
                self._show_history_detail()
        else:
            self._clear_history_detail()

    def _selected_studio_record(self):
        sel = self.studio_history_tree.selection()
        if not sel:
            return None
        return self.repo.get_item("history", sel[0])

    def _clear_history_detail(self):
        if hasattr(self, "history_status_edit"):
            self.history_status_edit.set("未評価")
            self.history_tags_edit.set("")
            self.history_notes_edit.delete("1.0", "end")
            self.studio_history_prompt.configure(state="normal")
            self.studio_history_prompt.delete("1.0", "end")
            self.studio_history_prompt.configure(state="disabled")
            self.history_preview_image = None
            if hasattr(self, "history_preview_label"):
                self.history_preview_label.configure(image="", text="画像プレビュー")

    def _history_show_preview(self, image_path):
        path = Path(image_path or "")
        if not path.exists():
            self.history_preview_image = None
            self.history_preview_label.configure(
                image="", text=f"画像が見つかりません\n{path}"
            )
            return
        try:
            img = tk.PhotoImage(file=str(path))
            w, h = img.width(), img.height()

            # Keep History preview compact; stdlib Tk only, no Pillow/API.
            max_w, max_h = 330, 260
            factor = max(
                1,
                (w + max_w - 1) // max_w,
                (h + max_h - 1) // max_h
            )
            if factor > 1:
                img = img.subsample(factor, factor)

            self.history_preview_image = img
            self.history_preview_label.configure(image=img, text="")
        except Exception as e:
            self.history_preview_image = None
            self.history_preview_label.configure(
                image="", text=f"プレビューを表示できません\n{Path(path).name}"
            )

    def _show_history_detail(self, _event=None):
        r = self._selected_studio_record()
        if not r:
            self._clear_history_detail()
            return

        self._history_show_preview(r.get("image_path") or "")
        self.history_status_edit.set(r.get("status") or "未評価")
        tags = r.get("tags") or []
        if isinstance(tags, list):
            tags = ", ".join(tags)
        self.history_tags_edit.set(tags)

        self.history_notes_edit.delete("1.0", "end")
        self.history_notes_edit.insert("1.0", r.get("notes") or "")

        adopted_record = self.repo.find_adopted_by_history(r.get("id") or "")
        adopted_status = "採用済み" if adopted_record else "未採用"
        adopted_theme = (
            (adopted_record or {}).get("library_theme")
            or r.get("library_theme")
            or "-"
        )
        adopted_version = (
            (adopted_record or {}).get("library_version")
            or r.get("library_version")
            or "-"
        )
        adopted_at = (
            (adopted_record or {}).get("adopted_at")
            or "-"
        )

        master_path = r.get("master_image_path") or ""
        master_name = Path(master_path).name if master_path else ""
        master_type = r.get("master_type") or ""
        master_label = (
            f"{master_type} / {master_name}" if master_name else "なし"
        )

        detail = (
            f"Project: {self._history_project_name(r.get('project_id'))}\n"
            f"Character: {self._history_character_name(r.get('character_id'))}\n"
            f"Master: {master_label}\n"
            f"制作状態: {r.get('status') or '未評価'}\n"
            f"登録日時: {r.get('created_at') or ''}\n"
            f"採用情報: {adopted_status}\n"
            f"採用テーマ: {adopted_theme}\n"
            f"採用バージョン: {adopted_version}\n"
            f"採用日時: {adopted_at}\n\n"
            "Prompt:\n" + (r.get("prompt") or "") +
            "\n\nNegative:\n" + (r.get("negative_prompt") or "") +
            "\n\nModel:\n" + (r.get("model") or "") +
            "\n\nSampler / Steps / CFG / Size:\n"
            + f"{r.get('sampler','')} / {r.get('steps','')} / {r.get('cfg','')} / "
              f"{r.get('width','')}×{r.get('height','')}"
        )

        self.studio_history_prompt.configure(state="normal")
        self.studio_history_prompt.delete("1.0", "end")
        self.studio_history_prompt.insert("1.0", detail)
        self.studio_history_prompt.configure(state="disabled")

    def history_save_selected_metadata(self):
        r = self._selected_studio_record()
        if not r:
            messagebox.showinfo("History", "更新する画像を選択してください。")
            return

        updated = dict(r)
        updated["status"] = self.history_status_edit.get().strip() or "未評価"
        updated["tags"] = [
            x.strip() for x in self.history_tags_edit.get().split(",")
            if x.strip()
        ]
        updated["notes"] = self.history_notes_edit.get("1.0", "end").strip()

        self.repo.upsert_item("history", updated)
        selected_id = updated.get("id")
        self.refresh_studio_history()
        if selected_id in self.studio_history_tree.get_children():
            self.studio_history_tree.selection_set(selected_id)
            self.studio_history_tree.focus(selected_id)
            self._show_history_detail()
        self.status.set(
            f"Historyを更新しました: {Path(updated.get('image_path') or '').name}"
        )

    def history_adopt_selected(self):
        r = self._selected_studio_record()
        if not r:
            messagebox.showinfo("History", "採用する画像を選択してください。")
            return

        # Save any metadata currently edited in the History pane before adoption.
        updated = dict(r)
        updated["status"] = "採用"
        updated["tags"] = [
            x.strip() for x in self.history_tags_edit.get().split(",")
            if x.strip()
        ]
        updated["notes"] = self.history_notes_edit.get("1.0", "end").strip()

        theme_version = self._ask_adopted_theme_and_version(
            default_name=updated.get("name") or Path(updated.get("image_path") or "").stem
        )
        if theme_version is None or theme_version.get("theme") is None:
            return
        if theme_version.get("theme"):
            updated["library_theme"] = theme_version["theme"]
        if theme_version.get("version"):
            updated["library_version"] = theme_version["version"]

        self.repo.upsert_item("history", updated)

        from datetime import datetime as _dt
        adopted_at = _dt.now().isoformat(timespec="seconds")
        try:
            adopted, created = self.repo.adopt_history_item(
                updated.get("id") or "",
                adopted_at=adopted_at,
            )
        except Exception as e:
            messagebox.showerror("採用DB", f"採用DBへの登録に失敗しました。\n{e}")
            return

        selected_id = updated.get("id")
        self.refresh_studio_history()
        if selected_id in self.studio_history_tree.get_children():
            self.studio_history_tree.selection_set(selected_id)
            self.studio_history_tree.focus(selected_id)
            self._show_history_detail()

        image_name = Path(updated.get("image_path") or "").name
        if created:
            self.status.set(f"採用DBへ登録しました: {image_name}")
            messagebox.showinfo("採用DB", f"採用DBへ登録しました。\n{image_name}")
        else:
            self.status.set(f"採用DBは登録済みです: {image_name}")
            messagebox.showinfo(
                "採用DB",
                f"この画像は採用DBに登録済みです。\n重複登録はしていません。\n{image_name}"
            )

    def _studio_output_root(self) -> Path:
        return Path(self.setting_vars["forge_root"].get().strip()) / "outputs" / "selfie-ai-studio"

    def _studio_history_records(self):
        root = self._studio_output_root()
        records = []
        if not root.exists():
            return records
        for jp in root.glob("selfie_*.json"):
            try:
                data = json.loads(jp.read_text(encoding="utf-8"))
                req = data.get("request") or {}
                image = jp.with_suffix(".png")
                records.append({
                    "json_path": jp,
                    "image_path": image,
                    "mtime": jp.stat().st_mtime,
                    "model": data.get("model") or "",
                    "prompt": req.get("prompt") or "",
                    "negative_prompt": req.get("negative_prompt") or "",
                    "sampler": req.get("sampler_name") or "",
                    "steps": req.get("steps", ""),
                    "cfg": req.get("cfg_scale", ""),
                    "width": req.get("width", ""),
                    "height": req.get("height", ""),
                })
            except Exception:
                continue
        records.sort(key=lambda r: r["mtime"], reverse=True)
        return records

    def load_selected_history(self):
        r = self._selected_studio_record()
        if not r:
            messagebox.showinfo("History", "読み込む履歴を選択してください。")
            return

        self._import_loras_from_prompt(r.get("prompt") or "")
        visible_prompt = self._clean_prompt_lora_tags(r.get("prompt") or "")

        # Prefer stored managed-LoRA list if present.
        stored_loras = r.get("loras") or []
        if stored_loras:
            self.active_loras.clear()
            for x in stored_loras:
                if isinstance(x, dict) and x.get("name"):
                    try:
                        weight = float(x.get("weight", 1.0))
                    except Exception:
                        weight = 1.0
                    self.active_loras[x["name"]] = weight

        self.prompt.delete("1.0","end")
        self.prompt.insert("1.0", visible_prompt)
        self._refresh_lora_tree()

        self.negative.delete("1.0","end")
        self.negative.insert("1.0", r.get("negative_prompt") or "")

        try:
            if r.get("steps", "") != "":
                self.steps.set(int(r["steps"]))
            if r.get("cfg", "") != "":
                self.cfg.set(float(r["cfg"]))
            if r.get("width", "") != "":
                self.width.set(int(r["width"]))
            if r.get("height", "") != "":
                self.height.set(int(r["height"]))
            if r.get("sampler"):
                self.sampler.set(r["sampler"])
        except Exception:
            pass

        model = (r.get("model") or "").strip()
        if model:
            try:
                values = list(self.model_combo["values"])
                for i, title in enumerate(values):
                    if title == model or self._char_normalize_model_name(title) == self._char_normalize_model_name(model):
                        self.model_combo.current(i)
                        break
            except Exception:
                pass

        self.status.set(
            f"History「{Path(r.get('image_path') or '').name}」の設定を生成タブへ読み込みました。生成は開始していません。"
        )
        try:
            self.tabs.select(self.generate)
        except Exception:
            pass

    def send_selected_history_image_to_generate(self):
        record = self._selected_studio_record()
        if not record:
            messagebox.showinfo("History", "送る画像を選択してください。")
            return

        path = Path(record.get("image_path") or "")
        if not path.is_file():
            messagebox.showwarning(
                "History",
                f"画像ファイルが見つかりません。\n\n{path}"
            )
            return

        self._show_record_in_generate_detail(record, refresh_gallery=False)
        try:
            self.tabs.select(self.generate)
        except Exception:
            pass
        self.status.set(
            f"History画像「{path.name}」をGenerateの選択画像詳細へ送りました。"
            "画像や生成条件は変更していません。"
        )

    def open_selected_studio_image(self):
        r = self._selected_studio_record()
        if not r:
            messagebox.showinfo("History", "開く画像を選択してください。")
            return
        path = Path(r.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "History",
                f"画像ファイルが見つかりません。\n\n{path}\n\n"
                "History DBは残しています。ファイルの移動・削除は行っていません。"
            )
            return
        os.startfile(str(path))


    def _build_core(self):
        ttk.Label(self.core_tab, text="Studio Core", font=("", 14, "bold")).pack(anchor="w")
        ttk.Label(
            self.core_tab,
            text="Character / Project / Preset / Workspace の構造化データ基盤です。"
        ).pack(anchor="w", pady=(2, 8))

        self.core_tree = ttk.Treeview(
            self.core_tab,
            columns=("db", "count", "path", "backup"),
            show="headings"
        )
        for col, title, width in [
            ("db", "DB", 130),
            ("count", "件数", 80),
            ("path", "保存先", 650),
            ("backup", "バックアップ", 130),
        ]:
            self.core_tree.heading(col, text=title)
            self.core_tree.column(col, width=width)
        self.core_tree.pack(fill="both", expand=True)

        row = ttk.Frame(self.core_tab)
        row.pack(fill="x", pady=10)
        ttk.Button(row, text="Core状態更新", command=self.refresh_core_status).pack(side="left")
        ttk.Button(row, text="データフォルダを開く", command=self.open_core_data_dir).pack(side="left", padx=6)

        ttk.Label(
            self.core_tab,
            text=(
                "※ v0.6では基盤作成のみ。既存のモデル・LoRA・生成画像を移動しません。"
                " DB保存は一時ファイル検証→バックアップ→置換の順で行います。"
            ),
            wraplength=1000
        ).pack(anchor="w")

        self.refresh_core_status()

    def refresh_core_status(self):
        if not hasattr(self, "core_tree"):
            return
        self.core_tree.delete(*self.core_tree.get_children())
        paths = self.repo.db_paths()
        for name in ("characters", "projects", "presets", "prompt_library", "workspace"):
            path = paths[name]
            try:
                count = self.repo.count(name)
                backup = path.with_suffix(path.suffix + ".bak")
                backup_status = "あり" if backup.exists() else "未作成"
                self.core_tree.insert(
                    "", "end",
                    values=(name, count, str(path), backup_status)
                )
            except Exception as e:
                self.core_tree.insert(
                    "", "end",
                    values=(name, "ERR", str(path), str(e))
                )

    def open_core_data_dir(self):
        try:
            import os
            path = next(iter(self.repo.db_paths().values())).parent
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("Core", str(e))

    def save_ai_api_settings(self):
        data = {
            "enabled": bool(self.ai_api_enabled.get()),
            "provider": self.ai_api_provider.get().strip() or "OpenAI",
            "model": (
                self.ai_api_model.get().strip()
                or "gpt-5.6-luna"
            ),
            "base_url": (
                self.ai_api_base_url.get().strip()
                or "https://api.openai.com/v1"
            ),
            "api_key": self.ai_api_key.get().strip(),
        }
        try:
            save_api_config(self.ai_api_config_path, data)
            self._ai_api_config = load_api_config(
                self.ai_api_config_path
            )
        except Exception as e:
            messagebox.showerror(
                "AI API",
                f"設定の保存に失敗しました。\n{e}"
            )
            return

        key, source = effective_api_key(
            self._ai_api_config,
            self.ai_api_key.get()
        )
        self.ai_api_status.set(
            "AI API: 保存済み / Key="
            + masked_key(key)
            + f" / {source}"
        )

        if hasattr(self, "ai_assistant_mode"):
            self.ai_assistant_mode.set(
                "OpenAI API"
                if self.ai_api_enabled.get()
                else "ローカル補助"
            )
        self.status.set("AI API設定を保存しました。")

    def test_ai_api_connection(self):
        model = (
            self.ai_api_model.get().strip()
            or "gpt-5.6-luna"
        )
        base_url = (
            self.ai_api_base_url.get().strip()
            or "https://api.openai.com/v1"
        )
        cfg = load_api_config(self.ai_api_config_path)
        api_key, source = effective_api_key(
            cfg,
            self.ai_api_key.get()
        )

        if not api_key:
            self.ai_api_status.set("AI API: API Key未設定")
            messagebox.showinfo(
                "AI API",
                "API Keyを設定してから接続テストを行ってください。"
            )
            return

        self.ai_api_status.set("AI API: 接続テスト中…")

        def work():
            started = time.perf_counter()
            result = test_openai_connection(
                api_key,
                model,
                base_url
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            self.after(
                0,
                lambda: self.ai_api_status.set(
                    f"AI API: 接続OK / {model} / {latency_ms}ms"
                )
            )
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "AI API",
                    "OpenAI APIへ接続できました。\n\n"
                    f"Model: {model}\n"
                    f"API: OK\n"
                    f"Latency: {latency_ms} ms\n"
                    f"Key: {source}\n"
                    f"応答: {result}"
                )
            )

        def runner():
            try:
                work()
            except Exception as e:
                self.after(
                    0,
                    lambda err=str(e):
                        self.ai_api_status.set(
                            "AI API: 接続失敗"
                        )
                )
                self.after(
                    0,
                    lambda err=str(e):
                        messagebox.showerror(
                            "AI API",
                            err
                        )
                )

        threading.Thread(
            target=runner,
            daemon=True
        ).start()

    def run_release_self_check(self):
        checks = []

        # Core folders/files
        try:
            shared_root = Path(self.shared_root)
            checks.append(
                check_path(shared_root, "Shared root")
            )
            checks.append(
                check_path(
                    shared_root / "Data",
                    "Shared Data folder"
                )
            )
        except Exception:
            checks.append({
                "label": "Shared root",
                "ok": False,
                "detail": "shared_root取得失敗",
            })

        # Major tabs / widgets
        for attr, label in (
            ("generate", "Generate tab"),
            ("project", "Project tab"),
            ("character", "Character tab"),
            ("prompt_library", "Prompt Library tab"),
            ("prompt_builder", "Prompt Builder tab"),
            ("ai_assistant", "AI Assistant tab"),
            ("image_review", "画像解析 tab"),
            ("adopted_tab", "採用DB tab"),
        ):
            checks.append(
                check_attr(self, attr, label)
            )

        # Major actions
        for name, label in (
            ("generate_image", "1枚生成"),
            ("start_generation_queue", "連続生成"),
            ("stop_generation_queue", "連続生成停止"),
            ("start_production_prepare", "制作開始"),
            ("prompt_builder_apply_to_generate", "Prompt Builder反映"),
            ("ai_assistant_analyze", "AI Assistant解析"),
            ("image_review_ai_analyze", "AI画像解析"),
            ("open_adopted_compare_viewer", "A/B比較"),
            ("adopt_latest_generated_image", "最新生成採用"),
            ("save_current_project_session", "制作セッション保存"),
            ("restore_current_project_session", "制作セッション復元"),
        ):
            checks.append(
                check_callable(self, name, label)
            )

        # Repositories / data access smoke tests
        try:
            for collection in (
                "projects",
                "characters",
                "prompt_library",
                "history",
                "adopted",
            ):
                self.repo.list_items(collection)
                checks.append({
                    "label": f"DB:{collection}",
                    "ok": True,
                    "detail": "read ok",
                })
        except Exception as e:
            checks.append({
                "label": "Repository read",
                "ok": False,
                "detail": str(e),
            })

        # Compile-like runtime support file presence
        for filename in (
            "studio_helpers.py",
            "generate_support.py",
            "prompt_builder_support.py",
            "prompt_assistant_support.py",
            "ai_api_config.py",
            "openai_api_client.py",
            "image_review_support.py",
            "release_check.py",
        ):
            checks.append(
                check_path(
                    Path(__file__).parent / filename,
                    filename
                )
            )

        passed, failed, total = summarize_checks(checks)

        if failed:
            self.release_check_status.set(
                f"完成チェック: {passed}/{total} OK / {failed} NG"
            )
        else:
            self.release_check_status.set(
                f"完成チェック: {passed}/{total} OK"
            )

        win = tk.Toplevel(self)
        win.title("v1.5 完成チェック結果")
        win.geometry("900x620")
        win.transient(self)

        text = tk.Text(
            win,
            wrap="word",
            state="normal"
        )
        text.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

        lines = [
            f"v1.5 完成チェック",
            f"OK: {passed} / NG: {failed} / Total: {total}",
            "",
        ]
        for item in checks:
            mark = "OK" if item.get("ok") else "NG"
            lines.append(
                f"[{mark}] {item.get('label')}  {item.get('detail')}"
            )

        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")

        ttk.Button(
            win,
            text="閉じる",
            command=win.destroy
        ).pack(pady=(0, 10))

        if failed:
            self.status.set(
                "v1.5完成チェックでNG項目があります。"
            )
        else:
            self.status.set(
                "v1.5完成チェックはすべてOKです。"
            )

    def _build_settings(self):

        release_box = ttk.LabelFrame(
            self.settings_tab,
            text="v1.5 完成チェック",
            padding=8
        )
        release_box.pack(fill="x", pady=(0, 10))

        self.release_check_status = tk.StringVar(
            value="完成チェック: 未実行"
        )

        ttk.Button(
            release_box,
            text="完成チェック実行",
            command=self.run_release_self_check
        ).pack(side="left")

        ttk.Label(
            release_box,
            textvariable=self.release_check_status
        ).pack(side="left", padx=(12, 0))


        api_box = ttk.LabelFrame(
            self.settings_tab,
            text="AI API",
            padding=8
        )
        api_box.pack(fill="x", pady=(8, 10))

        self.ai_api_config_path = (
            Path(self.shared_root) / "Data" / "ai_api_config.json"
        )
        self._ai_api_config = load_api_config(self.ai_api_config_path)

        self.ai_api_enabled = tk.BooleanVar(
            value=bool(self._ai_api_config.get("enabled", False))
        )
        self.ai_api_provider = tk.StringVar(
            value=str(self._ai_api_config.get("provider") or "OpenAI")
        )
        self.ai_api_model = tk.StringVar(
            value=str(
                self._ai_api_config.get("model")
                or "gpt-5.6-luna"
            )
        )
        self.ai_api_base_url = tk.StringVar(
            value=str(
                self._ai_api_config.get("base_url")
                or "https://api.openai.com/v1"
            )
        )
        self.ai_api_key = tk.StringVar(
            value=str(self._ai_api_config.get("_api_key") or "")
        )
        self.ai_api_status = tk.StringVar(
            value="AI API: 未確認"
        )

        ttk.Checkbutton(
            api_box,
            text="AI APIを使用する",
            variable=self.ai_api_enabled
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(api_box, text="Provider", width=12).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Combobox(
            api_box,
            textvariable=self.ai_api_provider,
            state="readonly",
            values=("OpenAI",),
            width=18
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))

        ttk.Label(api_box, text="Model", width=12).grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Combobox(
            api_box,
            textvariable=self.ai_api_model,
            values=(
                "gpt-5.6-luna",
                "gpt-5.6-terra",
                "gpt-5.6-sol",
                "gpt-5.6",
            ),
        ).grid(row=2, column=1, sticky="ew", pady=(6, 0))

        ttk.Label(api_box, text="API Key", width=12).grid(
            row=3, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(
            api_box,
            textvariable=self.ai_api_key,
            show="*"
        ).grid(row=3, column=1, sticky="ew", pady=(6, 0))

        ttk.Label(api_box, text="Base URL", width=12).grid(
            row=4, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(
            api_box,
            textvariable=self.ai_api_base_url
        ).grid(row=4, column=1, sticky="ew", pady=(6, 0))

        api_actions = ttk.Frame(api_box)
        api_actions.grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        ttk.Button(
            api_actions,
            text="保存",
            command=self.save_ai_api_settings
        ).pack(side="left")

        ttk.Button(
            api_actions,
            text="接続テスト",
            command=self.test_ai_api_connection
        ).pack(side="left", padx=(6, 0))

        ttk.Label(
            api_actions,
            textvariable=self.ai_api_status
        ).pack(side="left", padx=(12, 0))

        ttk.Label(
            api_box,
            text=(
                "※ API KeyはWindows DPAPIで暗号化保存。"
                " OPENAI_API_KEY環境変数がある場合はそちらを優先します。"
            ),
            anchor="w"
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(7, 0)
        )

        api_box.columnconfigure(1, weight=1)

        self.setting_vars = {}

        ttk.Label(
            self.settings_tab,
            text="設定",
            font=("", 16, "bold")
        ).pack(anchor="w", pady=(0, 10))

        appearance_box = ttk.LabelFrame(
            self.settings_tab, text="表示", padding=10
        )
        appearance_box.pack(fill="x", pady=(0, 10))
        font_row = ttk.Frame(appearance_box)
        font_row.pack(fill="x")
        ttk.Label(font_row, text="UI Font", width=24).pack(side="left")
        self.ui_font_combo = ttk.Combobox(
            font_row,
            textvariable=self.ui_font_name,
            values=ui_font_choices(),
            state="readonly",
            width=24,
        )
        self.ui_font_combo.pack(side="left")
        self.ui_font_combo.bind(
            "<<ComboboxSelected>>", self._ui_font_selected
        )
        self.ui_font_status_var = tk.StringVar(
            value=(
                "現在のUIフォントを使用中"
                if self._ui_font_available
                else "フォント未検出: Current / Defaultで表示中"
            )
        )
        ttk.Label(
            font_row,
            textvariable=self.ui_font_status_var,
            style="Muted.TLabel",
        ).pack(side="left", padx=(12, 0))
        ttk.Label(
            appearance_box,
            text="UI Themeとは独立して保存されます。Prompt本文のフォントは変更しません。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        forge_box = ttk.LabelFrame(
            self.settings_tab, text="Forge", padding=10
        )
        forge_box.pack(fill="x", pady=(0, 10))

        for key, label in [
            ("forge_root", "Forge appフォルダ"),
            ("forge_url", "Forge API URL"),
        ]:
            row = ttk.Frame(forge_box)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=24).pack(side="left")
            var = tk.StringVar()
            self.setting_vars[key] = var
            ttk.Entry(
                row, textvariable=var
            ).pack(side="left", fill="x", expand=True)
            if key == "forge_root":
                ttk.Button(
                    row, text="参照",
                    command=lambda v=var: self.choose_dir(v)
                ).pack(side="left", padx=(6, 0))

        storage_box = ttk.LabelFrame(
            self.settings_tab, text="ストレージ / NAS", padding=10
        )
        storage_box.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(storage_box)
        row.pack(fill="x", pady=4)
        ttk.Label(
            row, text="NASモデル保管フォルダ", width=24
        ).pack(side="left")
        nas_var = tk.StringVar()
        self.setting_vars["nas_models_dir"] = nas_var
        ttk.Entry(
            row, textvariable=nas_var
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            row, text="参照",
            command=lambda v=nas_var: self.choose_dir(v)
        ).pack(side="left", padx=(6, 0))

        backup_box = ttk.LabelFrame(
            self.settings_tab, text="バックアップ", padding=10
        )
        backup_box.pack(fill="x", pady=(0, 10))

        ttk.Label(
            backup_box,
            text="Project / Character / Prompt Library / History / 採用DB などの共通DataをZIP保存します。"
        ).pack(anchor="w")

        backup_actions = ttk.Frame(backup_box)
        backup_actions.pack(fill="x", pady=(8, 0))
        ttk.Button(
            backup_actions,
            text="今すぐバックアップ",
            command=self.backup_shared_data
        ).pack(side="left")
        ttk.Button(
            backup_actions,
            text="バックアップから復元",
            command=self.restore_shared_data_from_backup
        ).pack(side="left", padx=(6, 0))
        self.backup_status_var = tk.StringVar(value="")
        ttk.Label(
            backup_actions,
            textvariable=self.backup_status_var
        ).pack(side="left", padx=(10, 0))

        diag_box = ttk.LabelFrame(
            self.settings_tab, text="環境診断", padding=10
        )
        diag_box.pack(fill="both", expand=True, pady=(0, 10))

        diag_actions = ttk.Frame(diag_box)
        diag_actions.pack(fill="x", pady=(0, 8))
        ttk.Button(
            diag_actions, text="Forge接続確認",
            command=self.ping
        ).pack(side="left")
        ttk.Button(
            diag_actions, text="再診断",
            command=self.run_diagnostics
        ).pack(side="left", padx=(6, 0))

        self.diag = tk.Text(
            diag_box, height=12, wrap="word", state="disabled"
        )
        self.diag.pack(fill="both", expand=True)

        bottom = ttk.Frame(self.settings_tab)
        bottom.pack(fill="x")
        ttk.Label(
            bottom,
            text="設定変更は「保存して再診断」で確定します。"
        ).pack(side="left")
        ttk.Button(
            bottom, text="保存して再診断",
            command=self.save_config
        ).pack(side="right")

    def backup_shared_data(self):
        data_dir = Path(self.shared_root) / "Data"
        if not data_dir.exists():
            messagebox.showwarning(
                "バックアップ",
                f"共通Dataフォルダが見つかりません。\n\n{data_dir}"
            )
            return

        target = filedialog.asksaveasfilename(
            title="Selfie AI Studio バックアップ保存先",
            defaultextension=".zip",
            filetypes=[("ZIPファイル", "*.zip")],
            initialfile=f"Selfie_AI_Studio_Data_Backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
        )
        if not target:
            return

        target_path = Path(target)

        try:
            self._create_data_backup_zip(target_path)

            size_mb = target_path.stat().st_size / (1024 * 1024)
            if hasattr(self, "backup_status_var"):
                self.backup_status_var.set(
                    f"完了: {target_path.name} ({size_mb:.1f} MB)"
                )
            self.status.set(f"バックアップを作成しました: {target_path}")
            messagebox.showinfo(
                "バックアップ",
                f"バックアップを作成しました。\n\n{target_path}"
            )
        except Exception as e:
            if hasattr(self, "backup_status_var"):
                self.backup_status_var.set("失敗")
            messagebox.showerror(
                "バックアップ",
                f"バックアップ作成に失敗しました。\n{e}"
            )

    def _create_data_backup_zip(self, target_path):
        data_dir = Path(self.shared_root) / "Data"
        if not data_dir.exists():
            raise FileNotFoundError(f"共通Dataフォルダが見つかりません: {data_dir}")

        target_path = Path(target_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with ZipFile(target_path, "w", ZIP_DEFLATED) as zf:
            for path in data_dir.rglob("*"):
                if not path.is_file():
                    continue
                arcname = Path("Data") / path.relative_to(data_dir)
                zf.write(path, arcname)

        return target_path

    def _validate_restore_zip(self, zip_path):
        zip_path = Path(zip_path)
        try:
            with ZipFile(zip_path, "r") as zf:
                bad = zf.testzip()
                if bad:
                    raise ValueError(f"ZIP内のファイルが破損しています: {bad}")

                names = [n.replace("\\", "/") for n in zf.namelist()]
                data_files = [
                    n for n in names
                    if n.startswith("Data/") and not n.endswith("/")
                ]
                if not data_files:
                    raise ValueError(
                        "Selfie AI Studioのバックアップとして認識できません。"
                        "\\nZIP内に Data/ フォルダがありません。"
                    )

                # Path traversal defense.
                for name in names:
                    parts = Path(name).parts
                    if name.startswith("/") or ".." in parts:
                        raise ValueError(
                            f"安全でないパスを含むため復元できません: {name}"
                        )

                return data_files
        except BadZipFile:
            raise ValueError("ZIPファイルが壊れているか、ZIP形式ではありません。")

    def restore_shared_data_from_backup(self):
        source = filedialog.askopenfilename(
            title="Selfie AI Studio バックアップを選択",
            filetypes=[("ZIPファイル", "*.zip")],
        )
        if not source:
            return

        source_path = Path(source)

        try:
            data_files = self._validate_restore_zip(source_path)
        except Exception as e:
            messagebox.showerror(
                "バックアップ復元",
                f"バックアップを検証できませんでした。\n\n{e}"
            )
            return

        preview = (
            f"バックアップ: {source_path.name}\n"
            f"Dataファイル数: {len(data_files)}\n\n"
            "復元前に現在のDataを自動バックアップします。\n"
            "その後、現在のDataを選択したバックアップ内容へ置き換えます。\n\n"
            "Forge・Model・LoRA本体には触れません。\n\n"
            "復元を実行しますか？"
        )
        if not messagebox.askyesno("バックアップ復元", preview):
            return

        shared_root = Path(self.shared_root)
        data_dir = shared_root / "Data"
        backups_dir = shared_root / "Backups"
        backups_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_zip = backups_dir / f"AutoBeforeRestore_{stamp}.zip"
        old_dir = shared_root / f"Data_before_restore_{stamp}"

        temp_root = Path(tempfile.mkdtemp(prefix="selfie_restore_", dir=str(shared_root)))
        extracted_data = temp_root / "Data"

        try:
            # 1. Safety backup first.
            self._create_data_backup_zip(safety_zip)

            # 2. Extract into a temporary directory only.
            with ZipFile(source_path, "r") as zf:
                zf.extractall(temp_root)

            if not extracted_data.exists() or not extracted_data.is_dir():
                raise RuntimeError("展開後にDataフォルダを確認できません。")

            # 3. Transactional swap. Keep old Data until new Data is in place.
            if old_dir.exists():
                shutil.rmtree(old_dir, ignore_errors=True)

            data_dir.rename(old_dir)

            try:
                shutil.move(str(extracted_data), str(data_dir))
            except Exception:
                # Immediate rollback if new Data cannot be placed.
                if data_dir.exists():
                    shutil.rmtree(data_dir, ignore_errors=True)
                old_dir.rename(data_dir)
                raise

            # New Data is now active. Remove the temporary old directory because
            # the safety ZIP already preserves it.
            shutil.rmtree(old_dir, ignore_errors=True)

            if hasattr(self, "backup_status_var"):
                self.backup_status_var.set(
                    f"復元完了 / 復元前Backup: {safety_zip.name}"
                )

            # Reload visible data from the restored Data directory.
            try:
                ws = self.repo.workspace()
                self.active_project_id = (
                    ws.get("current_project_id")
                    or ws.get("active_project_id")
                    or ""
                )
                self.active_character_id = (
                    ws.get("current_character_id")
                    or ws.get("active_character_id")
                    or ""
                )
            except Exception:
                self.active_project_id = ""
                self.active_character_id = ""

            refreshers = [
                "refresh_prompt_library",
                "refresh_character_list",
                "refresh_project_list",
                "refresh_studio_history",
                "refresh_adopted",
                "refresh_core_status",
                "refresh_generate_quick_setup",
                "refresh_home_dashboard",
            ]
            for name in refreshers:
                fn = getattr(self, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass

            self.status.set(
                f"バックアップからDataを復元しました: {source_path.name}"
            )
            messagebox.showinfo(
                "バックアップ復元",
                "復元が完了しました。\n\n"
                f"復元前の自動バックアップ:\n{safety_zip}\n\n"
                "画面データを再読込しました。"
            )

        except Exception as e:
            # Best-effort rollback for failures after the old Data was renamed.
            try:
                if old_dir.exists():
                    if data_dir.exists():
                        shutil.rmtree(data_dir, ignore_errors=True)
                    old_dir.rename(data_dir)
            except Exception:
                pass

            if hasattr(self, "backup_status_var"):
                self.backup_status_var.set("復元失敗")

            messagebox.showerror(
                "バックアップ復元",
                "復元に失敗しました。\n\n"
                f"{e}\n\n"
                f"復元前バックアップが作成済みの場合:\n{safety_zip}"
            )
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def _load_settings(self):
        self.setting_vars["forge_root"].set(self.settings.forge_root)
        self.setting_vars["forge_url"].set(self.settings.forge_url)
        self.setting_vars["nas_models_dir"].set(self.settings.nas_models_dir)

    def choose_dir(self, var):
        d = filedialog.askdirectory()
        if d:
            var.set(d)

    def save_config(self):
        self.settings = Settings(
            forge_root=self.setting_vars["forge_root"].get().strip(),
            forge_url=self.setting_vars["forge_url"].get().strip(),
            nas_models_dir=self.setting_vars["nas_models_dir"].get().strip(),
            ui_theme=self.ui_theme_name.get(),
            ui_font=self.ui_font_name.get(),
        )
        save_settings(self.settings)
        self.status.set("設定を保存しました")
        self.run_diagnostics()

    def api(self):
        return ForgeApi(self.setting_vars["forge_url"].get().strip())

    def _bg(self, fn, success="完了"):
        def runner():
            try:
                fn()
                self.after(0, lambda: self.status.set(success))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("エラー", str(e)))
                self.after(0, lambda: self.status.set("エラー"))
        self.status.set("処理中…")
        threading.Thread(target=runner, daemon=True).start()

    def _diag_set(self, text):
        self.diag.configure(state="normal")
        self.diag.delete("1.0","end")
        self.diag.insert("1.0", text)
        self.diag.configure(state="disabled")

    def run_diagnostics(self):
        def work():
            root = Path(self.setting_vars["forge_root"].get().strip())
            s = Settings(
                forge_root=str(root),
                forge_url=self.setting_vars["forge_url"].get().strip(),
                nas_models_dir=self.setting_vars["nas_models_dir"].get().strip(),
            )
            lines = []
            lines.append(("OK" if root.exists() else "NG") + f"  Forge app: {root}")
            for name, path in [
                ("Stable-diffusion", s.checkpoints_dir),
                ("Lora", s.lora_dir),
                ("VAE", s.vae_dir),
                ("outputs", s.outputs_dir),
                ("txt2img-images", s.txt2img_dir),
            ]:
                lines.append(("OK" if path.exists() else "NG") + f"  {name}: {path}")

            if s.checkpoints_dir.exists():
                lines.append(f"\nローカルモデル: {count_files(s.checkpoints_dir, MODEL_EXTS)}")
            if s.lora_dir.exists():
                lines.append(f"LoRA: {count_files(s.lora_dir, {'.safetensors','.pt'})}")
            if s.vae_dir.exists():
                lines.append(f"VAE: {count_files(s.vae_dir, {'.safetensors','.pt','.ckpt'})}")

            nas = Path(s.nas_models_dir) if s.nas_models_dir else None
            if nas:
                lines.append(f"NASモデル保管: {'OK' if nas.exists() else 'NG'}  {nas}")
            else:
                lines.append("NASモデル保管: 未設定")

            try:
                opts = ForgeApi(s.forge_url, timeout=4).ping()
                model = opts.get("sd_model_checkpoint", "(取得できず)")
                lines.append(f"\nForge API: OK  {s.forge_url}")
                lines.append(f"現在モデル: {model}")
                self.after(0, lambda: self.connection_var.set("Forge: 接続中"))
            except Exception:
                lines.append(f"\nForge API: 未接続  {s.forge_url}")
                lines.append("Forgeが停止中、またはAPIが有効でない可能性があります。")
                self.after(0, lambda: self.connection_var.set("Forge: 未接続"))

            self.after(0, lambda: self._diag_set("\n".join(lines)))
            self.after(0, self.scan_local_models)
            self.after(0, self.scan_loras)
            self.after(0, self.refresh_history)
            self.after(0, self.refresh_studio_history)
            self.after(0, self.refresh_core_status)
        self._bg(work, "環境診断が完了しました")

    def ping(self):
        def work():
            self.api().ping()
            self.after(0, lambda: self.connection_var.set("Forge: 接続中"))
        self._bg(work, "Forgeへ接続できました")

    def _model_meta_path(self):
        return Path(self.shared_root) / "Data" / "model_meta.json"

    def _load_model_meta(self):
        path = self._model_meta_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _save_model_meta(self):
        path = self._model_meta_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(
                json.dumps(self._model_meta, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            os.replace(temp, path)
        except Exception:
            pass

    def _normalize_model_key(self, name):
        return normalize_model_key(name)


    def _current_character_model_key(self):
        character_id = getattr(self, "active_character_id", "") or ""
        if not character_id:
            try:
                ws = self.repo.workspace()
                character_id = (
                    ws.get("current_character_id")
                    or ws.get("active_character_id")
                    or ""
                )
            except Exception:
                character_id = ""
        if not character_id:
            return "", "未選択"

        try:
            character = self.repo.get_item("characters", character_id)
        except Exception:
            character = None

        if not character:
            return "", "未選択"

        model_name = character.get("model") or ""
        return (
            self._normalize_model_key(model_name),
            character.get("name") or "名称なし"
        )

    def _record_model_usage(self, model_name):
        key = self._normalize_model_key(model_name)
        if not key:
            return

        current = self._model_meta.get(key)
        if not isinstance(current, dict):
            current = {}
        current["name"] = model_name
        current["usage_count"] = int(current.get("usage_count") or 0) + 1
        current["last_used_at"] = datetime.now().isoformat(timespec="seconds")
        self._model_meta[key] = current
        self._save_model_meta()
        self.after(0, self._refresh_model_tree)

    def _selected_model_asset(self):
        sel = self.model_tree.selection() if hasattr(self, "model_tree") else ()
        if not sel:
            return None

        values = self.model_tree.item(sel[0], "values")
        if not values or len(values) < 8:
            return None

        path = str(values[9] or "")
        for a in list(self.local_models) + list(self.nas_models):
            if str(a.path) == path:
                return a
        return None

    def _show_selected_model_meta(self, _event=None):
        asset = self._selected_model_asset()
        if not asset:
            return

        key = self._normalize_model_key(asset.path.stem)
        meta = self._model_meta.get(key, {})
        if not isinstance(meta, dict):
            meta = {}

        tags = meta.get("tags") or []
        if isinstance(tags, list):
            tags = ", ".join(str(x) for x in tags)

        if hasattr(self, "model_meta_tags"):
            self.model_meta_tags.set(str(tags or ""))
        if hasattr(self, "model_meta_usage"):
            self.model_meta_usage.set(str(meta.get("usage") or ""))
        if hasattr(self, "model_meta_cfg"):
            self.model_meta_cfg.set(str(meta.get("recommended_cfg") or ""))
        if hasattr(self, "model_meta_sampler"):
            self.model_meta_sampler.set(str(meta.get("recommended_sampler") or ""))
        if hasattr(self, "model_meta_notes"):
            self.model_meta_notes.set(str(meta.get("notes") or ""))

    def save_selected_model_meta(self):
        asset = self._selected_model_asset()
        if not asset:
            messagebox.showinfo(
                "モデル",
                "管理情報を保存するモデルを選択してください。"
            )
            return

        key = self._normalize_model_key(asset.path.stem)
        current = self._model_meta.get(key)
        if not isinstance(current, dict):
            current = {}

        tags = [
            x.strip()
            for x in self.model_meta_tags.get().split(",")
            if x.strip()
        ]

        current.update({
            "name": asset.name,
            "tags": tags,
            "usage": self.model_meta_usage.get().strip(),
            "recommended_cfg": self.model_meta_cfg.get().strip(),
            "recommended_sampler": self.model_meta_sampler.get().strip(),
            "notes": self.model_meta_notes.get().strip(),
        })
        self._model_meta[key] = current
        self._save_model_meta()
        self._refresh_model_tree()
        self.status.set(
            f"モデル管理情報を保存しました: {asset.name}"
        )

    def toggle_selected_model_favorite(self):
        asset = self._selected_model_asset()
        if not asset:
            messagebox.showinfo(
                "モデル",
                "お気に入りを切り替えるモデルを選択してください。"
            )
            return

        key = self._normalize_model_key(asset.path.stem)
        current = self._model_meta.get(key)
        if not isinstance(current, dict):
            current = {}
        current["name"] = asset.name
        current["favorite"] = not bool(current.get("favorite", False))
        self._model_meta[key] = current
        self._save_model_meta()
        self._refresh_model_tree()
        self.status.set("モデルのお気に入りを更新しました。")

    def scan_local_models(self):
        root = Path(self.setting_vars["forge_root"].get().strip()) / "models" / "Stable-diffusion"
        self.local_models = scan_assets(root, MODEL_EXTS)
        self._refresh_model_tree()

    def scan_nas_models(self):
        value = self.setting_vars["nas_models_dir"].get().strip()
        if not value:
            messagebox.showinfo("NAS", "設定タブでNASモデル保管フォルダを指定してください。")
            return
        self.nas_models = scan_assets(Path(value), MODEL_EXTS)
        self._refresh_model_tree()

    def _refresh_model_tree(self):
        if not hasattr(self, "model_tree"):
            return

        q = self.model_search.get().lower().strip()
        fav_only = (
            self.model_fav_only.get()
            if hasattr(self, "model_fav_only")
            else False
        )
        recommended_only = (
            self.model_recommended_only.get()
            if hasattr(self, "model_recommended_only")
            else False
        )

        recommended_key, character_name = self._current_character_model_key()
        rows = []

        for kind, items in [("Forge", self.local_models), ("NAS", self.nas_models)]:
            for a in items:
                key = self._normalize_model_key(a.path.stem)
                meta = self._model_meta.get(key, {})
                if not isinstance(meta, dict):
                    meta = {}

                if q:
                    meta_tags = meta.get("tags") or []
                    if isinstance(meta_tags, list):
                        meta_tags = " ".join(str(x) for x in meta_tags)
                    search_text = " ".join([
                        a.name,
                        str(a.path),
                        str(meta_tags or ""),
                        str(meta.get("usage") or ""),
                        str(meta.get("recommended_sampler") or ""),
                        str(meta.get("notes") or ""),
                    ]).lower()
                    terms = [x for x in re.split(r"\s+", q) if x]
                    if not all(term in search_text for term in terms):
                        continue

                meta = self._model_meta.get(key, {})
                if not isinstance(meta, dict):
                    meta = {}

                favorite = bool(meta.get("favorite", False))
                recommended = bool(recommended_key and key == recommended_key)

                if fav_only and not favorite:
                    continue
                if recommended_only and not recommended:
                    continue

                rows.append({
                    "kind": kind,
                    "asset": a,
                    "favorite": favorite,
                    "recommended": recommended,
                    "usage_count": int(meta.get("usage_count") or 0),
                    "last_used": str(meta.get("last_used_at") or ""),
                })

        sort_mode = (
            self.model_sort_mode.get()
            if hasattr(self, "model_sort_mode")
            else "名前順"
        )

        if sort_mode == "使用回数順":
            rows.sort(
                key=lambda r: (
                    r["usage_count"],
                    r["last_used"],
                    r["asset"].name.lower(),
                ),
                reverse=True
            )
        elif sort_mode == "最終使用順":
            rows.sort(
                key=lambda r: (
                    r["last_used"],
                    r["usage_count"],
                    r["asset"].name.lower(),
                ),
                reverse=True
            )
        elif sort_mode == "お気に入り順":
            rows.sort(
                key=lambda r: (
                    r["favorite"],
                    r["recommended"],
                    r["usage_count"],
                    r["asset"].name.lower(),
                ),
                reverse=True
            )
        else:
            rows.sort(key=lambda r: r["asset"].name.lower())

        self.model_tree.delete(*self.model_tree.get_children())
        for idx, row in enumerate(rows):
            a = row["asset"]
            self.model_tree.insert(
                "", "end", iid=f"model_{idx}",
                values=(
                    "★" if row["favorite"] else "",
                    "●" if row["recommended"] else "",
                    row["kind"],
                    a.name,
                    ", ".join(
                        self._model_meta.get(
                            self._normalize_model_key(a.path.stem), {}
                        ).get("tags") or []
                    ),
                    str(
                        self._model_meta.get(
                            self._normalize_model_key(a.path.stem), {}
                        ).get("usage") or ""
                    ),
                    row["usage_count"],
                    row["last_used"].replace("T", " "),
                    f"{a.size_gb:.2f} GB",
                    str(a.path),
                )
            )

        if not rows:
            if hasattr(self, "model_meta_tags"):
                self.model_meta_tags.set("")
                self.model_meta_usage.set("")
                self.model_meta_cfg.set("")
                self.model_meta_sampler.set("")
                self.model_meta_notes.set("")

        if hasattr(self, "model_info"):
            self.model_info.set(
                f"表示 {len(rows)} / 全 {len(self.local_models)+len(self.nas_models)} 件"
            )
        if hasattr(self, "model_character_info"):
            self.model_character_info.set(
                f"Character推奨: {character_name}"
                + (
                    f" / {recommended_key}"
                    if recommended_key else ""
                )
            )

    def load_api_models(self):
        def work():
            api = self.api()
            models = api.list_models()
            titles = [x.get("title","") for x in models if x.get("title")]
            opts = api.ping()
            current = opts.get("sd_model_checkpoint", "")
            self.after(0, lambda: self.model_combo.configure(values=titles))
            if current:
                def select_current():
                    self.current_model_var.set(current)
                    for i, title in enumerate(titles):
                        if title == current or title.startswith(current) or current.startswith(title.split(" [")[0]):
                            self.model_combo.current(i)
                            break
                self.after(0, select_current)
            elif titles:
                self.after(0, lambda: self.model_combo.current(0))
        self._bg(work, "Forgeのモデル一覧を取得しました")

    def refresh_current_model(self):
        def work():
            opts = self.api().ping()
            current = opts.get("sd_model_checkpoint", "(取得できず)")
            self.after(0, lambda: self.current_model_var.set(current))
        self._bg(work, "現在のForgeモデルを確認しました")

    def apply_selected_model(self):
        selected = self.model_combo.get().strip()
        if not selected:
            messagebox.showinfo("確認", "適用するモデルを選択してください。")
            return
        if not messagebox.askyesno(
            "モデル切替確認",
            f"Forgeのモデルを次へ切り替えます。\n\n{selected}\n\n生成は開始しません。よろしいですか？"
        ):
            return

        def work():
            api = self.api()
            api.set_model(selected)
            opts = api.ping()
            current = opts.get("sd_model_checkpoint", selected)
            self.after(0, lambda: self.current_model_var.set(current))
            self.after(0, self._refresh_generate_workflow_state)
        self._bg(work, "Forgeのモデル切替が完了しました")

    def load_samplers(self):
        def work():
            names = self.api().list_samplers()
            self.after(0, lambda: self.sampler_combo.configure(values=names))
        self._bg(work, "Sampler一覧を取得しました")

    def _generate_precheck(self):
        issues = []

        # Project
        project_id = getattr(self, "active_project_id", "") or ""
        if not project_id:
            try:
                ws = self.repo.workspace()
                project_id = ws.get("current_project_id") or ws.get("active_project_id") or ""
            except Exception:
                project_id = ""
        if not project_id:
            issues.append("Project未選択")

        # Character
        character_id = getattr(self, "active_character_id", "") or ""
        if not character_id:
            try:
                ws = self.repo.workspace()
                character_id = ws.get("current_character_id") or ws.get("active_character_id") or ""
            except Exception:
                character_id = ""
        if not character_id:
            issues.append("Character未選択")

        # Prompt / LoRA
        prompt_text = self.prompt.get("1.0", "end").strip()
        has_lora = bool(getattr(self, "active_loras", {}) or {})
        if not prompt_text and not has_lora:
            issues.append("Prompt未設定")

        # Model
        selected_model = ""
        try:
            selected_model = self.model_combo.get().strip()
        except Exception:
            pass
        if not selected_model:
            try:
                current_model = self.current_model_var.get().strip()
                if current_model not in {"", "未取得", "(取得できず)"}:
                    selected_model = current_model
            except Exception:
                pass
        if not selected_model:
            issues.append("Model未選択")

        try:
            seed = int(self.seed.get().strip())
            if seed < -1:
                raise ValueError
        except (TypeError, ValueError):
            issues.append("Seedは-1または0以上の整数で指定")

        # Output destination derived from Forge root.
        forge_root = ""
        try:
            forge_root = self.setting_vars["forge_root"].get().strip()
        except Exception:
            pass
        if not forge_root:
            issues.append("Forge保存先未設定")

        # Forge connection: local Forge API only, no external AI API.
        forge_ok = False
        try:
            api = self.api()
            api.ping()
            forge_ok = True
        except Exception:
            forge_ok = False
        if not forge_ok:
            issues.append("Forge未接続")

        if issues:
            hints = []
            for issue in issues:
                if issue == "Project未選択":
                    hints.append("Projectを制作準備から選択してください。")
                elif issue == "Character未選択":
                    hints.append("Characterを制作準備から選択してください。")
                elif issue == "Prompt未設定":
                    hints.append("Prompt Libraryから読み込むか、Promptを入力してください。")
                elif issue == "Model未選択":
                    hints.append("モデル一覧を更新してModelを選択してください。")
                elif issue == "Forge保存先未設定":
                    hints.append("設定タブでForgeルートを確認してください。")
                elif issue == "Forge未接続":
                    hints.append("Forgeを起動し、接続状態を確認してください。")

            messagebox.showwarning(
                "生成前チェック",
                "生成を開始できません。\n\n" +
                "\n".join(f"・{x}" for x in issues) +
                "\n\n対応:\n" +
                "\n".join(f"・{x}" for x in hints)
            )
            return False

        return True

    def _reset_session_generation_gallery(self):
        self._session_generation_records = []
        self._session_gallery_refs = []
        self._session_selected_path = ""
        if hasattr(self, "session_gallery"):
            for child in self.session_gallery.winfo_children():
                child.destroy()
        if hasattr(self, "session_gallery_status"):
            self.session_gallery_status.set("このセッションの生成: 0枚")

    def _session_record_is_adopted(self, record):
        path = str((record or {}).get("image_path") or "")
        if not path:
            return False
        try:
            for item in self.repo.list_items("adopted"):
                if str(item.get("image_path") or "") == path:
                    return True
        except Exception:
            pass
        return False

    def _session_record_compare_mark(self, record):
        path = str((record or {}).get("image_path") or "")
        marks = []
        a = getattr(self, "_adopted_compare_a", None)
        b = getattr(self, "_adopted_compare_b", None)
        if a and str(a.get("image_path") or "") == path:
            marks.append("A")
        if b and str(b.get("image_path") or "") == path:
            marks.append("B")
        return "".join(marks)

    def _append_session_generation_record(self, record):
        if not record:
            return

        rid = str(record.get("id") or "")
        path = str(record.get("image_path") or "")
        for existing in self._session_generation_records:
            if rid and str(existing.get("id") or "") == rid:
                return
            if path and str(existing.get("image_path") or "") == path:
                return

        self._session_generation_records.append(dict(record))
        self._session_selected_path = path
        self._render_session_generation_gallery()

    def _render_session_generation_gallery(self):
        if not hasattr(self, "session_gallery"):
            return

        for child in self.session_gallery.winfo_children():
            child.destroy()
        self._session_gallery_refs = []

        records = list(self._session_generation_records)
        max_items = 12
        shown = records[-max_items:]

        for index, record in enumerate(shown):
            path = Path(record.get("image_path") or "")
            cell = ttk.Frame(self.session_gallery)
            cell.grid(
                row=index // 4,
                column=index % 4,
                padx=4,
                pady=4,
                sticky="nsew"
            )

            image_ref = None
            if path.exists():
                try:
                    img = tk.PhotoImage(file=str(path))
                    w, h = img.width(), img.height()
                    max_w, max_h = 110, 110
                    factor = preview_subsample_factor(
                        w, h, max_w, max_h
                    )
                    if factor > 1:
                        img = img.subsample(factor, factor)
                    image_ref = img
                except Exception:
                    image_ref = None

            absolute_index = len(records) - len(shown) + index + 1
            marks = []
            if str(path) == self._session_selected_path:
                marks.append("選択中")
            compare_mark = self._session_record_compare_mark(record)
            if compare_mark:
                marks.append(compare_mark)
            if self._session_record_is_adopted(record):
                marks.append("採用")

            label = str(absolute_index)
            if marks:
                label += "\n[" + " / ".join(marks) + "]"

            btn = ttk.Button(
                cell,
                text=label,
                command=lambda r=dict(record):
                    self.select_session_generation_record(r)
            )
            if image_ref is not None:
                btn.configure(image=image_ref, compound="top")
                self._session_gallery_refs.append(image_ref)
            btn.pack()

        for col in range(4):
            self.session_gallery.columnconfigure(col, weight=1)

        if hasattr(self, "session_gallery_status"):
            selected_name = ""
            if self._session_selected_path:
                selected_name = Path(self._session_selected_path).name
            text = f"このセッションの生成: {len(records)}枚"
            if selected_name:
                text += f" / 選択: {selected_name}"
            self.session_gallery_status.set(text)

    def select_session_generation_record(self, record):
        if not record:
            return
        self._session_selected_path = str(record.get("image_path") or "")
        self._update_latest_generated_panel(record)
        path = Path(record.get("image_path") or "")
        if path.exists():
            self._show_preview(path)
        self._render_session_generation_gallery()

    def remove_selected_session_generation(self):
        selected_path = str(getattr(self, "_session_selected_path", "") or "")
        if not selected_path:
            messagebox.showinfo(
                "セッション生成",
                "一覧から外す画像を選択してください。"
            )
            return

        before = len(self._session_generation_records)
        self._session_generation_records = [
            x for x in self._session_generation_records
            if str(x.get("image_path") or "") != selected_path
        ]
        if len(self._session_generation_records) == before:
            return

        # History / PNG / JSON are intentionally untouched.
        if self._session_generation_records:
            next_record = self._session_generation_records[-1]
            self._session_selected_path = str(
                next_record.get("image_path") or ""
            )
            self._update_latest_generated_panel(next_record)
            next_path = Path(next_record.get("image_path") or "")
            if next_path.exists():
                self._show_preview(next_path)
        else:
            self._session_selected_path = ""
            self._latest_generated_record = None
            self.preview_label.configure(
                image="",
                text="生成画像プレビュー"
            )
            self.latest_generated_file.set("ファイル: 未選択")
            self.latest_generated_seed.set("Seed: -")
            self.latest_generated_model.set("Model: -")
            self.latest_generated_lora.set("LoRA: -")
            self.latest_generated_prompt.set("Prompt: -")
            self.latest_generated_time.set("生成時刻: -")

        self._render_session_generation_gallery()
        self.status.set(
            "セッション一覧から外しました。Historyと画像ファイルは削除していません。"
        )

    def adopt_selected_session_generation(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("セッション生成", "選択画像がありません。")
            return
        self.adopt_latest_generated_image()

    def _extract_seed_from_forge_info(self, info):
        return extract_seed_from_info(info)


    def _update_latest_generated_panel(self, record):
        self._latest_generated_record = dict(record or {})
        if not record:
            return

        path = Path(record.get("image_path") or "")
        self.latest_generated_file.set(
            f"ファイル: {path.name or '-'}"
        )
        self.latest_generated_seed.set(
            f"Seed: {record.get('seed') if record.get('seed') not in ('', None) else '-'}"
        )
        self.latest_generated_model.set(
            f"Model: {record.get('model') or '-'}"
        )

        loras = []
        for entry in record.get("loras") or []:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            try:
                loras.append(
                    f"{entry['name']}:{float(entry.get('weight', 1.0)):g}"
                )
            except Exception:
                loras.append(str(entry.get("name")))
        self.latest_generated_lora.set(
            "LoRA: " + (", ".join(loras) if loras else "なし")
        )

        prompt_name = getattr(
            self, "_quick_active_prompt_name", ""
        ) or "未選択"
        self.latest_generated_prompt.set(f"Prompt: {prompt_name}")

        created = record.get("created_at")
        if isinstance(created, (int, float)):
            try:
                created_text = datetime.fromtimestamp(created).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                created_text = str(created)
        else:
            created_text = str(created or "").replace("T", " ")
        self.latest_generated_time.set(
            f"生成時刻: {created_text or '-'}"
        )

    def _show_record_in_generate_detail(self, record, refresh_gallery=True):
        if not record:
            return
        self._session_selected_path = str(record.get("image_path") or "")
        self._update_latest_generated_panel(record)
        path = Path(record.get("image_path") or "")
        if path.exists():
            self._show_preview(path)
        if refresh_gallery:
            self._render_session_generation_gallery()
    def save_latest_generated_status(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("状態保存", "保存する最新生成画像がありません。")
            return

        selected_status = self.latest_generated_status_choice.get().strip() or "未評価"
        if selected_status not in {"未評価", "採用", "仮採用", "保留", "不採用"}:
            selected_status = "未評価"

        updated = dict(record)
        updated["status"] = selected_status
        try:
            updated = self.repo.upsert_item("history", updated)
        except Exception as e:
            messagebox.showerror(
                "状態保存",
                f"最新生成画像の状態保存に失敗しました。\n{e}"
            )
            return

        self._latest_generated_record = updated
        self.latest_generated_status.set(f"生成状態: {selected_status}")
        self.status.set(f"最新生成画像の状態を保存しました: {selected_status}")
        self._refresh_generate_workflow_state()
        self.refresh_studio_history()
    def _ask_adopted_theme_and_version(self, default_name=""):
        dialog = tk.Toplevel(self)
        dialog.title("採用画像情報")
        dialog.transient(self)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="日本語テーマ名").pack(anchor="w")
        theme_entry = ttk.Entry(frame, width=40)
        theme_entry.pack(fill="x", pady=(2, 8))
        theme_entry.insert(0, default_name)

        ttk.Label(frame, text="バージョン").pack(anchor="w")
        version_entry = ttk.Entry(frame, width=40)
        version_entry.pack(fill="x", pady=(2, 8))

        result = {"theme": None, "version": None}

        def on_ok():
            result["theme"] = theme_entry.get().strip()
            result["version"] = version_entry.get().strip()
            dialog.destroy()

        def on_cancel():
            result["theme"] = None
            dialog.destroy()

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(12, 0))
        ttk.Button(button_frame, text="レビュー開始", command=on_ok).pack(side="right")
        ttk.Button(button_frame, text="キャンセル", command=on_cancel).pack(side="right", padx=(6, 0))

        self.wait_window(dialog)
        return result

    def save_current_prompt_from_generate(self):
        prompt_text = self.prompt.get("1.0", "end").strip()
        negative_text = self.negative.get("1.0", "end").strip()

        if not prompt_text:
            messagebox.showinfo(
                "Prompt保存",
                "現在のGenerate Promptが空です。"
            )
            return

        name = simpledialog.askstring(
            "Prompt保存",
            "Prompt Libraryへ保存する名前を入力してください。",
            parent=self
        )
        if not name:
            return
        name = name.strip()
        if not name:
            return

        try:
            existing_items = self.repo.list_items("prompt_library")
        except Exception:
            existing_items = []

        existing = next(
            (
                x for x in existing_items
                if (x.get("name") or "").strip() == name
            ),
            None
        )

        if existing:
            if not messagebox.askyesno(
                "Prompt保存",
                f"「{name}」はすでにあります。\n上書きしますか？"
            ):
                return
            item = dict(existing)
        else:
            item = {
                "name": name,
                "category": "generate",
                "tags": [],
                "favorite": False,
                "notes": "",
                "usage_count": 0,
                "last_used_at": "",
            }

        model = ""
        try:
            model = self.model_combo.get().strip()
        except Exception:
            pass

        recommended_loras = []
        try:
            for lora_name, weight in self.active_loras.items():
                recommended_loras.append({
                    "name": lora_name,
                    "weight": float(weight),
                })
        except Exception:
            pass

        item.update({
            "name": name,
            "prompt": prompt_text,
            "negative_prompt": negative_text,
            "recommended_model": model,
            "recommended_loras": recommended_loras,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

        try:
            saved = self.repo.upsert_item("prompt_library", item)
        except Exception as e:
            messagebox.showerror(
                "Prompt保存",
                f"保存に失敗しました。\n{e}"
            )
            return

        try:
            self.refresh_prompt_library()
            self.refresh_generate_quick_setup()
        except Exception:
            pass

        self._quick_active_prompt_name = saved.get("name") or name
        try:
            self.quick_prompt.set(self._quick_active_prompt_name)
        except Exception:
            pass
        self._refresh_generate_workflow_state()
        self.status.set(
            f"現在のPromptをPrompt Libraryへ保存しました: {name}"
        )

    def open_prompt_builder_from_generate(self):
        try:
            self.prompt_builder_refresh_preview()
            self.tabs.select(self.prompt_builder)
        except Exception:
            return
        self.prompt_builder_status.set(
            "Generateの現在Promptをベースにしています。"
            "必要な部品を選んでください。"
        )

    def open_ai_assistant_from_generate(self):
        try:
            self.ai_assistant_refresh_current()
            self.tabs.select(self.ai_assistant)
        except Exception:
            return
        self.ai_assistant_status.set(
            "Generateの現在Promptを取得しました。指示を入力してください。"
        )

    def open_image_review_for_latest(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo(
                "画像解析",
                "まだ選択中の生成画像がありません。"
            )
            return

        target_path = str(record.get("image_path") or "")
        if not target_path:
            return

        try:
            self.refresh_image_review_sources()
            self.tabs.select(self.image_review)
        except Exception:
            return

        matched_iid = None
        for iid, item in self._image_review_records.items():
            if str(item.get("image_path") or "") == target_path:
                matched_iid = iid
                break

        if matched_iid:
            self.image_review_tree.selection_set(matched_iid)
            self.image_review_tree.focus(matched_iid)
            self.image_review_tree.see(matched_iid)
            self._show_image_review_selected()
            self.image_review_ai_status.set(
                "最新生成を画像解析へ読み込みました。"
            )
        else:
            self.image_review_ai_status.set(
                "最新生成が画像一覧に見つかりませんでした。History更新を試してください。"
            )

    def open_latest_generated_image(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("最新生成", "まだ生成画像がありません。")
            return
        path = Path(record.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "最新生成", f"画像ファイルが見つかりません。\\n\\n{path}"
            )
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("最新生成", f"画像を開けませんでした。\\n{e}")

    def open_latest_generated_image_folder(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("Open Latest Image", "まだ最新生成画像がありません。")
            return

        image_path = str(record.get("image_path") or "")
        if not image_path:
            messagebox.showwarning("Open Latest Image", "最新生成画像のパスが見つかりません。")
            return

        folder = Path(image_path).parent
        if not folder.exists():
            messagebox.showwarning(
                "Open Latest Image",
                f"画像フォルダーが見つかりません。\n\n{folder}"
            )
            return

        try:
            os.startfile(str(folder))
        except Exception as e:
            messagebox.showerror("Open Latest Image", f"フォルダーを開けませんでした。\n{e}")
    def _ask_review_goal(self):
        presets = [
            "全体レビュー",
            "顔",
            "目",
            "髪",
            "背景",
            "光・ライティング",
            "プロンプト改善",
        ]

        dialog = tk.Toplevel(self)
        dialog.title("レビュー対象を選択")
        dialog.transient(self)
        dialog.grab_set()
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="改善したい点を選択してください",
            font=(None, 11, "bold")
        ).pack(anchor="w")

        goal_var = tk.StringVar(value=presets[0])
        for preset in presets:
            ttk.Radiobutton(
                frame,
                text=preset,
                variable=goal_var,
                value=preset
            ).pack(anchor="w", pady=1)

        ttk.Label(frame, text="自由入力:").pack(anchor="w", pady=(8, 0))
        custom_entry = ttk.Entry(frame, width=40)
        custom_entry.pack(fill="x")
        custom_entry.focus_set()

        result = {"value": None}

        def on_ok():
            custom = custom_entry.get().strip()
            result["value"] = custom or goal_var.get()
            dialog.destroy()

        def on_cancel():
            result["value"] = None
            dialog.destroy()

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(12, 0))
        ttk.Button(button_frame, text="レビュー開始", command=on_ok).pack(side="right")
        ttk.Button(button_frame, text="キャンセル", command=on_cancel).pack(side="right", padx=(0, 6))

        self.wait_window(dialog)
        return result["value"]
    def send_latest_generated_to_selfie(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("Send to Selfie", "まだ送信対象の生成画像がありません。")
            return

        goal_text = self._ask_review_goal()
        if goal_text is None:
            return

        image_path = str(record.get("image_path") or "")
        absolute_image_path = ""
        if image_path:
            try:
                absolute_image_path = str(Path(image_path).resolve())
            except Exception:
                absolute_image_path = image_path

        prompt_text = self.prompt.get("1.0", "end").strip()
        negative_text = self.negative.get("1.0", "end").strip()
        model_text = self.model_combo.get().strip() if hasattr(self, "model_combo") else ""
        sampler_text = self.sampler.get() if hasattr(self, "sampler") else ""
        steps_value = self.steps.get() if hasattr(self, "steps") else ""
        cfg_value = self.cfg.get() if hasattr(self, "cfg") else ""
        width_value = self.width.get() if hasattr(self, "width") else ""
        height_value = self.height.get() if hasattr(self, "height") else ""
        size_text = f"{width_value}x{height_value}" if width_value and height_value else ""
        seed_value = record.get("seed") if record.get("seed") not in ("", None) else "-"

        master = self._get_active_character_master()
        master_text = "-"
        master_type_text = "-"
        if master:
            master_text = str(Path(master.get("image_path") or "").resolve())
            master_type_text = master.get("master_type") or "-"

        lora_entries = []
        for lora_name, weight in (self.active_loras or {}).items():
            try:
                lora_entries.append(f"{lora_name}:{float(weight):g}")
            except Exception:
                lora_entries.append(str(lora_name))
        lora_text = ", ".join(lora_entries) if lora_entries else "なし"

        markdown_lines = [
            "# Image Review",
            "",
            "## Goal",
            goal_text or "-",
            "",
            "## Image",
            absolute_image_path or "-",
            "",
            "## Prompt",
            prompt_text or "-",
            "",
            "## Negative Prompt",
            negative_text or "-",
            "",
            "## Model",
            model_text or "-",
            "",
            "## LoRA",
            lora_text,
            "",
            "## Sampler",
            sampler_text or "-",
            "",
            "## Steps",
            str(steps_value) if steps_value != "" else "-",
            "",
            "## CFG",
            str(cfg_value) if cfg_value != "" else "-",
            "",
            "## Seed",
            str(seed_value),
            "",
            "## Size",
            size_text or "-",
            "",
            "## Master",
            master_text,
            "",
            "## Master Type",
            master_type_text,
            "",
            "## Improvement Notes",
            "(write here)",
        ]
        markdown = "\n".join(markdown_lines)

        try:
            self.clipboard_clear()
            self.clipboard_append(markdown)
            self.update()
            messagebox.showinfo(
                "Send to Selfie",
                "✅ レビュー内容をクリップボードにコピーしました。\n生成画像を添付して貼り付けてください。"
            )
        except Exception as e:
            messagebox.showerror(
                "Send to Selfie",
                f"クリップボードへのコピーに失敗しました。\n{e}"
            )

    def adopt_latest_generated_image(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("最新生成", "採用する生成画像がありません。")
            return

        history_id = record.get("id") or ""
        if not history_id:
            messagebox.showwarning(
                "最新生成", "History登録IDを確認できません。"
            )
            return

        theme_version = self._ask_adopted_theme_and_version(
            default_name=Path(record.get("image_path") or "").stem
        )
        if theme_version is None or theme_version.get("theme") is None:
            return

        updated = dict(record)
        updated["status"] = "採用"
        if theme_version.get("theme"):
            updated["library_theme"] = theme_version["theme"]
        if theme_version.get("version"):
            updated["library_version"] = theme_version["version"]

        try:
            self.repo.upsert_item("history", updated)
            adopted, created = self.repo.adopt_history_item(
                history_id,
                adopted_at=datetime.now().isoformat(timespec="seconds"),
                extra={
                    "library_theme": theme_version.get("theme") or "",
                    "library_version": theme_version.get("version") or "",
                },
            )
        except Exception as e:
            messagebox.showerror(
                "採用DB", f"採用DBへの登録に失敗しました。\\n{e}"
            )
            return

        self._latest_generated_record = updated
        self.refresh_studio_history()
        self.refresh_adopted()
        self.refresh_home_dashboard()
        self._render_session_generation_gallery()

        name = Path(record.get("image_path") or "").name
        if created:
            self.status.set(f"最新生成を採用DBへ登録しました: {name}")
        else:
            self.status.set(f"最新生成は採用DBへ登録済みです: {name}")

    def _compare_label(self, item):
        return image_name(item)

    def _refresh_generate_compare_status(self):
        a = getattr(self, "_adopted_compare_a", None)
        b = getattr(self, "_adopted_compare_b", None)

        if hasattr(self, "generate_compare_a_status"):
            self.generate_compare_a_status.set(
                f"A: {self._compare_label(a)}"
            )
        if hasattr(self, "generate_compare_b_status"):
            self.generate_compare_b_status.set(
                f"B: {self._compare_label(b)}"
            )

        if hasattr(self, "adopted_compare_a_status"):
            self.adopted_compare_a_status.set(
                f"A: {self._compare_label(a)}"
            )
        if hasattr(self, "adopted_compare_b_status"):
            self.adopted_compare_b_status.set(
                f"B: {self._compare_label(b)}"
            )

    def _get_active_character_item(self):
        character_id = getattr(self, "active_character_id", "") or ""
        if not character_id:
            return None
        return self.repo.get_item("characters", character_id)

    def _get_active_character_master(self):
        character = self._get_active_character_item()
        if not character:
            return None

        refs = character.get("master_refs") or []
        if not isinstance(refs, list):
            return None

        preferred = None
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            if ref.get("master_type") == "正面":
                return ref
            if preferred is None:
                preferred = ref
        return preferred

    def _attach_master_reference_to_payload(self, payload: dict, api: ForgeApi) -> dict | None:
        """If Master Reference is enabled, augment `payload` with the
        appropriate `alwayson_scripts` entry for the IP-Adapter script.

        Returns the modified payload, or None if generation should be aborted
        (e.g. master missing or script not available).
        This uses live script metadata from Forge via `api.script_info()` to
        build a matching `args` array without hard-coding argument positions.
        """
        if not getattr(self, 'master_reference_var', None) or not self.master_reference_var.get():
            return payload

        master = self._get_active_character_master()
        if not master or not master.get("image_path"):
            messagebox.showerror("Master Reference", "Master画像が設定されていません。生成を中止します。")
            return None

        master_path = Path(str(master.get("image_path") or ""))
        if not master_path.exists():
            messagebox.showerror("Master Reference", f"Master画像が見つかりません。\n{master_path}")
            return None

        # Read image and prepare data URI
        try:
            img_bytes = master_path.read_bytes()
            b64 = base64.b64encode(img_bytes).decode('ascii')
            data_uri = f"data:image/png;base64,{b64}"
        except Exception as e:
            messagebox.showerror("Master Reference", f"Master画像の読み込みに失敗しました。\n{e}")
            return None

        # Retrieve script-info from Forge and locate the Integrated ControlNet script.
        try:
            scripts = api.script_info() or []
        except Exception:
            messagebox.showerror("Master Reference", "Forgeからスクリプト情報を取得できませんでした。")
            return None

        ip_script = None
        for s in scripts:
            name = (s.get('name') or '')
            if not name:
                continue
            low = name.lower()
            if (
                low == 'controlnet'
                and s.get('is_alwayson')
                and not s.get('is_img2img')
            ):
                ip_script = s
                break

        if not ip_script:
            messagebox.showerror(
                "Master Reference",
                "Integrated ControlNetスクリプトが見つかりません。Forgeの/script-infoでスクリプト名を確認してください。"
            )
            return None

        args_template = ip_script.get('args') or []
        if not isinstance(args_template, list) or len(args_template) == 0:
            messagebox.showerror(
                "Master Reference",
                "Integrated ControlNetスクリプトの引数情報が不十分です。生成を中止します。"
            )
            return None

        # Current Forge exposes ControlNet units as unlabeled dictionaries.
        first_arg = args_template[0]
        first_value = first_arg.get('value') if isinstance(first_arg, dict) else None
        master_reference_mode = 'reference_adain+attn'
        required_unit_fields = {
            'enabled', 'module', 'model', 'weight', 'image', 'resize_mode',
            'threshold_a', 'guidance_start', 'guidance_end',
        }
        missing_unit_fields = sorted(
            required_unit_fields - set(first_value)
        ) if isinstance(first_value, dict) else sorted(required_unit_fields)
        if missing_unit_fields:
            messagebox.showerror(
                "Master Reference",
                "ControlNet Unitの必須項目を取得できませんでした: "
                + ", ".join(missing_unit_fields)
            )
            return None

        if isinstance(first_value, dict):
            unit = dict(first_value)
            if master_reference_mode == 'reference_adain+attn':
                unit.update({
                    'enabled': True,
                    'module': 'reference_adain+attn',
                    'model': 'None',
                    'weight': float(self.master_reference_strength.get()),
                    'image': data_uri,
                    'resize_mode': 'Crop and Resize',
                    'processor_res': 0.5,
                    'threshold_a': 0.8,
                    'threshold_b': 0.5,
                    'guidance_start': 0.0,
                    'guidance_end': 1.0,
                })
            else:
                # Retained for a future UI/config switch back to IP-Adapter.
                unit.update({
                    'enabled': True,
                    'module': 'InsightFace+CLIP-H (IPAdapter)',
                    'model': 'ip-adapter-plus-face_sdxl_vit-h [368cf551]',
                    'weight': float(self.master_reference_strength.get()),
                    'image': data_uri,
                    'resize_mode': 'Crop and Resize',
                    'guidance_start': 0.0,
                    'guidance_end': 1.0,
                })
            args = [unit] + [arg.get('value') for arg in args_template[1:]]
            alwayson = dict(payload.get('alwayson_scripts') or {})
            alwayson[ip_script.get('name')] = {'args': args}
            payload['alwayson_scripts'] = alwayson
            return payload

        # Required logical fields we must be able to map from script args.
        required_keys = {
            'image': ['image', 'img', 'input', 'reference'],
            'model': ['model'],
            'preprocessor': ['preprocessor', 'processor'],
            'weight': ['weight', 'strength'],
            'resize': ['resize'],
            'start': ['start'],
            'end': ['end'],
        }

        # Build a mapping from role -> index in args_template.
        label_map = {}
        for idx, arg in enumerate(args_template):
            lbl = (arg.get('label') or '')
            low_lbl = lbl.lower()
            for role, keywords in required_keys.items():
                if role in label_map:
                    continue
                for kw in keywords:
                    if kw in low_lbl:
                        label_map[role] = idx
                        break

        missing = [k for k in required_keys.keys() if k not in label_map]
        if missing:
            messagebox.showerror(
                "Master Reference",
                f"Integrated ControlNetスクリプトの引数から必須フィールドを特定できませんでした: {', '.join(missing)}。生成を中止します。"
            )
            return None

        # Now construct args array preserving original length/order.
        args = []
        for idx, arg in enumerate(args_template):
            # default fallback to existing default value
            default = arg.get('value')
            if idx == label_map['image']:
                args.append(data_uri)
            elif idx == label_map['model']:
                args.append('ip-adapter-plus-face_sdxl_vit-h [368cf551]')
            elif idx == label_map['preprocessor']:
                args.append('InsightFace+CLIP-H (IPAdapter)')
            elif idx == label_map['weight']:
                try:
                    args.append(float(self.master_reference_strength.get()))
                except Exception:
                    args.append(0.8)
            elif idx == label_map['resize']:
                args.append('Crop and Resize')
            elif idx == label_map['start']:
                args.append(0.0)
            elif idx == label_map['end']:
                args.append(1.0)
            else:
                args.append(default)

        alwayson = dict(payload.get('alwayson_scripts') or {})
        alwayson[ip_script.get('name')] = {'args': args}
        payload['alwayson_scripts'] = alwayson
        return payload

    def _refresh_latest_master_status(self):
        master = self._get_active_character_master()
        if not hasattr(self, "latest_master_status"):
            return

        if master and master.get("image_path"):
            image_path = Path(str(master.get("image_path") or ""))
            name = image_path.name or ""
            master_type = master.get("master_type") or "Master"
            if name:
                self.latest_master_status.set(
                    f"Master: {master_type} / {name}"
                )
            else:
                self.latest_master_status.set(f"Master: {master_type}")
        else:
            self.latest_master_status.set("Master: 未設定")

    def _ask_master_type(self):
        dialog = tk.Toplevel(self)
        dialog.title("Master種別選択")
        dialog.transient(self)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="この生成画像をどのMaster種別として保存しますか？").pack(anchor="w")
        master_type_var = tk.StringVar(value="正面")
        ttk.Combobox(
            frame,
            textvariable=master_type_var,
            state="readonly",
            values=("正面", "横顔", "全身", "その他"),
            width=14,
        ).pack(fill="x", pady=(8, 0))

        result = {"value": None}

        def on_ok():
            result["value"] = master_type_var.get().strip()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        button_frame = ttk.Frame(frame)
        button_frame.pack(fill="x", pady=(12, 0))
        ttk.Button(button_frame, text="OK", command=on_ok).pack(side="right")
        ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side="right", padx=(6, 0))

        dialog.protocol("WM_DELETE_WINDOW", on_cancel)
        dialog.wait_visibility()
        dialog.lift()
        dialog.focus_force()
        dialog.grab_set()
        self.wait_window(dialog)
        return result["value"]

    def set_latest_generated_as_master(self):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("Master", "保存する生成画像がありません。")
            return

        character = self._get_active_character_item()
        if not character:
            messagebox.showinfo(
                "Master",
                "現在アクティブなCharacterがありません。Characterを選択してから再試行してください。"
            )
            return

        master_type = self._ask_master_type()
        if not master_type:
            return

        image_path = Path(record.get("image_path") or "")
        if not image_path.exists():
            messagebox.showwarning(
                "Master",
                f"マスターに設定する画像ファイルが見つかりません。\n\n{image_path}"
            )
            return

        master_path = str(image_path.resolve())
        refs = [x for x in (character.get("master_refs") or []) if x.get("master_type") != master_type]
        refs.append({
            "image_path": master_path,
            "master_type": master_type,
            "name": image_path.name,
            "source_history_id": record.get("id") or "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

        updated = dict(character)
        updated["master_refs"] = refs
        self.repo.upsert_item("characters", updated)
        self._refresh_latest_master_status()
        self.status.set(f"{master_type}マスターを保存しました: {image_path.name}")

    def set_external_image_as_master(self):
        character = self._get_active_character_item()
        if not character:
            messagebox.showinfo(
                "Master",
                "現在アクティブなCharacterがありません。Characterを選択してから再試行してください。"
            )
            return

        selected = filedialog.askopenfilename(
            parent=self,
            title="Master画像を選択",
            filetypes=(
                ("画像ファイル", "*.png *.jpg *.jpeg"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
            ),
        )
        if not selected:
            return

        image_path = Path(selected)
        if not image_path.is_file() or image_path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            messagebox.showwarning("Master", "PNG / JPG / JPEG画像を選択してください。")
            return

        master_type = self._ask_master_type()
        if not master_type:
            return

        refs = list(character.get("master_refs") or [])
        existing = next(
            (x for x in refs if x.get("master_type") == master_type),
            None,
        )
        if existing and not messagebox.askyesno(
            "Master上書き確認",
            f"{master_type}Masterは既に設定されています。\n\n"
            f"{existing.get('image_path') or ''}\n\n上書きしますか？",
        ):
            return

        refs = [x for x in refs if x.get("master_type") != master_type]
        refs.append({
            "image_path": str(image_path.resolve()),
            "master_type": master_type,
            "name": image_path.name,
            "source_history_id": "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })

        updated = dict(character)
        updated["master_refs"] = refs
        self.repo.upsert_item("characters", updated)
        self._refresh_latest_master_status()
        self.status.set(f"外部画像を{master_type}Masterに設定しました: {image_path.name}")

    def open_current_character_master_image(self):
        master = self._get_active_character_master()
        if not master or not master.get("image_path"):
            messagebox.showinfo("Master", "現在のCharacterに設定されたMaster画像がありません。")
            return

        master_path = Path(master.get("image_path") or "")
        if not master_path.exists():
            messagebox.showwarning(
                "Master",
                f"Master画像が見つかりません。\n\n{master_path}"
            )
            return

        try:
            os.startfile(str(master_path))
        except Exception as e:
            messagebox.showerror(
                "Master",
                f"Master画像を開けませんでした。\n{e}"
            )

    def compare_master_to_latest_generated(self):
        master = self._get_active_character_master()
        if not master or not master.get("image_path"):
            messagebox.showinfo("Master", "現在のCharacterに設定されたMaster画像がありません。")
            return

        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo("Master", "比較する最新生成画像がありません。")
            return

        current_path = Path(record.get("image_path") or "")
        master_path = Path(master.get("image_path") or "")
        if not master_path.exists():
            messagebox.showwarning(
                "Master",
                f"Master画像が見つかりません。\n\n{master_path}"
            )
            return
        if not current_path.exists():
            messagebox.showwarning(
                "Master",
                f"最新生成画像が見つかりません。\n\n{current_path}"
            )
            return

        self._open_image_compare_viewer(
            master_path,
            current_path,
            f"Master: {master.get('master_type') or 'Master'}",
            "Latest Generated"
        )

    def _open_image_compare_viewer(self, path_a, path_b, title_a, title_b):
        try:
            orig_a = tk.PhotoImage(file=str(path_a))
            orig_b = tk.PhotoImage(file=str(path_b))
        except Exception as e:
            messagebox.showerror(
                "比較ビューア",
                f"画像を読み込めませんでした。\n{e}"
            )
            return

        win = tk.Toplevel(self)
        win.title("Master / Current 比較")
        win.geometry("1450x820")
        win.transient(self)

        toolbar = ttk.Frame(win, padding=(8, 8, 8, 4))
        toolbar.pack(fill="x")

        zoom_var = tk.StringVar(value="Fit")
        sync_var = tk.BooleanVar(value=True)

        ttk.Label(toolbar, textvariable=zoom_var, width=12).pack(side="left")
        ttk.Button(toolbar, text="Fit", command=lambda: _draw_all(fit=True)).pack(side="left", padx=(0, 4))
        for p in (25, 50, 100, 200, 400):
            ttk.Button(
                toolbar,
                text=f"{p}%",
                command=lambda value=p: _draw_all(percent=value)
            ).pack(side="left", padx=(0, 4))

        ttk.Checkbutton(
            toolbar,
            text="同期ズーム",
            variable=sync_var
        ).pack(side="left", padx=(12, 0))

        ttk.Label(
            toolbar,
            text="ホイール: ズーム / 左ドラッグ: 移動"
        ).pack(side="left", padx=(12, 0))

        ttk.Button(toolbar, text="閉じる", command=win.destroy).pack(side="right")

        pane = ttk.Panedwindow(win, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        left_box = ttk.LabelFrame(
            pane, text=title_a, padding=4
        )
        right_box = ttk.LabelFrame(
            pane, text=title_b, padding=4
        )
        pane.add(left_box, weight=1)
        pane.add(right_box, weight=1)

        canvas_a = tk.Canvas(left_box, bg="black")
        canvas_b = tk.Canvas(right_box, bg="black")
        canvas_a.pack(fill="both", expand=True)
        canvas_b.pack(fill="both", expand=True)

        display = {"a": None, "b": None}
        state = {
            "percent": 100,
            "fit_mode": True,
        }
        zoom_steps = [25, 50, 100, 200, 400]

        def draw_one(canvas, image, key, percent):
            if image is None:
                return
            if percent != 100:
                display[key] = image.subsample(max(1, int(100 / percent)), max(1, int(100 / percent)))
            else:
                display[key] = image
            canvas.delete("all")
            canvas.create_image(
                0,
                0,
                image=display[key],
                anchor="nw"
            )
            canvas.config(scrollregion=canvas.bbox("all"))

        def fit_percent_for(src_img, canvas):
            cw = max(1, canvas.winfo_width() - 30)
            ch = max(1, canvas.winfo_height() - 30)
            ow = max(1, src_img.width())
            oh = max(1, src_img.height())
            candidates = [
                p for p in zoom_steps
                if ow * p / 100 <= cw and oh * p / 100 <= ch
            ]
            return max(candidates) if candidates else 25

        def _draw_all(percent=None, fit=False):
            win.update_idletasks()
            if fit:
                pa = fit_percent_for(orig_a, canvas_a)
                pb = fit_percent_for(orig_b, canvas_b)
                percent = min(pa, pb) if sync_var.get() else None
                state["fit_mode"] = True
            else:
                state["fit_mode"] = False

            if sync_var.get():
                p = percent if percent is not None else state["percent"]
                state["percent"] = p
                draw_one(canvas_a, orig_a, "a", p)
                draw_one(canvas_b, orig_b, "b", p)
                zoom_var.set(f"Fit ({p}%)" if fit else f"{p}%")
            else:
                if fit:
                    pa = fit_percent_for(orig_a, canvas_a)
                    pb = fit_percent_for(orig_b, canvas_b)
                    draw_one(canvas_a, orig_a, "a", pa)
                    draw_one(canvas_b, orig_b, "b", pb)
                    zoom_var.set(f"Fit (A:{pa}% / B:{pb}%)")
                else:
                    p = percent if percent is not None else state["percent"]
                    state["percent"] = p
                    draw_one(canvas_a, orig_a, "a", p)
                    draw_one(canvas_b, orig_b, "b", p)
                    zoom_var.set(f"{p}%")

        def next_zoom(current, direction):
            if direction > 0:
                larger = [p for p in zoom_steps if p > current]
                return min(larger) if larger else zoom_steps[-1]
            smaller = [p for p in zoom_steps if p < current]
            return max(smaller) if smaller else zoom_steps[0]

        def wheel(event):
            target = next_zoom(state["percent"], 1 if event.delta > 0 else -1)
            _draw_all(percent=target)

        def pan_start(event):
            event.widget.scan_mark(event.x, event.y)

        def pan_move(event):
            event.widget.scan_dragto(event.x, event.y, gain=1)

        for canvas in (canvas_a, canvas_b):
            canvas.bind("<MouseWheel>", wheel)
            canvas.bind("<ButtonPress-1>", pan_start)
            canvas.bind("<B1-Motion>", pan_move)

        win.after(100, lambda: _draw_all(fit=True))

    def clear_generate_compare_slots(self):
        self._adopted_compare_a = None
        self._adopted_compare_b = None
        self._refresh_generate_compare_status()
        self._render_session_generation_gallery()
        self.status.set("A/B比較をクリアしました。")

    def adopt_compare_slot_from_generate(self, slot):
        item = (
            getattr(self, "_adopted_compare_a", None)
            if slot == "A"
            else getattr(self, "_adopted_compare_b", None)
        )
        if not item:
            messagebox.showinfo(
                "A/B比較",
                f"比較{slot}に画像が設定されていません。"
            )
            return

        path = Path(item.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "A/B比較",
                f"画像ファイルが見つかりません。\\n\\n{path}"
            )
            return

        # If already an adopted record, just surface it.
        if item.get("adopted_at") and item.get("id"):
            self.status.set(
                f"比較{slot}は採用DB登録済みです: {self._compare_label(item)}"
            )
            try:
                self.tabs.select(self.adopted_tab)
            except Exception:
                pass
            return

        history_id = item.get("id") or ""
        history_record = None
        if history_id:
            try:
                history_record = self.repo.get_item("history", history_id)
            except Exception:
                history_record = None

        # Fallback by path for records placed into A/B from latest generation.
        if not history_record:
            try:
                for candidate in self.repo.list_items("history"):
                    if str(candidate.get("image_path") or "") == str(path):
                        history_record = candidate
                        break
            except Exception:
                pass

        if not history_record:
            messagebox.showwarning(
                "A/B比較",
                "この画像に対応するHistory記録が見つかりません。"
            )
            return

        updated = dict(history_record)
        updated["status"] = "採用"

        try:
            updated = self.repo.upsert_item("history", updated)
            adopted, created = self.repo.adopt_history_item(
                updated.get("id") or "",
                adopted_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as e:
            messagebox.showerror(
                "採用DB",
                f"採用DBへの登録に失敗しました。\\n{e}"
            )
            return

        # Update compare slot with the adopted record so later clicks know it is adopted.
        if adopted:
            if slot == "A":
                self._adopted_compare_a = dict(adopted)
            else:
                self._adopted_compare_b = dict(adopted)

        self._refresh_generate_compare_status()
        self.refresh_studio_history()
        self.refresh_adopted()
        self._render_session_generation_gallery()
        try:
            self.refresh_home_dashboard()
        except Exception:
            pass

        label = self._compare_label(adopted or updated)
        if created:
            self.status.set(f"比較{slot}を採用DBへ登録しました: {label}")
        else:
            self.status.set(f"比較{slot}は採用DB登録済みです: {label}")

    def set_latest_generated_compare_slot(self, slot):
        record = getattr(self, "_latest_generated_record", None)
        if not record:
            messagebox.showinfo(
                "A/B比較", f"比較{slot}に設定する生成画像がありません。"
            )
            return

        path = Path(record.get("image_path") or "")
        if not path.exists():
            messagebox.showwarning(
                "A/B比較", f"画像ファイルが見つかりません。\\n\\n{path}"
            )
            return

        compare_item = dict(record)
        compare_item["name"] = (
            compare_item.get("name") or path.name
        )

        if slot == "A":
            self._adopted_compare_a = compare_item
        else:
            self._adopted_compare_b = compare_item

        self._refresh_generate_compare_status()
        self._render_session_generation_gallery()
        self.status.set(f"最新生成を比較{slot}に設定しました: {path.name}")

    def stop_generation_queue(self):
        if not getattr(self, "_queue_running", False):
            self.queue_status.set("キュー: 待機")
            return
        self._queue_stop_requested = True
        self.queue_status.set("キュー: 停止予約（現在の1枚完了後）")
        self.status.set(
            "連続生成の停止を予約しました。現在生成中の1枚が完了した後に停止します。"
        )

    def start_generation_queue(self):
        if getattr(self, "_queue_running", False):
            messagebox.showinfo("連続生成", "すでに連続生成中です。")
            return

        if not self._generate_precheck():
            return

        count = clamp_queue_count(self.queue_count.get())
        self.queue_count.set(count)

        base_prompt = self.prompt.get("1.0", "end").strip()
        self._import_loras_from_prompt(base_prompt)
        cleaned_base_prompt = self._clean_prompt_lora_tags(base_prompt)
        if cleaned_base_prompt != base_prompt:
            self.prompt.delete("1.0", "end")
            self.prompt.insert("1.0", cleaned_base_prompt)
            base_prompt = cleaned_base_prompt

        prompt = self._compose_prompt_with_loras(base_prompt)
        if not prompt:
            messagebox.showinfo(
                "確認",
                "プロンプトまたは有効なLoRAを指定してください。"
            )
            return

        if not messagebox.askyesno(
            "連続生成確認",
            f"Forgeへ {count} 枚の連続生成要求を送ります。\\n"
            "現在生成中の1枚は途中停止できません。\\n\\n"
            "開始しますか？"
        ):
            return

        payload = build_generation_payload(
            prompt=prompt,
            negative_prompt=self.negative.get("1.0", "end").strip(),
            steps=self.steps.get(),
            cfg_scale=self.cfg.get(),
            width=self.width.get(),
            height=self.height.get(),
            sampler_name=self.sampler.get().strip(),
            seed=int(self.seed.get().strip()),
            scheduler=self.scheduler.get(),
        )

        selected_model = self.model_combo.get().strip()
        generation_loras = dict(self.active_loras)

        generation_project_id = getattr(
            self, "active_project_id", ""
        ) or ""
        generation_character_id = getattr(
            self, "active_character_id", ""
        ) or ""

        if not generation_project_id or not generation_character_id:
            try:
                ws = self.repo.workspace()
                if not generation_project_id:
                    generation_project_id = (
                        ws.get("current_project_id")
                        or ws.get("active_project_id")
                        or ""
                    )
                if not generation_character_id:
                    generation_character_id = (
                        ws.get("current_character_id")
                        or ws.get("active_character_id")
                        or ""
                    )
            except Exception:
                pass

        current_model_hint = ""
        try:
            current_model_hint = self.current_model_var.get().strip()
            if current_model_hint in {"未取得", "(取得できず)"}:
                current_model_hint = ""
        except Exception:
            pass

        self._queue_running = True
        self._queue_stop_requested = False
        self._reset_session_generation_gallery()
        self.queue_status.set(f"キュー: 0 / {count}")
        self.status.set(f"連続生成を開始しました: {count}枚")

        def worker():
            completed = 0
            try:
                api = self.api()
                if selected_model:
                    api.set_model(selected_model)

                for index in range(1, count + 1):
                    if self._queue_stop_requested:
                        break

                    self.after(
                        0,
                        lambda i=index, n=count:
                            self.queue_status.set(f"キュー: {i} / {n} 生成中")
                    )

                    # Optionally attach Master Reference for each queued item.
                    augmented = self._attach_master_reference_to_payload(dict(payload), api)
                    if augmented is None:
                        return
                    images, raw = api.txt2img(augmented)
                    if not images:
                        raise ForgeApiError("画像が返りませんでした。")

                    self._record_active_lora_usage(generation_loras)
                    self._record_model_usage(
                        selected_model or current_model_hint
                    )

                    out_root = (
                        Path(self.setting_vars["forge_root"].get().strip())
                        / "outputs"
                        / "selfie-ai-studio"
                    )
                    out_root.mkdir(parents=True, exist_ok=True)

                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    image_path = out_root / f"selfie_{stamp}.png"
                    json_path = out_root / f"selfie_{stamp}.json"
                    image_path.write_bytes(images[0])

                    effective_model = selected_model or current_model_hint
                    if not effective_model:
                        try:
                            opts = api.ping()
                            effective_model = (
                                opts.get("sd_model_checkpoint", "") or ""
                            )
                        except Exception:
                            effective_model = ""

                    json_path.write_text(
                        json.dumps(
                            {
                                "request": payload,
                                "model": effective_model,
                                "info": raw.get("info"),
                                "project_id": generation_project_id,
                                "character_id": generation_character_id,
                                "queue_index": index,
                                "queue_total": count,
                            },
                            ensure_ascii=False,
                            indent=2
                        ),
                        encoding="utf-8"
                    )

                    saved_history, _created = self._history_register_record(
                        image_path=image_path,
                        json_path=json_path,
                        request=payload,
                        model=effective_model,
                        info=raw.get("info"),
                        project_id=generation_project_id,
                        character_id=generation_character_id,
                    )

                    if saved_history:
                        seed_value = self._extract_seed_from_forge_info(
                            raw.get("info")
                        )
                        if seed_value not in ("", None):
                            saved_history = dict(saved_history)
                            saved_history["seed"] = seed_value
                            saved_history = self.repo.upsert_item(
                                "history", saved_history
                            )

                        self.after(
                            0,
                            lambda r=dict(saved_history):
                                self._update_latest_generated_panel(r)
                        )
                        self.after(
                            0,
                            lambda r=dict(saved_history):
                                self._append_session_generation_record(r)
                        )

                    completed += 1
                    self.after(
                        0,
                        lambda p=image_path: self._show_preview(p)
                    )
                    self.after(0, self.refresh_history)
                    self.after(0, self.refresh_studio_history)
                    self.after(0, self.refresh_core_status)

                    self.after(
                        0,
                        lambda c=completed, n=count:
                            self.queue_status.set(f"キュー: {c} / {n} 完了")
                    )

                    if self._queue_stop_requested:
                        break

            except Exception as e:
                self.after(
                    0,
                    lambda err=str(e):
                        messagebox.showerror("連続生成", err)
                )
                self.after(
                    0,
                    lambda: self.status.set("連続生成でエラーが発生しました。")
                )
            finally:
                stopped = self._queue_stop_requested and completed < count

                def finish():
                    self._queue_running = False
                    self._queue_stop_requested = False
                    if stopped:
                        self.queue_status.set(
                            f"キュー: 停止 / {completed}枚完了"
                        )
                        self.status.set(
                            f"連続生成を停止しました。完了: {completed}枚"
                        )
                    else:
                        self.queue_status.set(
                            f"キュー: 完了 / {completed}枚"
                        )
                        self.status.set(
                            f"連続生成が完了しました: {completed}枚"
                        )
                    try:
                        self.refresh_home_dashboard()
                    except Exception:
                        pass

                self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def generate_image(self):
        if not self._generate_precheck():
            return

        base_prompt = self.prompt.get("1.0","end").strip()
        self._import_loras_from_prompt(base_prompt)
        cleaned_base_prompt = self._clean_prompt_lora_tags(base_prompt)
        if cleaned_base_prompt != base_prompt:
            self.prompt.delete("1.0","end")
            self.prompt.insert("1.0", cleaned_base_prompt)
            base_prompt = cleaned_base_prompt

        prompt = self._compose_prompt_with_loras(base_prompt)
        if not prompt:
            messagebox.showinfo("確認","プロンプトまたは有効なLoRAを指定してください。")
            return
        if not messagebox.askyesno("生成確認", "Forgeへ生成要求を送ります。実際にGPUで画像生成を開始します。よろしいですか？"):
            return

        payload = build_generation_payload(
            prompt=prompt,
            negative_prompt=self.negative.get("1.0","end").strip(),
            steps=self.steps.get(),
            cfg_scale=self.cfg.get(),
            width=self.width.get(),
            height=self.height.get(),
            sampler_name=self.sampler.get().strip(),
            seed=int(self.seed.get().strip()),
            scheduler=self.scheduler.get(),
        )
        selected_model = self.model_combo.get().strip()
        generation_loras = dict(self.active_loras)

        # Snapshot active Project / Character at generation start.
        # The worker thread receives these exact IDs instead of resolving mutable UI state later.
        generation_project_id = getattr(self, "active_project_id", "") or ""
        generation_character_id = getattr(self, "active_character_id", "") or ""

        if not generation_project_id or not generation_character_id:
            try:
                _ws = self.repo.workspace()
                if not generation_project_id:
                    generation_project_id = (
                        _ws.get("current_project_id")
                        or _ws.get("active_project_id")
                        or ""
                    )
                if not generation_character_id:
                    generation_character_id = (
                        _ws.get("current_character_id")
                        or _ws.get("active_character_id")
                        or ""
                    )
            except Exception:
                pass

        current_model_hint = ""
        try:
            current_model_hint = self.current_model_var.get().strip()
            if current_model_hint in {"未取得", "(取得できず)"}:
                current_model_hint = ""
        except Exception:
            pass

        def work():
            api = self.api()
            if selected_model:
                api.set_model(selected_model)
            # Optionally attach Master Reference to payload before calling Forge.
            augmented = self._attach_master_reference_to_payload(payload, api)
            if augmented is None:
                return
            images, raw = api.txt2img(augmented)
            if not images:
                raise ForgeApiError("画像が返りませんでした。")

            # Count usage only after Forge returned an image successfully.
            self._record_active_lora_usage(generation_loras)
            self._record_model_usage(
                selected_model or current_model_hint
            )

            out_root = Path(self.setting_vars["forge_root"].get().strip()) / "outputs" / "selfie-ai-studio"
            out_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image_path = out_root / f"selfie_{stamp}.png"
            json_path = out_root / f"selfie_{stamp}.json"
            image_path.write_bytes(images[0])
            effective_model = selected_model or current_model_hint
            if not effective_model:
                try:
                    opts = api.ping()
                    effective_model = opts.get("sd_model_checkpoint", "") or ""
                except Exception:
                    effective_model = ""

            json_path.write_text(
                json.dumps(
                    {
                        "request": payload,
                        "model": effective_model,
                        "info": raw.get("info"),
                        "project_id": generation_project_id,
                        "character_id": generation_character_id,
                    },
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

            # Register with stable Project linkage and effective Forge model.
            saved_history, _created = self._history_register_record(
                image_path=image_path,
                json_path=json_path,
                request=payload,
                model=effective_model,
                info=raw.get("info"),
                project_id=generation_project_id,
                character_id=generation_character_id,
            )

            if saved_history:
                seed_value = self._extract_seed_from_forge_info(raw.get("info"))
                if seed_value not in ("", None):
                    saved_history = dict(saved_history)
                    saved_history["seed"] = seed_value
                    saved_history = self.repo.upsert_item(
                        "history", saved_history
                    )

                self.after(
                    0,
                    lambda r=dict(saved_history): self._update_latest_generated_panel(r)
                )
                self.after(
                    0,
                    lambda r=dict(saved_history): self._append_session_generation_record(r)
                )

            self.after(0, lambda p=image_path: self._show_preview(p))
            self.after(0, self.refresh_history)
            self.after(0, self.refresh_studio_history)
            self.after(0, self.refresh_core_status)

        project_name_for_status = ""
        if generation_project_id:
            try:
                _p = self.repo.get_item("projects", generation_project_id)
                project_name_for_status = (_p or {}).get("name") or ""
            except Exception:
                pass
        if project_name_for_status:
            self._bg(work, f"生成が完了しました / Project: {project_name_for_status}")
        else:
            self._bg(work, "生成が完了しました / Project: 未設定")

    def _show_preview(self, path: Path):
        try:
            img = tk.PhotoImage(file=str(path))
            w, h = img.width(), img.height()

            # Tk PhotoImage supports integer subsampling. Use ceiling division
            # so images slightly larger than the preview area are actually
            # reduced instead of being clipped by the panel.
            max_w, max_h = 480, 520
            factor = preview_subsample_factor(
                w, h, max_w, max_h
            )
            if factor > 1:
                img = img.subsample(factor, factor)

            self.current_preview = img
            self.preview_label.configure(image=img, text="")
        except Exception:
            self.preview_label.configure(
                image="",
                text=f"生成完了\n{path}"
            )

    def refresh_history(self):
        root = Path(self.setting_vars["forge_root"].get().strip()) / "outputs"
        paths = recent_images(root, 100)
        self.history_tree.delete(*self.history_tree.get_children())
        for p in paths:
            self.history_tree.insert("", "end", values=(format_mtime(p), p.name, str(p)))
        self.history_info.set(f"最新 {len(paths)} 件")

    def _open_history_item(self, _event=None):
        sel = self.history_tree.selection()
        if not sel:
            return
        path = self.history_tree.item(sel[0], "values")[2]
        try:
            import os
            os.startfile(path)
        except Exception as e:
            messagebox.showerror("エラー", str(e))

def run():
    App().mainloop()
