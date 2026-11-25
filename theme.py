# -*- coding: utf-8 -*-
"""HuaJian warm-paper theme palettes, WinShun nativeLook mapping, DPI and Windows 11 DWM tile chrome."""
from __future__ import annotations

import ctypes
import os
from typing import Any, Dict, Optional, Tuple

FONT_FAMILY = "Microsoft YaHei UI"

# Warm editorial paper aesthetic inspired by HuaJian desktop tiles
HUAJIAN_LIGHT: Dict[str, str] = {
    "BG": "#F5F4EE",
    "SURFACE": "#EEECE4",
    "SURFACE_ALT": "#E7E5DC",
    "BORDER": "#E2E0D6",
    "HOVER": "#ECEAE0",
    "TITLE_MUTED": "#C5C3BB",
    "HEADING": "#23221F",
    "TEXT": "#3D3C38",
    "TEXT_BOLD": "#23221F",
    "MUTED": "#8E8C84",
    "FAINT": "#B5B3AA",
    "CHECK_BORDER": "#C8C6BE",
    "CHECK_BG": "#F5F4EE",
    "CHECK_FILL": "#6E7F6B",
    "ACCENT": "#6B5CA5",
    "ACCENT_FILL": "#6B5CA5",
    "ACCENT_TEXT": "#FFFFFF",
    "TAG_BG": "#EAE6F3",
    "CODE_BG": "#EAE8DF",
    "DANGER": "#C65349",
    "DANGER_BG": "#F5E4E2",
    "SUCCESS": "#6E7F6B",
    "INPUT_BG": "#FAF9F5",
}

HUAJIAN_DARK: Dict[str, str] = {
    "BG": "#232320",
    "SURFACE": "#2B2B27",
    "SURFACE_ALT": "#33332E",
    "BORDER": "#363631",
    "HOVER": "#2C2C28",
    "TITLE_MUTED": "#63625C",
    "HEADING": "#ECEBE4",
    "TEXT": "#D2D0C8",
    "TEXT_BOLD": "#F0EFE9",
    "MUTED": "#95938B",
    "FAINT": "#676660",
    "CHECK_BORDER": "#5A5953",
    "CHECK_BG": "#232320",
    "CHECK_FILL": "#7B9477",
    "ACCENT": "#A899E0",
    "ACCENT_FILL": "#7D6EC2",
    "ACCENT_TEXT": "#FFFFFF",
    "TAG_BG": "#322E44",
    "CODE_BG": "#2F2F2B",
    "DANGER": "#E07B72",
    "DANGER_BG": "#3A2726",
    "SUCCESS": "#7B9477",
    "INPUT_BG": "#292926",
}


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def blend_argb_hex(value: Any, fallback: str, backdrop: str) -> str:
    """Convert WinShun #AARRGGBB or #RRGGBB into an opaque #RRGGBB color for Tk."""
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text.upper()
        except ValueError:
            return fallback
    if len(text) == 9 and text.startswith("#"):
        try:
            alpha = int(text[1:3], 16) / 255.0
            fg = tuple(int(text[i:i + 2], 16) for i in (3, 5, 7))
            bg = tuple(int(backdrop[i:i + 2], 16) for i in (1, 3, 5))
            mixed = tuple(round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg))
            return "#%02X%02X%02X" % mixed
        except (TypeError, ValueError):
            return fallback
    return fallback


def build_palette(
    appearance: Optional[Dict[str, Any]] = None,
    theme: str = "light",
    *,
    preset: str = "huajian",
) -> Tuple[Dict[str, str], str]:
    """Build Tk palette: 'huajian' warm-paper (light/dark) or 'winshun' nativeLook."""
    app_dict = dict(appearance or {})
    native = app_dict.get("nativeLook")
    if not isinstance(native, dict):
        native = {}

    if isinstance(native.get("dark"), bool):
        resolved_theme = "dark" if native["dark"] else "light"
    else:
        resolved_theme = "dark" if str(theme or app_dict.get("theme") or "light").lower() == "dark" else "light"

    base = dict(HUAJIAN_DARK if resolved_theme == "dark" else HUAJIAN_LIGHT)
    if str(preset or "huajian").lower() != "winshun" or not native:
        return base, resolved_theme

    bg = blend_argb_hex(native.get("background"), base["BG"], base["BG"])
    base["BG"] = bg
    mapping = {
        "SURFACE": "layer",
        "SURFACE_ALT": "control",
        "BORDER": "divider",
        "HOVER": "hover",
        "HEADING": "text",
        "TEXT": "text",
        "TEXT_BOLD": "text",
        "MUTED": "subtext",
        "FAINT": "faint",
        "TITLE_MUTED": "faint",
        "CHECK_BORDER": "faint",
        "ACCENT": "accent",
        "ACCENT_FILL": "accentFill",
        "ACCENT_TEXT": "accentText",
        "CHECK_FILL": "accentFill",
        "TAG_BG": "tagFill",
        "DANGER": "danger",
        "DANGER_BG": "dangerFill",
    }
    for key, native_key in mapping.items():
        base[key] = blend_argb_hex(native.get(native_key), base[key], bg)
    base["CHECK_BG"] = base["BG"]
    base["INPUT_BG"] = blend_argb_hex(native.get("layer"), base["SURFACE"], bg)
    base["CODE_BG"] = base["SURFACE_ALT"]
    return base, resolved_theme


