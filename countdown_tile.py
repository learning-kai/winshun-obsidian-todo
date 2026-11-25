# -*- coding: utf-8 -*-
"""Minimalist HuaJian-style floating countdown tile (Square Watercolor, Circular Dial, Pill Bar).

Powered by Win32 UpdateLayeredWindow + GDI+ PARGB per-pixel alpha rendering for true
anti-aliased silhouettes and real soft ambient drop shadows on any Windows desktop wallpaper.
"""
from __future__ import annotations

import ctypes
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

import layered_countdown
import theme as ui_theme

MAX_COUNTDOWN_SECONDS = 24 * 3600
MIN_COUNTDOWN_SECONDS = 1
SHAPE_ORDER = ("square", "circle", "pill")

_HHMM_RE = re.compile(r"^(?P<m>\d{1,4}):(?P<s>[0-5]?\d)$")
_UNIT_SEQ_RE = re.compile(
    r"^(?:(?P<h>\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours|小时|时))?"
    r"(?:(?P<m>\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes|分钟|分))?"
    r"(?:(?P<s>\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒钟|秒))?$",
    re.IGNORECASE,
)
_BARE_NUM_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class ParsedCountdown:
    seconds: int
    label: str
    display_duration: str


def format_duration_zh(total_seconds: int) -> str:
    sec = max(0, int(total_seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} 小时")
    if m:
        parts.append(f"{m} 分钟")
    if s or not parts:
        parts.append(f"{s} 秒")
    return " ".join(parts)


def format_clock(remaining_seconds: float) -> str:
    """Format remaining time as MM:SS or H:MM:SS (ceiling so 00:00 only shows when done)."""
    sec = max(0, int(math.ceil(max(0.0, float(remaining_seconds)))))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_eta_clock(remaining_seconds: float) -> str:
    if remaining_seconds <= 0.0:
        return ""
    eta = datetime.now().astimezone() + timedelta(seconds=float(remaining_seconds))
    return eta.strftime("%H:%M")


def parse_countdown_input(raw_text: str) -> Tuple[Optional[ParsedCountdown], str]:
    """Parse user input like '25', '40 政治', '1.5h', '90s', '10:30'. Bare numbers default to minutes."""
    text = str(raw_text or "").strip()
    if not text:
        return None, ""

    tokens = text.split()
    first = tokens[0]
    rest_tokens = tokens[1:]

    if rest_tokens and re.fullmatch(
        r"(?:h|hr|hrs|hour|hours|小时|时|m|min|mins|minute|minutes|分钟|分|s|sec|secs|second|seconds|秒钟|秒)",
        rest_tokens[0],
        re.IGNORECASE,
    ):
        first = first + rest_tokens[0]
        rest_tokens = rest_tokens[1:]

    label = " ".join(rest_tokens).strip()
    seconds_float: Optional[float] = None

    if first.startswith("-"):
        return None, "倒计时时长必须大于 0"

    mm = _HHMM_RE.match(first)
    if mm:
        seconds_float = int(mm.group("m")) * 60 + int(mm.group("s"))
    else:
        bm = _BARE_NUM_RE.match(first)
        if bm:
            seconds_float = float(bm.group("num")) * 60.0
        else:
            um = _UNIT_SEQ_RE.match(first)
            if um and any(um.group(k) is not None for k in ("h", "m", "s")):
                h = float(um.group("h") or 0.0)
                m = float(um.group("m") or 0.0)
                s = float(um.group("s") or 0.0)
                seconds_float = h * 3600.0 + m * 60.0 + s

    if seconds_float is None:
        return None, "请输入分钟数，例如：25、40 政治、1.5h 或 90s"

    total_sec = int(round(seconds_float))
    if total_sec < MIN_COUNTDOWN_SECONDS:
        return None, "倒计时时长至少 1 秒"
    if total_sec > MAX_COUNTDOWN_SECONDS:
        return None, "单次倒计时最长支持 24 小时（1440 分钟）"

    return ParsedCountdown(
        seconds=total_sec,
        label=label,
        display_duration=format_duration_zh(total_sec),
    ), ""


class CountdownState:
    """Thread-safe monotonic countdown timer state shared by launcher queries and layered tile."""

    def __init__(self) -> None:
        self.total_seconds: int = 0
        self.label: str = ""
        self.status: str = "idle"  # idle | running | paused | finished
        self._end_mono: float = 0.0
        self._paused_remaining: float = 0.0
        self._finished_notified: bool = False

    def start(self, seconds: int, label: str = "") -> None:
        self.total_seconds = max(1, min(MAX_COUNTDOWN_SECONDS, int(seconds)))
        self.label = str(label or "").strip()
        self.status = "running"
        self._end_mono = time.monotonic() + float(self.total_seconds)
        self._paused_remaining = float(self.total_seconds)
        self._finished_notified = False

    def remaining(self, now: Optional[float] = None) -> float:
        if self.status in ("idle", "finished"):
            return 0.0
        if self.status == "paused":
            return max(0.0, self._paused_remaining)
        cur = time.monotonic() if now is None else now
        rem = self._end_mono - cur
        if rem <= 0.0:
            self.status = "finished"
            self._paused_remaining = 0.0
            return 0.0
        return rem

    def adjust_minutes(self, delta_minutes: int) -> bool:
        """Scroll-wheel ±N minutes adjustment while preserving smooth progress."""
        delta_sec = int(delta_minutes) * 60
        if delta_sec == 0 or self.status == "idle":
            return False
        if self.status == "finished":
            if delta_sec > 0:
                self.start(delta_sec, self.label)
                return True
            return False

        cur_rem = self.remaining()
        new_rem = max(10.0, min(float(MAX_COUNTDOWN_SECONDS), cur_rem + float(delta_sec)))
        if abs(new_rem - cur_rem) < 0.5:
            return False
        if new_rem > float(self.total_seconds):
            self.total_seconds = int(math.ceil(new_rem))
        if self.status == "running":
            self._end_mono = time.monotonic() + new_rem
        elif self.status == "paused":
            self._paused_remaining = new_rem
        return True

    def pause(self) -> bool:
        if self.status != "running":
            return False
        rem = self.remaining()
        if self.status != "running":
            return False
        self._paused_remaining = rem
        self.status = "paused"
        return True

    def resume(self) -> bool:
        if self.status != "paused":
            return False
        self._end_mono = time.monotonic() + max(0.0, self._paused_remaining)
        self.status = "running"
        return True

    def toggle_pause(self) -> str:
        if self.status == "running":
            self.pause()
        elif self.status == "paused":
            self.resume()
        elif self.status == "finished" and self.total_seconds > 0:
            self.start(self.total_seconds, self.label)
        return self.status

    def reset(self) -> bool:
        if self.total_seconds <= 0:
            return False
        self.start(self.total_seconds, self.label)
        return True

    def stop(self) -> None:
        self.status = "idle"
        self.total_seconds = 0
        self.label = ""
        self._end_mono = 0.0
        self._paused_remaining = 0.0
        self._finished_notified = False

    def consume_finish_event(self) -> bool:
        self.remaining()
        if self.status == "finished" and not self._finished_notified:
            self._finished_notified = True
            return True
        return False

    def snapshot(self) -> Dict[str, Any]:
        rem = self.remaining()
        total = self.total_seconds
        progress = (rem / float(total)) if total > 0 else 0.0
        return {
            "status": self.status,
            "total_seconds": total,
            "remaining": rem,
            "clock": format_clock(rem),
            "eta": format_eta_clock(rem) if self.status == "running" else "",
            "label": self.label,
            "progress": max(0.0, min(1.0, progress)),
        }


CHIME_WAV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chime.wav")


def play_finish_chime(sound_path: Optional[str] = None) -> bool:
    """Play the bundled gentle felt-celesta WAV chime asynchronously (fallback to soft OS notify)."""
    if os.name != "nt":
        return False
    try:
        import winsound

        path = sound_path or CHIME_WAV_PATH
        if path and os.path.isfile(path):
            winsound.PlaySound(
                path,
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return True
        winsound.PlaySound(
            "SystemNotification",
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        return True
    except Exception:
        return False


class CountdownTile:
    """Per-pixel alpha layered countdown tile supporting Square, Circle, and Pill modes."""

    def __init__(
        self,
        tk_mod: Any,
        root: Any,
        controller: Any,
        *,
        palette: Dict[str, str],
        theme_name: str,
        dpi: float,
        icon_font_family: str,
        glyphs: Dict[str, str],
        hide_from_taskbar_fn: Callable[[Any], bool],
    ) -> None:
        self.tk = tk_mod
        self.root = root
        self.controller = controller
        self.palette = dict(palette)
        self.theme_name = theme_name
        self._dpi = max(1.0, float(dpi))
        self._icon_font_family = icon_font_family
        self._glyphs = dict(glyphs)
        self._hide_from_taskbar = hide_from_taskbar_fn
        self._digit_font_family = self._detect_digit_font()

        self._surface = layered_countdown.LayeredSurface()
        self.win: Any = None
        self._hwnd: int = 0
        self.visible: bool = False
        self.pinned: bool = True
        self.shape: str = "square"  # square | circle | pill

        self._win_x: int = 0
        self._win_y: int = 0
        self._win_w: int = 0
        self._win_h: int = 0
        self._pos_initialized: bool = False

        self._hovered: bool = False
        self._hover_id: str = ""
        self._pressed_id: str = ""
        self._hits: List[layered_countdown.HitRect] = []
        self._hover_job: Any = None
        self._tick_job: Any = None
        self._wheel_hint: str = ""
        self._wheel_job: Any = None
        self._drag_origin: Optional[Tuple[int, int, int, int]] = None
        self._pulse_start: float = time.monotonic()

    def _detect_digit_font(self) -> str:
        try:
            from tkinter import font as tkfont

            fams = set(tkfont.families(self.root))
            for cand in ("Bahnschrift", "Segoe UI Variable Display", "Segoe UI"):
                if cand in fams:
                    return cand
        except Exception:
            pass
        return ui_theme.FONT_FAMILY

    def _px(self, logical: float) -> int:
        return max(1, int(round(logical * self._dpi)))

    def _ensure_window(self) -> None:
        st = self.controller.get_state()
        self.pinned = bool(st.get("countdown_pinned", True))
        desired = str(st.get("countdown_shape") or "square").strip().lower()
        self.shape = desired if desired in SHAPE_ORDER else "square"

        if self.win is not None:
            try:
                if self.win.winfo_exists():
                    return
            except Exception:
                pass
            self.win = None
            self._hwnd = 0

        win = self.tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.title("倒计时")
        win.bind("<Enter>", self._on_enter, add="+")
        win.bind("<Leave>", self._on_leave, add="+")
        win.bind("<Motion>", self._on_motion, add="+")
        win.bind("<ButtonPress-1>", self._on_press, add="+")
        win.bind("<B1-Motion>", self._on_drag, add="+")
        win.bind("<ButtonRelease-1>", self._on_release, add="+")
        win.bind("<MouseWheel>", self._on_wheel, add="+")
        self.win = win

        win.update_idletasks()
        self._hide_from_taskbar(win)
        if os.name == "nt":
            try:
                u32 = ctypes.windll.user32
                self._hwnd = int(u32.GetParent(win.winfo_id()) or win.winfo_id())
                ui_theme.clear_frameless_tile_chrome(win)
                self._surface.enable_layered_style(self._hwnd)
            except Exception:
                self._hwnd = 0
        try:
            win.attributes("-topmost", self.pinned)
        except Exception:
            pass

    def _init_position(self, w: int, h: int, *, preserve_center: Optional[Tuple[int, int]] = None) -> None:
        sw = max(800, self.root.winfo_screenwidth())
        sh = max(600, self.root.winfo_screenheight())
        if preserve_center is not None:
            x = max(0, min(sw - w, preserve_center[0] - w // 2))
            y = max(0, min(sh - h, preserve_center[1] - h // 2))
        else:
            st = self.controller.get_state()
            sx, sy = st.get("countdown_x"), st.get("countdown_y")
            if sx is None or sy is None:
                x = max(self._px(16), sw - w - self._px(28))
                y = max(self._px(16), sh - h - self._px(72))
            else:
                x = max(-self._px(16), min(sw - self._px(60), int(sx)))
                y = max(-self._px(16), min(sh - self._px(50), int(sy)))
        self._win_x = int(x)
        self._win_y = int(y)
        self._pos_initialized = True

    # ---- pointer hit-testing, drag, click, and wheel ±1m -------------------

    def _hit_at(self, x: float, y: float) -> Optional[layered_countdown.HitRect]:
        # Later rects (buttons) take priority over broader digit/background rects
        for rect in reversed(self._hits):
            if rect.contains(x, y):
                return rect
        return None

    def _on_enter(self, _e: Any = None) -> None:
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
            self._hover_job = None
        if not self._hovered:
            self._hovered = True
            self.render_now()

    def _on_leave(self, _e: Any = None) -> None:
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:
                pass
        self._hover_job = self.root.after(180, self._check_outside)

    def _check_outside(self) -> None:
        self._hover_job = None
        if self.win is None:
            return
        try:
            px, py = self.win.winfo_pointerxy()
            inside = (self._win_x <= px <= self._win_x + self._win_w) and (
                self._win_y <= py <= self._win_y + self._win_h
            )
        except Exception:
            inside = False
        if not inside and (self._hovered or self._hover_id):
            self._hovered = False
            self._hover_id = ""
            self.render_now()

    def _on_motion(self, event: Any) -> None:
        if not self._hovered:
            self._hovered = True
        hit = self._hit_at(event.x, event.y)
        new_id = hit.id if hit else ""
        want_cursor = hit.cursor if hit else "fleur"
        if self.win is not None:
            try:
                if str(self.win.cget("cursor")) != want_cursor:
                    self.win.configure(cursor=want_cursor)
            except Exception:
                pass
        if new_id != self._hover_id:
            self._hover_id = new_id
            self.render_now()

    def _on_press(self, event: Any) -> None:
        hit = self._hit_at(event.x, event.y)
        if hit is not None:
            self._pressed_id = hit.id
            self._drag_origin = None
            return
        self._pressed_id = ""
        self._drag_origin = (event.x_root, event.y_root, self._win_x, self._win_y)

    def _on_drag(self, event: Any) -> None:
        if not self._drag_origin:
            return
        sx, sy, ox, oy = self._drag_origin
        self._win_x = int(ox + (event.x_root - sx))
        self._win_y = int(oy + (event.y_root - sy))
        if self._surface.available and self._hwnd:
            opacity = int(self.controller.get_state().get("opacity") or 96)
            self._surface.present(self._hwnd, self._win_x, self._win_y, opacity_pct=opacity)
        elif self.win is not None:
            self.win.geometry(f"+{self._win_x}+{self._win_y}")

    def _on_release(self, event: Any) -> None:
        if self._drag_origin:
            self._drag_origin = None
            try:
                self.controller.save_countdown_position(self._win_x, self._win_y)
            except Exception:
                pass
            return
        pressed = self._pressed_id
        self._pressed_id = ""
        if not pressed:
            return
        hit = self._hit_at(event.x, event.y)
        if hit is not None and hit.id == pressed:
            self._dispatch_action(pressed)

    def _on_wheel(self, event: Any) -> None:
        delta_min = 1 if event.delta > 0 else -1
        if self.controller.adjust_countdown_minutes(delta_min):
            self._show_wheel_hint("+1 分钟" if delta_min > 0 else "-1 分钟")
            self.sync_view()

    def _show_wheel_hint(self, text: str) -> None:
        if self._wheel_job is not None:
            try:
                self.root.after_cancel(self._wheel_job)
            except Exception:
                pass
        self._wheel_hint = text
        self._wheel_job = self.root.after(1100, self._clear_wheel_hint)

    def _clear_wheel_hint(self) -> None:
        self._wheel_job = None
        self._wheel_hint = ""
        if self.visible:
            self.render_now()

    def _dispatch_action(self, action_id: str) -> None:
        if action_id == "toggle_pause":
            self._on_toggle_pause()
        elif action_id == "reset":
            self._on_reset()
        elif action_id == "next_shape":
            self._on_toggle_shape()
        elif action_id == "toggle_pin":
            self._on_toggle_pin()
        elif action_id == "close":
            self.stop_and_hide()

    # ---- controls ----------------------------------------------------------

    def _on_toggle_pause(self) -> None:
        self.controller.toggle_countdown_pause()
        self.sync_view()

    def _on_reset(self) -> None:
        self.controller.reset_countdown()
        self.sync_view()

    def _on_toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.controller.update_state(countdown_pinned=self.pinned)
        if self.win is not None:
            try:
                self.win.attributes("-topmost", self.pinned)
            except Exception:
                pass
        self.render_now()

    def _on_toggle_shape(self) -> None:
        idx = SHAPE_ORDER.index(self.shape) if self.shape in SHAPE_ORDER else 0
        next_shape = SHAPE_ORDER[(idx + 1) % len(SHAPE_ORDER)]
        self.controller.set_countdown_shape(next_shape, sync_host=True)
        self.set_shape(next_shape)

    def set_shape(self, shape: str) -> None:
        target = str(shape or "").strip().lower()
        if target not in SHAPE_ORDER:
            target = "square"
        if target == self.shape:
            return
        old_center: Optional[Tuple[int, int]] = None
        if self._pos_initialized and self._win_w > 0 and self._win_h > 0:
            old_center = (self._win_x + self._win_w // 2, self._win_y + self._win_h // 2)
        self.shape = target
        self._hover_id = ""
        if self.win is not None:
            self.render_now(preserve_center=old_center)

    def show(self) -> None:
        self._ensure_window()
        self.visible = True
        self.sync_view()
        if self.win is not None:
            try:
                self.win.deiconify()
                self.win.lift()
                self.win.attributes("-topmost", self.pinned)
            except Exception:
                pass
        self.render_now()
        self._schedule_tick()

    def hide(self) -> None:
        self._cancel_tick()
        self.visible = False
        self._hovered = False
        self._hover_id = ""
        if self.win is not None:
            try:
                self.win.withdraw()
            except Exception:
                pass

    def stop_and_hide(self) -> None:
        self.controller.stop_countdown()
        self.hide()

    def apply_theme(self, palette: Dict[str, str], theme_name: str) -> None:
        self.palette = dict(palette)
        self.theme_name = theme_name
        if self.visible and self.win is not None:
            self.render_now()

    # ---- layered rendering & animation tick --------------------------------

    def render_now(self, *, preserve_center: Optional[Tuple[int, int]] = None) -> Tuple[int, int]:
        if self.win is None:
            return 0, 0
        snap = self.controller.countdown_snapshot()
        elapsed = time.monotonic() - self._pulse_start
        # Smooth ~0.45 Hz breathing / finish pulse
        phase = elapsed * 2.8

        prev_w, prev_h = self._win_w, self._win_h
        w, h, hits = layered_countdown.render_countdown_frame(
            self._surface,
            shape=self.shape,
            snap=snap,
            palette=self.palette,
            theme_name=self.theme_name,
            dpi=self._dpi,
            hovered=self._hovered,
            hover_id=self._hover_id,
            pinned=self.pinned,
            pulse_phase=phase,
            wheel_hint=self._wheel_hint,
            digit_family=self._digit_font_family,
            icon_family=self._icon_font_family,
            glyphs=self._glyphs,
        )
        self._hits = hits

        if not self._pos_initialized:
            self._init_position(w, h)
        elif preserve_center is not None:
            self._init_position(w, h, preserve_center=preserve_center)
        elif prev_w and prev_h and (w != prev_w or h != prev_h):
            # Keep tile visually anchored by its center when expanding/shrinking (e.g., pill hover or finish)
            cx = self._win_x + prev_w // 2
            cy = self._win_y + prev_h // 2
            self._init_position(w, h, preserve_center=(cx, cy))

        self._win_w, self._win_h = w, h
        opacity = int(self.controller.get_state().get("opacity") or 96)
        if self._surface.available and self._hwnd:
            self._surface.present(self._hwnd, self._win_x, self._win_y, opacity_pct=opacity)
        elif self.win is not None:
            try:
                self.win.geometry(f"{w}x{h}+{self._win_x}+{self._win_y}")
            except Exception:
                pass
        return w, h

    def sync_view(self) -> None:
        if self.win is None:
            return
        if self.controller.consume_countdown_finish():
            self._on_finished_triggered()

        snap = self.controller.countdown_snapshot()
        if snap["status"] == "idle":
            self.hide()
            return
        self.render_now()
        if self.visible:
            self._schedule_tick()

    def _on_finished_triggered(self) -> None:
        if self.controller.countdown_sound_enabled():
            play_finish_chime()
        self.controller.notify_countdown_finished()
        if self.win is not None and self.visible:
            try:
                self.win.lift()
            except Exception:
                pass

    def _schedule_tick(self) -> None:
        self._cancel_tick()
        if not self.visible:
            return
        status = self.controller.countdown_snapshot()["status"]
        # 120 ms smooth pulse while running/finished; zero idle ticks when paused!
        if status in ("running", "finished"):
            self._tick_job = self.root.after(120, self._on_tick)

    def _cancel_tick(self) -> None:
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None

    def _on_tick(self) -> None:
        self._tick_job = None
        if not self.visible:
            return
        self.sync_view()
