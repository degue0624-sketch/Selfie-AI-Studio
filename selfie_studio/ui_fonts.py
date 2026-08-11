from __future__ import annotations

from tkinter import TclError
from tkinter import font as tkfont


DEFAULT_UI_FONT = "Current / Default"
UI_FONT_CHOICES = (DEFAULT_UI_FONT, "Yu Gothic UI", "Meiryo UI")
_DEFAULT_FAMILY = "Segoe UI Semibold"
_BODY_FAMILY = "Segoe UI"

# Stable Tk named-font identifiers. Updating their definitions refreshes every
# ttk/Tk widget that uses the shared typography without rebuilding each tab.
UI_FONT_NORMAL = "SelfieUINormal"
UI_FONT_SMALL = "SelfieUISmall"
UI_FONT_SECTION = "SelfieUISection"
UI_FONT_TAB = "SelfieUITab"
UI_FONT_EMPHASIS = "SelfieUIEmphasis"
UI_FONT_BODY = "SelfieUIBody"


def ui_font_choices():
    return UI_FONT_CHOICES


def resolve_ui_font_family(root, selection):
    """Return (family, available), safely falling back to the current font."""
    if selection == DEFAULT_UI_FONT:
        return _DEFAULT_FAMILY, True
    try:
        families = {name.casefold(): name for name in tkfont.families(root)}
        found = families.get(str(selection).casefold())
        if found:
            return found, True
    except Exception:
        pass
    return _DEFAULT_FAMILY, False


def _configure_font(root, name, family, size, weight="normal"):
    registry = getattr(root, "_selfie_ui_named_fonts", None)
    if registry is None:
        registry = {}
        root._selfie_ui_named_fonts = registry

    try:
        named = tkfont.Font(root=root, name=name, exists=True)
    except TclError:
        named = tkfont.Font(root=root, name=name)
    named.configure(family=family, size=size, weight=weight)
    # A newly created tkinter.font.Font owns its Tcl named font and deletes it
    # when the Python wrapper is collected. Keep the wrappers for the lifetime
    # of the application; existing named fonts are safely reused as well.
    registry[name] = named


def configure_named_fonts(root, selection=DEFAULT_UI_FONT):
    """Apply shared typography to Tk widgets and standard dialogs."""
    family, available = resolve_ui_font_family(root, selection)
    shared = {
        UI_FONT_NORMAL: (family, 11, "normal"),
        UI_FONT_SMALL: (family, 10, "normal"),
        UI_FONT_SECTION: (family, 12, "bold"),
        UI_FONT_TAB: (family, 11, "normal"),
        UI_FONT_EMPHASIS: (family, 11, "bold"),
        # Prompt/Negative Prompt body remains independent from UI selection.
        UI_FONT_BODY: (_BODY_FAMILY, 11, "normal"),
    }
    for name, (font_family, size, weight) in shared.items():
        _configure_font(root, name, font_family, size, weight)

    definitions = {
        "TkDefaultFont": UI_FONT_NORMAL,
        "TkTextFont": UI_FONT_NORMAL,
        "TkMenuFont": UI_FONT_NORMAL,
        "TkHeadingFont": UI_FONT_SECTION,
        "TkCaptionFont": UI_FONT_EMPHASIS,
        "TkSmallCaptionFont": UI_FONT_SMALL,
        "TkIconFont": UI_FONT_SMALL,
        "TkTooltipFont": UI_FONT_SMALL,
    }
    for name, source_name in definitions.items():
        try:
            named = tkfont.nametofont(name, root=root)
            source = tkfont.nametofont(source_name, root=root)
            named.configure(
                family=source.actual("family"),
                size=source.actual("size"),
                weight=source.actual("weight"),
            )
        except TclError:
            pass
    return available