def make_font(size: int, bold: bool = False, overstrike: bool = False) -> Tuple[Any, ...]:
    styles = []
    if bold:
        styles.append("bold")
    if overstrike:
        styles.append("overstrike")
    return (FONT_FAMILY, int(size)) + tuple(styles)


def set_process_dpi_awareness() -> bool:
    """Declare Per-Monitor V2 DPI awareness before Tk initializes."""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        try:
            fn = user32.SetProcessDpiAwarenessContext
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = ctypes.c_bool
            if fn(ctypes.c_void_p(-4)):
                return True
        except Exception:
            pass
        try:
            fn = user32.SetProcessDPIAware
            fn.argtypes = []
            fn.restype = ctypes.c_bool
            return bool(fn())
        except Exception:
            return False
    except Exception:
        return False


def apply_dpi_scaling(root: Any) -> float:
    """Return physical-to-logical DPI multiplier (e.g. 1.0 at 96dpi, 2.25 at 216dpi)."""
    try:
        fpix = float(root.winfo_fpixels("1i"))
        tk_scale = max(1.0, min(4.0, fpix / 72.0))
        root.tk.call("tk", "scaling", tk_scale)
        return max(1.0, min(3.5, fpix / 96.0))
    except Exception:
        return 1.0


def _hex_to_colorref(hex_color: str) -> int:
    text = str(hex_color or "#FFFFFF").strip().lstrip("#")
    if len(text) != 6:
        return 0x00FFFFFF
    r = int(text[0:2], 16)
    g = int(text[2:4], 16)
    b = int(text[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_frameless_tile_chrome(root: Any, palette: Dict[str, str], theme: str) -> bool:
    """Apply Windows 11 native rounded corners, hairline border, and subtle drop shadow."""
    if os.name != "nt":
        return False
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi
        # DWMWA_USE_IMMERSIVE_DARK_MODE (20)
        dark_val = ctypes.c_int(1 if theme == "dark" else 0)
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(20), ctypes.byref(dark_val), ctypes.sizeof(dark_val)
        )
        # DWMWA_WINDOW_CORNER_PREFERENCE (33): DWMWCP_ROUND = 2
        corner = ctypes.c_int(2)
        hr_corner = dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(33), ctypes.byref(corner), ctypes.sizeof(corner)
        )
        # DWMWA_BORDER_COLOR (34): subtle rounded 1px hairline border
        border = ctypes.c_uint(_hex_to_colorref(palette["BORDER"]))
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(34), ctypes.byref(border), ctypes.sizeof(border)
        )
        # Ambient DWM drop shadow
        margins = _MARGINS(1, 1, 1, 1)
        dwm.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
        return hr_corner == 0
    except Exception:
        return False


def clear_frameless_tile_chrome(root: Any) -> bool:
    """Disable DWM square rounding/border/shadow for transparent circular windows."""
    if os.name != "nt":
        return False
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        if not hwnd:
            return False
        dwm = ctypes.windll.dwmapi
        # DWMWCP_DONOTROUND = 1
        corner = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(33), ctypes.byref(corner), ctypes.sizeof(corner)
        )
        # DWMWA_COLOR_NONE = 0xFFFFFFFE
        no_border = ctypes.c_uint(0xFFFFFFFE)
        dwm.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd), ctypes.c_uint(34), ctypes.byref(no_border), ctypes.sizeof(no_border)
        )
        margins = _MARGINS(0, 0, 0, 0)
        dwm.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
        return True
    except Exception:
        return False
