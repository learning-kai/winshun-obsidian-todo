# -*- coding: utf-8 -*-
"""Persistent UI state stored strictly in WS_DATA_DIR/state.json.

Never stores user notes or secrets; only window geometry, filter, opacity,
font size, style preset, pinned/collapsed toggles, and optional fallback vault.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict

STATE_FILENAME = "state.json"

DEFAULT_STATE: Dict[str, Any] = {
    "window_x": None,
    "window_y": None,
    "window_width": None,
    "window_height": None,
    "pinned": True,
    "opacity": 96,
    "filter_mode": "all",
    "hide_completed": False,
    "collapsed": False,
    "font_size": 10,
    "style_preset": "huajian",
    "show_timestamps": False,
    "local_vault": "",
    "countdown_x": None,
    "countdown_y": None,
    "countdown_pinned": True,
    "countdown_shape": "square",
}

VALID_FILTER_MODES = ("active", "all", "done")
VALID_STYLE_PRESETS = ("huajian", "winshun")
VALID_COUNTDOWN_SHAPES = ("square", "circle", "pill")
FONT_MIN = 8
FONT_MAX = 15
OPACITY_MIN = 50
OPACITY_MAX = 100
DEFAULT_WIDTH = 440
DEFAULT_HEIGHT = 420
MIN_WIDTH = 300
MIN_HEIGHT = 240


def clamp_opacity(value: Any, fallback: int = 96) -> int:
    try:
        num = int(round(float(value)))
    except (TypeError, ValueError):
        num = fallback
    return max(OPACITY_MIN, min(OPACITY_MAX, num))


def clamp_font_size(value: Any, fallback: int = 11) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        num = fallback
    return max(FONT_MIN, min(FONT_MAX, num))


def normalize_filter_mode(value: Any, fallback: str = "all") -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in VALID_FILTER_MODES else fallback


def normalize_style_preset(value: Any, fallback: str = "huajian") -> str:
    preset = str(value or "").strip().lower()
    return preset if preset in VALID_STYLE_PRESETS else fallback


def normalize_countdown_shape(value: Any, fallback: str = "square") -> str:
    shape = str(value or "").strip().lower()
    return shape if shape in VALID_COUNTDOWN_SHAPES else fallback


def state_path(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, STATE_FILENAME)


def load_state(data_dir: str) -> Dict[str, Any]:
    state = dict(DEFAULT_STATE)
    if not data_dir:
        return state
    path = os.path.join(data_dir, STATE_FILENAME)
    if not os.path.isfile(path):
        return state
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            return state
    except (OSError, ValueError):
        return state

    for key in ("window_x", "window_y"):
        val = raw.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            state[key] = val
    for key, minimum in (("window_width", MIN_WIDTH), ("window_height", MIN_HEIGHT)):
        val = raw.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and val >= minimum:
            state[key] = val
        else:
            state[key] = None
    if isinstance(raw.get("pinned"), bool):
        state["pinned"] = raw["pinned"]
    if raw.get("opacity") is not None:
        state["opacity"] = clamp_opacity(raw.get("opacity"), state["opacity"])
    state["filter_mode"] = normalize_filter_mode(raw.get("filter_mode"), state["filter_mode"])
    if isinstance(raw.get("hide_completed"), bool):
        state["hide_completed"] = raw["hide_completed"]
    if isinstance(raw.get("collapsed"), bool):
        state["collapsed"] = raw["collapsed"]
    if raw.get("font_size") is not None:
        state["font_size"] = clamp_font_size(raw.get("font_size"), state["font_size"])
    state["style_preset"] = normalize_style_preset(raw.get("style_preset"), state["style_preset"])
    if isinstance(raw.get("show_timestamps"), bool):
        state["show_timestamps"] = raw["show_timestamps"]
    if isinstance(raw.get("local_vault"), str):
        state["local_vault"] = raw["local_vault"].strip()
    for key in ("countdown_x", "countdown_y"):
        val = raw.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            state[key] = val
    if isinstance(raw.get("countdown_pinned"), bool):
        state["countdown_pinned"] = raw["countdown_pinned"]
    state["countdown_shape"] = normalize_countdown_shape(raw.get("countdown_shape"), state["countdown_shape"])
    return state


def save_state(data_dir: str, state: Dict[str, Any]) -> bool:
    """Atomically persist state.json inside WS_DATA_DIR."""
    if not data_dir:
        return False
    os.makedirs(data_dir, exist_ok=True)
    target = os.path.join(data_dir, STATE_FILENAME)
    payload = dict(DEFAULT_STATE)
    payload.update({k: state[k] for k in DEFAULT_STATE if k in state})
    fd, tmp_path = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=data_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, target)
        return True
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False
