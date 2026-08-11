from __future__ import annotations


THEMES = {
    "Shiori": {
        "background": "#0D0E14",
        "panel": "#151621",
        "panel_alt": "#1E1F2C",
        "input": "#20212E",
        "border": "#454359",
        "text": "#F0EDF5",
        "text_muted": "#C3BDCF",
        "accent": "#B3A5CE",
        "accent_hover": "#74658F",
        "primary": "#C76845",
        "primary_hover": "#D17A55",
        "success": "#D6A75D",
        "danger": "#A94C55",
        "selection": "#514861",
    },
}

DEFAULT_THEME = "Shiori"


def theme_names():
    return tuple(THEMES)


def get_theme(name):
    return dict(THEMES.get(name, THEMES[DEFAULT_THEME]))
