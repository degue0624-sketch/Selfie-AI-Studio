from __future__ import annotations

from tkinter import ttk

UI_PAD = 8
UI_GAP = 8
UI_PAGE_GAP = 10


def make_list_detail_pane(
    parent,
    *,
    left_weight=2,
    right_weight=3,
    pady=(10, 6),
):
    """Create the common left-list / right-detail shell used across Studio."""
    pane = ttk.Panedwindow(parent, orient="horizontal")
    pane.pack(fill="both", expand=True, pady=pady)

    left = ttk.Frame(pane)
    right = ttk.Frame(pane)
    pane.add(left, weight=left_weight)
    pane.add(right, weight=right_weight)
    return pane, left, right


def make_detail_box(parent, title, *, padding=UI_PAD):
    """Create a consistent right-side detail container."""
    box = ttk.LabelFrame(parent, text=title, padding=padding)
    box.pack(fill="both", expand=True)
    return box
