# -*- coding: utf-8 -*-
"""Frameless HuaJian-inspired desktop sticky tile for Obsidian Tasks.md (Tkinter, stdlib only).

Fully HiDPI-aware (100%–300% Windows scaling), Windows 11 native DWM rounded corners,
Segoe Fluent Icons toolbar, auto-sized popovers, custom pill slider, and exact Text height sync.
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import checkbox_art
import countdown_tile
import markdown_store
import state_store
import theme as ui_theme

tk = None
ttk = None
tkfont = None
filedialog = None

_ui_lock = threading.Lock()
_ui_state: Dict[str, Any] = {
    "thread": None,
    "queue": None,
    "window": None,
    "visible": False,
    "appearance": {},
    "theme": "light",
}
_owner_hwnds: List[int] = []

# Windows 11 Segoe Fluent Icons / MDL2 Assets glyphs (uniform thin vector weight)
_FLUENT_GLYPHS: Dict[str, str] = {
    "add": "\uE710",
    "search": "\uE721",
    "pin": "\uE718",
    "pinned": "\uE840",
    "open": "\uE8A7",
    "settings": "\uE713",
    "collapse": "\uE70D",
    "expand": "\uE76C",
    "close": "\uE711",
    "edit": "\uE70F",
    "delete": "\uE74D",
    "check": "\uE73E",
    "pause": "\uE769",
    "play": "\uE768",
    "reset": "\uE72C",
    "circle": "\uEA3A",
    "square": "\uE739",
}
_FALLBACK_GLYPHS: Dict[str, str] = {
    "add": "+",
    "search": "⌕",
    "pin": "○",
    "pinned": "●",
    "open": "↗",
    "settings": "⚙",
    "collapse": "▾",
    "expand": "▸",
    "close": "×",
    "edit": "✎",
    "delete": "✕",
    "check": "✓",
    "pause": "‖",
    "play": "▶",
    "reset": "↻",
    "circle": "○",
    "square": "□",
}


def _load_tk() -> None:
    global tk, ttk, tkfont, filedialog
    if tk is not None:
        return
    import tkinter as _tk
    from tkinter import filedialog as _filedialog
    from tkinter import font as _tkfont
    from tkinter import ttk as _ttk

    tk = _tk
    ttk = _ttk
    tkfont = _tkfont
    filedialog = _filedialog


def is_visible() -> bool:
    return bool(_ui_state.get("visible"))


def _enqueue(command: Tuple[Any, ...]) -> None:
    q = _ui_state.get("queue")
    if q is None:
        return
    q.put(command)
    win = _ui_state.get("window")
    if win is not None:
        win.wake_commands()


def show(controller: Any, focus: bool = False, focus_add: bool = False) -> None:
    _ensure_started(controller)
    _enqueue(("show", bool(focus), bool(focus_add)))


def hide() -> None:
    _enqueue(("hide",))


def refresh() -> None:
    _enqueue(("refresh",))


def apply_settings() -> None:
    _enqueue(("settings",))


def apply_appearance(appearance: Optional[Dict[str, Any]] = None, theme: str = "light") -> None:
    app_dict = dict(appearance or {})
    resolved = theme or app_dict.get("theme") or _ui_state.get("theme") or "light"
    _ui_state["appearance"] = app_dict
    _ui_state["theme"] = resolved
    if _ui_state.get("window") is not None:
        _enqueue(("appearance", app_dict, resolved))


def set_pinned(pinned: bool) -> None:
    _enqueue(("pinned", bool(pinned)))


def set_hide_completed(hide_done: bool) -> None:
    _enqueue(("hide_completed", bool(hide_done)))


def set_collapsed(collapsed: bool) -> None:
    _enqueue(("collapsed", bool(collapsed)))


def show_countdown(controller: Any) -> None:
    _ensure_started(controller)
    _enqueue(("countdown_show",))


def sync_countdown() -> None:
    _enqueue(("countdown_sync",))


def hide_countdown() -> None:
    _enqueue(("countdown_hide",))


def shutdown() -> None:
    _enqueue(("shutdown",))


def _ensure_started(controller: Any) -> None:
    with _ui_lock:
        thread = _ui_state.get("thread")
        if thread is not None and thread.is_alive():
            return
        q: queue.Queue = queue.Queue()
        _ui_state["queue"] = q
        thread = threading.Thread(
            target=_run_tk_loop,
            args=(q, controller),
            name="obsidian-todo-sticky",
            daemon=True,
        )
        _ui_state["thread"] = thread
        thread.start()


def _lock_foreground(lock: bool) -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.LockSetForegroundWindow(1 if lock else 2)
    except Exception:
        pass


def _activate_hwnd(root: Any) -> None:
    """Activate frameless Tk window so Windows Chinese IME works reliably."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        if hwnd:
            user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    except Exception:
        pass


def _hide_from_taskbar(root: Any) -> bool:
    """Attach the frameless tile to an invisible owner so it stays off Taskbar & Alt+Tab."""
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
        if not hwnd:
            return False

        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        ws_ex_toolwindow = 0x00000080
        ws_popup = 0x80000000
        owner = user32.CreateWindowExW(
            ws_ex_toolwindow, "STATIC", "", ws_popup, 0, 0, 0, 0, None, None, None, None
        )
        if not owner:
            return False
        _owner_hwnds.append(int(owner))

        gwl_hwndparent = -8
        gwl_exstyle = -20
        ws_ex_appwindow = 0x00040000
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            set_long = user32.SetWindowLongPtrW
            get_long = user32.GetWindowLongPtrW
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
        else:
            set_long = user32.SetWindowLongW
            get_long = user32.GetWindowLongW
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            set_long.restype = ctypes.c_long
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_long

        set_long(hwnd, gwl_hwndparent, owner)
        ex_style = get_long(hwnd, gwl_exstyle)
        ex_style = (ex_style | ws_ex_toolwindow) & ~ws_ex_appwindow
        set_long(hwnd, gwl_exstyle, ex_style)
        return True
    except Exception:
        return False


def _draw_rounded_rect(canvas: Any, x1: float, y1: float, x2: float, y2: float, r: float, **kwargs: Any) -> int:
    r = max(0.0, min(r, (x2 - x1) * 0.5, (y2 - y1) * 0.5))
    points = [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs)


def _run_tk_loop(commands: queue.Queue, controller: Any) -> None:
    _load_tk()
    _lock_foreground(True)
    try:
        root = tk.Tk()
        root.withdraw()
        dpi = ui_theme.apply_dpi_scaling(root)
        window = StickyNoteWindow(
            root,
            commands,
            controller,
            appearance=_ui_state.get("appearance") or {},
            theme_name=_ui_state.get("theme") or "light",
            dpi_scale=dpi,
        )
        _ui_state["window"] = window
        _ui_state["visible"] = False
    finally:
        _lock_foreground(False)

    window.wake_commands()
    root.after(800, window.poll_timer)
    try:
        root.mainloop()
    finally:
        _ui_state["window"] = None
        _ui_state["visible"] = False


class _Tooltip:
    """Quiet themed hover tooltip."""

    def __init__(self, widget: Any, text_fn: Callable[[], str], palette_fn: Callable[[], Dict[str, str]], dpi: float = 1.0):
        self.widget = widget
        self.text_fn = text_fn
        self.palette_fn = palette_fn
        self.dpi = dpi
        self._tip: Any = None
        self._job: Any = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._job = self.widget.after(450, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        self._job = None
        text = self.text_fn()
        if not text:
            return
        try:
            if not self.widget.winfo_viewable():
                return
        except Exception:
            return
        pal = self.palette_fn()
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        frame = tk.Frame(tip, bg=pal["BORDER"], padx=1, pady=1)
        frame.pack()
        lbl = tk.Label(
            frame,
            text=text,
            bg=pal["SURFACE"],
            fg=pal["TEXT"],
            font=ui_theme.make_font(9),
            padx=max(6, int(round(8 * self.dpi))),
            pady=max(3, int(round(4 * self.dpi))),
        )
        lbl.pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x = min(sw - tw - 8, max(8, self.widget.winfo_rootx()))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + max(4, int(round(6 * self.dpi)))
        if y + th > sh - 8:
            y = max(8, self.widget.winfo_rooty() - th - max(4, int(round(6 * self.dpi))))
        tip.wm_geometry(f"{tw}x{th}+{x}+{y}")
        self._tip = tip

    def _hide(self, _event: Any = None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class _PillSlider:
    """Clean custom HiDPI-scaled rounded pill slider replacing Win95 tk.Scale."""

    def __init__(
        self,
        parent: Any,
        *,
        value: int,
        min_val: int,
        max_val: int,
        palette: Dict[str, str],
        dpi: float,
        on_change: Callable[[int], None],
        on_release: Callable[[int], None],
    ) -> None:
        self.min_val = min_val
        self.max_val = max_val
        self.value = value
        self.palette = palette
        self.dpi = dpi
        self.on_change = on_change
        self.on_release = on_release

        h = max(20, int(round(24 * dpi)))
        w = max(140, int(round(190 * dpi)))
        self.canvas = tk.Canvas(parent, width=w, height=h, bg=palette["SURFACE"], highlightthickness=0, cursor="hand2")
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.canvas.bind("<Button-1>", self._on_pointer)
        self.canvas.bind("<B1-Motion>", self._on_pointer)
        self.canvas.bind("<ButtonRelease-1>", self._on_pointer_release)
        self._draw()

    def _pad(self) -> float:
        return max(8.0, 9.0 * self.dpi)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = max(int(c.winfo_width()), int(c.cget("width")))
        h = max(int(c.winfo_height()), int(c.cget("height")))
        pad = self._pad()
        cy = h / 2.0
        track_h = max(4.0, 4.5 * self.dpi)
        tr = track_h / 2.0
        span = max(1.0, w - 2.0 * pad)
        frac = max(0.0, min(1.0, (self.value - self.min_val) / float(self.max_val - self.min_val)))
        kx = pad + frac * span
        kr = max(6.0, 7.0 * self.dpi)

        # Background track
        _draw_rounded_rect(c, pad, cy - tr, w - pad, cy + tr, tr, fill=self.palette["SURFACE_ALT"], outline="")
        # Active filled track
        if kx > pad + 2:
            _draw_rounded_rect(c, pad, cy - tr, kx, cy + tr, tr, fill=self.palette["ACCENT_FILL"], outline="")
        # Clean circular knob with thin accent rim
        rim = max(1.5, 1.8 * self.dpi)
        c.create_oval(
            kx - kr, cy - kr, kx + kr, cy + kr,
            fill=self.palette["INPUT_BG"],
            outline=self.palette["ACCENT_FILL"],
            width=rim,
        )

    def _value_from_x(self, x: float) -> int:
        w = max(int(self.canvas.winfo_width()), int(self.canvas.cget("width")))
        pad = self._pad()
        span = max(1.0, w - 2.0 * pad)
        frac = max(0.0, min(1.0, (x - pad) / span))
        raw = self.min_val + frac * (self.max_val - self.min_val)
        # Snap to step 2
        snapped = int(round(raw / 2.0) * 2)
        return max(self.min_val, min(self.max_val, snapped))

    def _on_pointer(self, event: Any) -> None:
        nv = self._value_from_x(event.x)
        if nv != self.value:
            self.value = nv
            self._draw()
            self.on_change(nv)

    def _on_pointer_release(self, event: Any) -> None:
        nv = self._value_from_x(event.x)
        self.value = nv
        self._draw()
        self.on_change(nv)
        self.on_release(nv)


class StickyNoteWindow:
    """Frameless HuaJian-style paper tile synchronized bidirectionally with Obsidian Tasks.md."""

    def __init__(
        self,
        root: Any,
        commands: queue.Queue,
        controller: Any,
        *,
        appearance: Optional[Dict[str, Any]] = None,
        theme_name: str = "light",
        dpi_scale: Optional[float] = None,
    ) -> None:
        self.root = root
        self.commands = commands
        self.controller = controller
        self.appearance = dict(appearance or {})
        self._dpi: float = self._detect_dpi(dpi_scale)
        self._icon_font_family, self._glyphs = self._detect_icon_font()

        st = controller.get_state()
        self.style_preset: str = state_store.normalize_style_preset(st.get("style_preset"), "huajian")
        self.palette, self.theme_name = ui_theme.build_palette(
            self.appearance, theme_name, preset=self.style_preset
        )
        self.font_size: int = state_store.clamp_font_size(st.get("font_size"), 10)
        self.opacity: int = state_store.clamp_opacity(st.get("opacity"), 96)
        self.pinned: bool = bool(st.get("pinned", True))
        self.filter_mode: str = state_store.normalize_filter_mode(st.get("filter_mode"), "all")
        self.hide_completed: bool = bool(st.get("hide_completed", False))
        self.show_timestamps: bool = bool(st.get("show_timestamps", False))
        self.collapsed: bool = bool(st.get("collapsed", False))
        self._expanded_height: int = int(st.get("window_height") or self._px(380))
        self._section_labels: List[Any] = []
        self._cb_cache: Dict[Tuple[Any, ...], Any] = {}
        self._edge_frames: List[Any] = []
        self._countdown_tile: Any = None

        self.search_var = tk.StringVar(value="")
        self.add_var = tk.StringVar(value="")
        self._add_drawer_open: bool = False
        self._filter_drawer_open: bool = False
        self._add_placeholder_active: bool = True
        self._search_placeholder_active: bool = True
        self._editing_task_id: Optional[str] = None
        self._editing_entry: Any = None
        self._undo_payload: Optional[Tuple[int, str, str]] = None
        self._undo_job: Any = None
        self._flash_job: Any = None
        self._hover_leave_job: Any = None
        self._toolbar_shown: bool = False
        self._popover: Any = None
        self._last_render_sig: Any = None
        self._content_width: int = self._px(300)
        self._shutting_down: bool = False

        self._drag_origin: Optional[Tuple[int, int, int, int]] = None
        self._resize_origin: Optional[Tuple[int, int, int, int, int, int, str]] = None

        self._doc_title: str = ""
        self._tasks: List[markdown_store.TaskItem] = []
        self._file_exists: bool = False
        self._target_path: Optional[str] = None
        self._error_message: str = ""
        self._vault_configured: bool = False

        self._measure_font = tkfont.Font(root=self.root, family=ui_theme.FONT_FAMILY, size=self.font_size)
        self._build_window()
        self._bind_shortcuts()

    def _detect_dpi(self, explicit: Optional[float]) -> float:
        if explicit is not None:
            return max(1.0, min(3.5, float(explicit)))
        try:
            return max(1.0, min(3.5, float(self.root.winfo_fpixels("1i")) / 96.0))
        except Exception:
            return 1.0

    def _px(self, logical: float) -> int:
        return max(1, int(round(logical * self._dpi)))

    def _detect_icon_font(self) -> Tuple[str, Dict[str, str]]:
        try:
            families = set(tkfont.families(self.root))
            if "Segoe Fluent Icons" in families:
                return "Segoe Fluent Icons", _FLUENT_GLYPHS
            if "Segoe MDL2 Assets" in families:
                return "Segoe MDL2 Assets", _FLUENT_GLYPHS
        except Exception:
            pass
        return ui_theme.FONT_FAMILY, _FALLBACK_GLYPHS

    def _icon_font(self, delta: int = 0) -> Tuple[str, int]:
        return (self._icon_font_family, max(8, self.font_size + delta))

    def _glyph(self, name: str) -> str:
        return self._glyphs.get(name, "")

    # ---- window creation & frameless tile chrome ---------------------------

    def _build_window(self) -> None:
        self.root.title("Obsidian 待办")
        self.root.overrideredirect(True)
        self.root.configure(bg=self.palette["BG"])
        self.root.bind("<<StickyCommand>>", self._on_command_event, add="+")
        self.root.bind("<Enter>", self._on_window_enter, add="+")
        self.root.bind("<Leave>", self._on_window_leave, add="+")

        self._apply_initial_geometry()
        self.root.update_idletasks()
        _hide_from_taskbar(self.root)
        self._apply_window_chrome()

        # 8-direction frameless resize border strips packed outside outer_frame
        self._build_resize_edges()

        self.outer_frame = tk.Frame(self.root, bg=self.palette["BG"])
        self.outer_frame.pack(fill="both", expand=True)

        self._build_header()
        self.body_frame = tk.Frame(self.outer_frame, bg=self.palette["BG"])
        self.body_frame.pack(fill="both", expand=True)

        self._build_add_drawer()
        self._build_filter_drawer()
        # Pack bottom_bar BEFORE list_wrap(expand=True) so bottom_bar & resize grip never clip!
        self._build_bottom_bar()
        self._build_list_area()

        if self.collapsed:
            self._apply_collapsed_layout(resize=True)

    def _build_resize_edges(self) -> None:
        """Create invisible BG-colored perimeter strips enabling smooth 8-direction resize."""
        pal = self.palette
        edge_t = self._px(5)
        edge_side = self._px(7)
        corner_zone = self._px(22)

        def edge_dir(side: str, widget: Any, event: Any) -> str:
            if side in ("n", "s"):
                w = max(1, widget.winfo_width())
                if event.x <= corner_zone:
                    return side + "w"
                if event.x >= w - corner_zone:
                    return side + "e"
                return side
            h = max(1, widget.winfo_height())
            if event.y <= corner_zone:
                return "n" + side
            if event.y >= h - corner_zone:
                return "s" + side
            return side

        cursor_map = {
            "n": "size_ns",
            "s": "size_ns",
            "e": "size_we",
            "w": "size_we",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }

        specs = [
            ("n", "top", "x", 0, edge_t),
            ("s", "bottom", "x", 0, edge_side),
            ("w", "left", "y", edge_side, 0),
            ("e", "right", "y", edge_side, 0),
        ]
        self._edge_frames = []
        for side, pack_side, fill, w, h in specs:
            kw: Dict[str, Any] = {"bg": pal["BG"]}
            if w:
                kw["width"] = w
            if h:
                kw["height"] = h
            f = tk.Frame(self.root, **kw)
            f.pack(side=pack_side, fill=fill)

            def on_motion(ev: Any, s: str = side, wid: Any = f) -> None:
                if self.collapsed:
                    wid.configure(cursor="")
                    return
                d = edge_dir(s, wid, ev)
                wid.configure(cursor=cursor_map.get(d, ""))

            def on_press(ev: Any, s: str = side, wid: Any = f) -> None:
                self._start_edge_resize(ev, edge_dir(s, wid, ev))

            f.bind("<Motion>", on_motion)
            f.bind("<ButtonPress-1>", on_press)
            f.bind("<B1-Motion>", self._do_edge_resize)
            f.bind("<ButtonRelease-1>", self._end_edge_resize)
            self._edge_frames.append(f)

    def _remember_window_size(self) -> bool:
        val = self.appearance.get("rememberWindowSize")
        return True if val is None else bool(val)

    def _min_dims(self) -> Tuple[int, int]:
        return self._px(280), self._px(220)

    def _default_dims(self) -> Tuple[int, int]:
        return self._px(390), self._px(390)

    def _apply_initial_geometry(self) -> None:
        st = self.controller.get_state()
        sw = max(800, self.root.winfo_screenwidth())
        sh = max(600, self.root.winfo_screenheight())
        min_w, min_h = self._min_dims()
        def_w, def_h = self._default_dims()
        if self._remember_window_size():
            w = max(min_w, int(st.get("window_width") or def_w))
            h = max(min_h, int(st.get("window_height") or def_h))
        else:
            w, h = def_w, def_h
        self._expanded_height = h

        x = st.get("window_x")
        y = st.get("window_y")
        if x is None or y is None:
            x = max(self._px(20), sw - w - self._px(36))
            y = self._px(48)
        else:
            x = max(0, min(sw - self._px(100), int(x)))
            y = max(0, min(sh - self._px(80), int(y)))

        use_h = self._px(48) if self.collapsed else h
        self.root.geometry(f"{w}x{use_h}+{x}+{y}")

    def _apply_window_chrome(self) -> None:
        try:
            self.root.attributes("-topmost", self.pinned)
            self.root.attributes("-alpha", self.opacity / 100.0)
        except Exception:
            pass
        ui_theme.apply_frameless_tile_chrome(self.root, self.palette, self.theme_name)

    def _bind_drag_move(self, widget: Any) -> None:
        widget.bind("<ButtonPress-1>", self._start_drag, add="+")
        widget.bind("<B1-Motion>", self._do_drag, add="+")
        widget.bind("<ButtonRelease-1>", self._end_drag, add="+")

    def _start_drag(self, event: Any) -> None:
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _do_drag(self, event: Any) -> None:
        if not self._drag_origin:
            return
        sx, sy, wx, wy = self._drag_origin
        self.root.geometry(f"+{wx + (event.x_root - sx)}+{wy + (event.y_root - sy)}")

    def _end_drag(self, _event: Any) -> None:
        if not self._drag_origin:
            return
        self._drag_origin = None
        self._persist_geometry()

    def _start_edge_resize(self, event: Any, direction: str) -> None:
        if self.collapsed:
            return
        self._resize_origin = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_width(),
            self.root.winfo_height(),
            direction,
        )

    def _do_edge_resize(self, event: Any) -> None:
        if not self._resize_origin or self.collapsed:
            return
        sx, sy, ox, oy, ow, oh, direction = self._resize_origin
        dx = event.x_root - sx
        dy = event.y_root - sy
        min_w, min_h = self._min_dims()

        nx, ny, nw, nh = ox, oy, ow, oh
        if "e" in direction:
            nw = max(min_w, ow + dx)
        if "s" in direction:
            nh = max(min_h, oh + dy)
        if "w" in direction:
            nw = max(min_w, ow - dx)
            nx = ox + (ow - nw)
        if "n" in direction:
            nh = max(min_h, oh - dy)
            ny = oy + (oh - nh)

        self.root.geometry(f"{int(nw)}x{int(nh)}+{int(nx)}+{int(ny)}")

    def _end_edge_resize(self, _event: Any) -> None:
        if not self._resize_origin:
            return
        self._resize_origin = None
        self._persist_geometry()
        self._render_tasks(force=True)

    def _persist_geometry(self) -> None:
        try:
            x, y = int(self.root.winfo_x()), int(self.root.winfo_y())
            w, h = int(self.root.winfo_width()), int(self.root.winfo_height())
        except Exception:
            return
        _min_w, min_h = self._min_dims()
        if not self.collapsed and h >= min_h:
            self._expanded_height = h
        if self._remember_window_size():
            self.controller.save_window_geometry(x, y, w, self._expanded_height)
        else:
            self.controller.save_window_geometry(x, y, None, None)

    # ---- quiet header & Segoe Fluent Icons hover toolbar -------------------

    def _quiet_icon_btn(self, parent: Any, glyph_name: str, command: Callable[[], None], tip_fn: Callable[[], str]) -> Any:
        pal = self.palette
        btn = tk.Label(
            parent,
            text=self._glyph(glyph_name),
            bg=pal["BG"],
            fg=pal["MUTED"],
            font=self._icon_font(-1),
            padx=self._px(5),
            pady=self._px(3),
            anchor="center",
            cursor="hand2",
        )
        btn.bind("<Button-1>", lambda _e: command())
        btn.bind("<Enter>", lambda _e: btn.configure(bg=self.palette["HOVER"], fg=self.palette["TEXT"]))
        btn.bind("<Leave>", lambda _e: self._restore_tool_btn(btn))
        _Tooltip(btn, tip_fn, lambda: self.palette, dpi=self._dpi)
        return btn

    def _restore_tool_btn(self, btn: Any) -> None:
        pal = self.palette
        if btn is getattr(self, "pin_btn", None):
            btn.configure(
                text=self._glyph("pinned" if self.pinned else "pin"),
                bg=pal["BG"],
                fg=pal["ACCENT"] if self.pinned else pal["MUTED"],
            )
        elif btn is getattr(self, "add_toggle_btn", None) and self._add_drawer_open:
            btn.configure(bg=pal["TAG_BG"], fg=pal["ACCENT"])
        elif btn is getattr(self, "filter_toggle_btn", None) and (self._filter_drawer_open or self._current_search()):
            btn.configure(bg=pal["TAG_BG"], fg=pal["ACCENT"])
        else:
            btn.configure(bg=pal["BG"], fg=pal["MUTED"])

    def _build_header(self) -> None:
        pal = self.palette
        self.header = tk.Frame(self.outer_frame, bg=pal["BG"])
        self.header.pack(side="top", fill="x", padx=self._px(14), pady=(self._px(8), self._px(4)))
        self._bind_drag_move(self.header)

        self.title_lbl = tk.Label(
            self.header,
            text="今日TODO",
            bg=pal["BG"],
            fg=pal["TITLE_MUTED"],
            font=ui_theme.make_font(self.font_size),
            anchor="w",
            cursor="fleur",
        )
        self.title_lbl.pack(side="left")
        self._bind_drag_move(self.title_lbl)

        self.header_spacer = tk.Frame(self.header, bg=pal["BG"], cursor="fleur")
        self.header_spacer.pack(side="left", fill="both", expand=True)
        self._bind_drag_move(self.header_spacer)

        # Far-right close button (packed FIRST with side='right', so it ALWAYS stays at the far right!)
        self.close_btn = tk.Label(
            self.header,
            text=self._glyph("close"),
            bg=pal["BG"],
            fg=pal["TITLE_MUTED"],
            font=self._icon_font(-1),
            padx=self._px(6),
            pady=self._px(4),
            anchor="center",
            cursor="hand2",
        )
        self.close_btn.pack(side="right")
        self.close_btn.bind("<Button-1>", lambda _e: self._on_close_button())
        self.close_btn.bind("<Enter>", lambda _e: self.close_btn.configure(bg=self.palette["HOVER"], fg=self.palette["TEXT"]))
        self.close_btn.bind("<Leave>", lambda _e: self.close_btn.configure(bg=self.palette["BG"], fg=self.palette["TITLE_MUTED"]))
        _Tooltip(self.close_btn, lambda: "隐藏便签（快捷键可再次呼出）", lambda: self.palette, dpi=self._dpi)

        # Quiet toolbar packed to the LEFT of close_btn (pack side='right' AFTER close_btn!)
        self.tools_frame = tk.Frame(self.header, bg=pal["BG"])
        self.add_toggle_btn = self._quiet_icon_btn(
            self.tools_frame, "add", self._toggle_add_drawer, lambda: "新增待办 (Ctrl+N)"
        )
        self.add_toggle_btn.pack(side="left")

        self.filter_toggle_btn = self._quiet_icon_btn(
            self.tools_frame, "search", self._toggle_filter_drawer, lambda: "筛选与搜索 (Ctrl+F)"
        )
        self.filter_toggle_btn.pack(side="left")

        self.pin_btn = self._quiet_icon_btn(
            self.tools_frame, "pinned" if self.pinned else "pin", self._toggle_pin,
            lambda: "取消置顶" if self.pinned else "窗口置顶",
        )
        self.pin_btn.pack(side="left")

        self.open_btn = self._quiet_icon_btn(
            self.tools_frame, "open", self._open_tasks_file, lambda: "用 Obsidian 打开 Tasks.md (Ctrl+O)"
        )
        self.open_btn.pack(side="left")

        self.opts_btn = self._quiet_icon_btn(
            self.tools_frame, "settings", self._toggle_options_popover, lambda: "透明度、字号、配色与同步"
        )
        self.opts_btn.pack(side="left")

        self.collapse_btn = self._quiet_icon_btn(
            self.tools_frame,
            "expand" if self.collapsed else "collapse",
            self._toggle_collapse,
            lambda: "展开便签" if self.collapsed else "折叠为标题栏",
        )
        self.collapse_btn.pack(side="left", padx=(0, self._px(2)))

        for b in (
            self.add_toggle_btn, self.filter_toggle_btn, self.pin_btn,
            self.open_btn, self.opts_btn, self.collapse_btn,
        ):
            self._restore_tool_btn(b)

    def _set_toolbar_visible(self, show_bar: bool) -> None:
        keep_open = self._add_drawer_open or self._filter_drawer_open or (self._popover is not None) or self.collapsed
        want = bool(show_bar or keep_open)
        if want == self._toolbar_shown:
            return
        self._toolbar_shown = want
        if want:
            # Pack AFTER close_btn with side="right" so close_btn stays pinned at the far right edge!
            self.tools_frame.pack(side="right", after=self.close_btn)
        else:
            self.tools_frame.pack_forget()

    def _on_window_enter(self, _event: Any = None) -> None:
        if self._hover_leave_job is not None:
            try:
                self.root.after_cancel(self._hover_leave_job)
            except Exception:
                pass
            self._hover_leave_job = None
        self._set_toolbar_visible(True)

    def _on_window_leave(self, _event: Any = None) -> None:
        if self._hover_leave_job is not None:
            try:
                self.root.after_cancel(self._hover_leave_job)
            except Exception:
                pass
        self._hover_leave_job = self.root.after(220, self._check_pointer_outside)

    def _check_pointer_outside(self) -> None:
        self._hover_leave_job = None
        try:
            px, py = self.root.winfo_pointerxy()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            inside = (rx <= px <= rx + rw) and (ry <= py <= ry + rh)
        except Exception:
            inside = False
        if not inside:
            self._set_toolbar_visible(False)

    # ---- collapsible quiet drawers (add & filter) --------------------------

    ADD_PLACEHOLDER = "输入新待办（支持 **粗体** 或 学科：内容），Enter 保存…"
    SEARCH_PLACEHOLDER = "筛选待办…"

    def _build_add_drawer(self) -> None:
        pal = self.palette
        self.add_drawer = tk.Frame(self.body_frame, bg=pal["BG"])
        self.add_border = tk.Frame(self.add_drawer, bg=pal["BORDER"], padx=1, pady=1)
        self.add_border.pack(fill="x", padx=self._px(18), pady=(self._px(2), self._px(6)))

        self.add_inner = tk.Frame(self.add_border, bg=pal["INPUT_BG"])
        self.add_inner.pack(fill="x")

        self.add_entry = tk.Entry(
            self.add_inner,
            textvariable=self.add_var,
            bg=pal["INPUT_BG"],
            fg=pal["FAINT"],
            insertbackground=pal["TEXT"],
            relief="flat",
            bd=0,
            font=ui_theme.make_font(max(9, self.font_size - 1)),
        )
        self.add_entry.pack(side="left", fill="x", expand=True, padx=(self._px(8), self._px(4)), pady=self._px(5))
        self._set_add_placeholder()
        self.add_entry.bind("<Button-1>", lambda _e: self._focus_widget_ime(self.add_entry), add="+")
        self.add_entry.bind("<FocusIn>", self._on_add_focus_in)
        self.add_entry.bind("<FocusOut>", self._on_add_focus_out)
        self.add_entry.bind("<Return>", self._on_add_submit)

        self.add_btn = tk.Label(
            self.add_inner,
            text="↵",
            bg=pal["SURFACE"],
            fg=pal["MUTED"],
            font=ui_theme.make_font(max(9, self.font_size - 1), bold=True),
            padx=self._px(8),
            pady=self._px(3),
            cursor="hand2",
        )
        self.add_btn.pack(side="right", padx=(0, self._px(4)), pady=self._px(3))
        self.add_btn.bind("<Button-1>", self._on_add_submit)
        self.add_btn.bind("<Enter>", lambda _e: self.add_btn.configure(bg=self.palette["ACCENT_FILL"], fg=self.palette["ACCENT_TEXT"]))
        self.add_btn.bind("<Leave>", lambda _e: self.add_btn.configure(bg=self.palette["SURFACE"], fg=self.palette["MUTED"]))

    def _build_filter_drawer(self) -> None:
        pal = self.palette
        self.filter_drawer = tk.Frame(self.body_frame, bg=pal["BG"])
        inner_wrap = tk.Frame(self.filter_drawer, bg=pal["BG"])
        inner_wrap.pack(fill="x", padx=self._px(18), pady=(self._px(2), self._px(6)))
        self.filter_inner_wrap = inner_wrap

        tabs_row = tk.Frame(inner_wrap, bg=pal["BG"])
        tabs_row.pack(side="top", fill="x")
        self.tabs_row = tabs_row

        self._tab_buttons: Dict[str, Any] = {}
        for mode, label in (("all", "全部"), ("active", "未完成"), ("done", "已完成")):
            btn = tk.Label(
                tabs_row,
                text=label,
                bg=pal["BG"],
                fg=pal["MUTED"],
                font=ui_theme.make_font(max(8, self.font_size - 2)),
                padx=self._px(7),
                pady=self._px(2),
                cursor="hand2",
            )
            btn.pack(side="left", padx=(0, self._px(4)))
            btn.bind("<Button-1>", lambda _e, m=mode: self._set_filter_mode(m))
            self._tab_buttons[mode] = btn

        self.hide_done_pill = tk.Label(
            tabs_row,
            text="隐藏已完成",
            bg=pal["BG"],
            fg=pal["MUTED"],
            font=ui_theme.make_font(max(8, self.font_size - 2)),
            padx=self._px(6),
            pady=self._px(2),
            cursor="hand2",
        )
        self.hide_done_pill.pack(side="right")
        self.hide_done_pill.bind("<Button-1>", lambda _e: self._toggle_hide_completed())

        self.search_border = tk.Frame(inner_wrap, bg=pal["BORDER"], padx=1, pady=1)
        self.search_border.pack(side="top", fill="x", pady=(self._px(6), 0))
        self.search_inner = tk.Frame(self.search_border, bg=pal["INPUT_BG"])
        self.search_inner.pack(fill="x")

        self.search_entry = tk.Entry(
            self.search_inner,
            textvariable=self.search_var,
            bg=pal["INPUT_BG"],
            fg=pal["FAINT"],
            insertbackground=pal["TEXT"],
            relief="flat",
            bd=0,
            font=ui_theme.make_font(max(8, self.font_size - 2)),
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(self._px(8), self._px(4)), pady=self._px(4))
        self._set_search_placeholder()
        self.search_entry.bind("<Button-1>", lambda _e: self._focus_widget_ime(self.search_entry), add="+")
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self.search_entry.bind("<KeyRelease>", self._on_search_change)

        self.search_clear_btn = tk.Label(
            self.search_inner,
            text=self._glyph("close"),
            bg=pal["INPUT_BG"],
            fg=pal["MUTED"],
            font=self._icon_font(-3),
            padx=self._px(6),
            cursor="hand2",
        )
        self.search_clear_btn.bind("<Button-1>", lambda _e: self._clear_search())
        self._style_tabs()

    def _toggle_add_drawer(self, force_open: Optional[bool] = None) -> None:
        want = (not self._add_drawer_open) if force_open is None else bool(force_open)
        if want and self.collapsed:
            self._toggle_collapse()
        self._add_drawer_open = want
        if want:
            if not self.add_drawer.winfo_ismapped():
                self.add_drawer.pack(side="top", fill="x", before=self.list_wrap)
            self._set_toolbar_visible(True)
            self._focus_widget_ime(self.add_entry)
        else:
            self.add_drawer.pack_forget()
        self._restore_tool_btn(self.add_toggle_btn)

    def _toggle_filter_drawer(self, force_open: Optional[bool] = None) -> None:
        want = (not self._filter_drawer_open) if force_open is None else bool(force_open)
        if want and self.collapsed:
            self._toggle_collapse()
        self._filter_drawer_open = want
        if want:
            if not self.filter_drawer.winfo_ismapped():
                self.filter_drawer.pack(side="top", fill="x", before=self.list_wrap)
            self._set_toolbar_visible(True)
            self._focus_widget_ime(self.search_entry)
        else:
            self.filter_drawer.pack_forget()
        self._restore_tool_btn(self.filter_toggle_btn)

    def _focus_widget_ime(self, widget: Any) -> None:
        _activate_hwnd(self.root)
        try:
            widget.focus_force()
        except Exception:
            try:
                widget.focus_set()
            except Exception:
                pass

    # ---- scrollable document-flow list & subtle bottom resize grip ---------

    def _build_list_area(self) -> None:
        pal = self.palette
        self.list_wrap = tk.Frame(self.body_frame, bg=pal["BG"])
        self.list_wrap.pack(side="top", fill="both", expand=True, padx=(self._px(14), self._px(8)), pady=(self._px(2), self._px(2)))

        self.canvas = tk.Canvas(self.list_wrap, bg=pal["BG"], highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self.list_wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_yscroll)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.rows_frame = tk.Frame(self.canvas, bg=pal["BG"])
        self._canvas_win = self.canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")

        self.rows_frame.bind("<Configure>", self._on_rows_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

    def _build_bottom_bar(self) -> None:
        pal = self.palette
        self.bottom_bar = tk.Frame(self.body_frame, bg=pal["BG"])
        self.bottom_bar.pack(side="bottom", fill="x", padx=(self._px(14), 0), pady=0)

        self.status_lbl = tk.Label(
            self.bottom_bar,
            text="",
            bg=pal["BG"],
            fg=pal["MUTED"],
            font=ui_theme.make_font(max(8, self.font_size - 3)),
            anchor="w",
        )
        self.status_lbl.pack(side="left", fill="x", expand=True)

        self.undo_btn = tk.Label(
            self.bottom_bar,
            text="撤销",
            bg=pal["TAG_BG"],
            fg=pal["ACCENT"],
            font=ui_theme.make_font(max(8, self.font_size - 3), bold=True),
            padx=self._px(7),
            pady=self._px(2),
            cursor="hand2",
        )
        self.undo_btn.bind("<Button-1>", lambda _e: self._undo_delete())

        grip_sz = self._px(18)
        self.resize_grip = tk.Canvas(
            self.bottom_bar, width=grip_sz, height=grip_sz, bg=pal["BG"], highlightthickness=0, cursor="size_nw_se"
        )
        self.resize_grip.pack(side="right", anchor="se")
        self._draw_resize_grip(pal["FAINT"])
        self.resize_grip.bind("<ButtonPress-1>", lambda ev: self._start_edge_resize(ev, "se"))
        self.resize_grip.bind("<B1-Motion>", self._do_edge_resize)
        self.resize_grip.bind("<ButtonRelease-1>", self._end_edge_resize)
        self.resize_grip.bind("<Enter>", lambda _e: self._draw_resize_grip(self.palette["MUTED"]))
        self.resize_grip.bind("<Leave>", lambda _e: self._draw_resize_grip(self.palette["FAINT"]))

    def _draw_resize_grip(self, color: str) -> None:
        self.resize_grip.delete("all")
        sz = self._px(18)
        a = int(round(sz * 0.48))
        b = int(round(sz * 0.86))
        w = max(1.4, 1.2 * self._dpi)
        self.resize_grip.create_line(a, b, b, b, fill=color, width=w)
        self.resize_grip.create_line(b, a, b, b, fill=color, width=w)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-n>", lambda _e: self._toggle_add_drawer(True))
        self.root.bind("<Control-N>", lambda _e: self._toggle_add_drawer(True))
        self.root.bind("<Control-f>", lambda _e: self._toggle_filter_drawer(True))
        self.root.bind("<Control-F>", lambda _e: self._toggle_filter_drawer(True))
        self.root.bind("<F5>", lambda _e: self.reload_tasks(force=True))
        self.root.bind("<Control-r>", lambda _e: self.reload_tasks(force=True))
        self.root.bind("<Control-R>", lambda _e: self.reload_tasks(force=True))
        self.root.bind("<Control-o>", lambda _e: self._open_tasks_file())
        self.root.bind("<Control-O>", lambda _e: self._open_tasks_file())
        self.root.bind("<Escape>", self._on_escape)

    # ---- input & placeholder handlers --------------------------------------

    def _set_add_placeholder(self) -> None:
        self._add_placeholder_active = True
        self.add_var.set(self.ADD_PLACEHOLDER)
        self.add_entry.configure(fg=self.palette["FAINT"])

    def _on_add_focus_in(self, _event: Any = None) -> None:
        self.add_border.configure(bg=self.palette["ACCENT"])
        if self._add_placeholder_active:
            self._add_placeholder_active = False
            self.add_var.set("")
            self.add_entry.configure(fg=self.palette["TEXT"])

    def _on_add_focus_out(self, _event: Any = None) -> None:
        self.add_border.configure(bg=self.palette["BORDER"])
        if not self.add_var.get().strip():
            self._set_add_placeholder()

    def _on_add_submit(self, _event: Any = None) -> str:
        if self._add_placeholder_active:
            return "break"
        text = self.add_var.get().strip()
        if not text:
            return "break"
        ok, err = self.controller.add_task(text)
        if ok:
            self.add_var.set("")
            if self.filter_mode == "done":
                self._set_filter_mode("all")
            self.reload_tasks(force=True)
            self._set_status_flash(f"已写入 {self.controller.todo_file_rel()}")
        else:
            self._error_message = err or "添加待办失败"
            self._render_tasks(force=True)
        return "break"

    def _set_search_placeholder(self) -> None:
        self._search_placeholder_active = True
        self.search_var.set(self.SEARCH_PLACEHOLDER)
        self.search_entry.configure(fg=self.palette["FAINT"])

    def _on_search_focus_in(self, _event: Any = None) -> None:
        self.search_border.configure(bg=self.palette["ACCENT"])
        if self._search_placeholder_active:
            self._search_placeholder_active = False
            self.search_var.set("")
            self.search_entry.configure(fg=self.palette["TEXT"])

    def _on_search_focus_out(self, _event: Any = None) -> None:
        self.search_border.configure(bg=self.palette["BORDER"])
        if not self.search_var.get().strip():
            self._set_search_placeholder()

    def _current_search(self) -> str:
        if self._search_placeholder_active:
            return ""
        return self.search_var.get().strip()

    def _on_search_change(self, _event: Any = None) -> None:
        if self._current_search():
            self.search_clear_btn.pack(side="right")
        else:
            self.search_clear_btn.pack_forget()
        self._update_header_title()
        self._render_tasks(force=True)

    def _clear_search(self) -> None:
        self.search_var.set("")
        self.search_clear_btn.pack_forget()
        if self.root.focus_get() is not self.search_entry:
            self._set_search_placeholder()
        self._update_header_title()
        self._render_tasks(force=True)

    def _on_escape(self, _event: Any = None) -> None:
        if self._popover is not None:
            self._close_popover()
            return
        if self._editing_task_id is not None:
            self._cancel_inline_edit()
            return
        if self._current_search():
            self._clear_search()
            return
        if self._add_drawer_open:
            self._toggle_add_drawer(False)
            return
        if self._filter_drawer_open:
            self._toggle_filter_drawer(False)

    # ---- scrolling & responsive text wrap ----------------------------------

    def _on_yscroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.scrollbar.pack_forget()
            elif not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side="right", fill="y", padx=(self._px(3), 0))
        except Exception:
            pass

    def _on_rows_configure(self, _event: Any = None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas_configure(self, event: Any) -> None:
        try:
            self.canvas.itemconfigure(self._canvas_win, width=event.width)
            new_w = max(self._px(160), int(event.width))
            if abs(new_w - self._content_width) >= self._px(4):
                self._content_width = new_w
                wrap = max(self._px(160), new_w - self._px(12))
                for lbl in list(self._section_labels):
                    try:
                        if lbl.winfo_exists():
                            lbl.configure(wraplength=wrap)
                    except Exception:
                        pass
        except Exception:
            pass

    def _on_mousewheel(self, event: Any) -> None:
        if self.collapsed or not _ui_state.get("visible"):
            return
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
            if w is None or w.winfo_toplevel() is not self.root:
                return
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta * 3, "units")
        except Exception:
            pass

    def _on_close_button(self) -> None:
        self._close_popover()
        self._hide_window()

    # ---- filter / pin / collapse / vault actions ---------------------------

    def _update_header_title(self) -> None:
        base = self._doc_title or "今日TODO"
        suffix = ""
        if self._current_search():
            suffix = " · 筛选"
        elif self.filter_mode == "done":
            suffix = " · 已完成"
        elif self.filter_mode == "active":
            suffix = " · 未完成"
        self.title_lbl.configure(text=base + suffix)

    def _style_tabs(self) -> None:
        pal = self.palette
        active_n = sum(1 for t in self._tasks if not t.done)
        done_n = sum(1 for t in self._tasks if t.done)
        total_n = len(self._tasks)
        labels = {"all": f"全部 {total_n}", "active": f"未完成 {active_n}", "done": f"已完成 {done_n}"}
        for mode, btn in self._tab_buttons.items():
            btn.configure(text=labels[mode])
            if mode == self.filter_mode:
                btn.configure(bg=pal["TAG_BG"], fg=pal["ACCENT"], font=ui_theme.make_font(max(8, self.font_size - 2), bold=True))
            else:
                btn.configure(bg=pal["BG"], fg=pal["MUTED"], font=ui_theme.make_font(max(8, self.font_size - 2)))

        if self.filter_mode == "all":
            if not self.hide_done_pill.winfo_ismapped():
                self.hide_done_pill.pack(side="right")
            if self.hide_completed:
                self.hide_done_pill.configure(bg=pal["TAG_BG"], fg=pal["ACCENT"], text="✓ 隐藏已完成")
            else:
                self.hide_done_pill.configure(bg=pal["BG"], fg=pal["MUTED"], text="隐藏已完成")
        else:
            self.hide_done_pill.pack_forget()

    def _set_filter_mode(self, mode: str) -> None:
        self.filter_mode = state_store.normalize_filter_mode(mode, "all")
        self.controller.update_state(filter_mode=self.filter_mode)
        self._update_header_title()
        self._style_tabs()
        self._render_tasks(force=True)

    def _toggle_hide_completed(self) -> None:
        self.hide_completed = not self.hide_completed
        self.controller.set_hide_completed(self.hide_completed, sync_host=True)
        self._style_tabs()
        self._render_tasks(force=True)

    def _toggle_pin(self) -> None:
        self.pinned = not self.pinned
        self.controller.set_pinned(self.pinned, sync_host=True)
        self._apply_window_chrome()
        self._restore_tool_btn(self.pin_btn)

    def _collapsed_height(self) -> int:
        try:
            self.header.update_idletasks()
            req = int(self.header.winfo_reqheight())
            if req > 16:
                return req + self._px(20)
        except Exception:
            pass
        return self._px(48)

    def _toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        self.controller.update_state(collapsed=self.collapsed)
        self._apply_collapsed_layout(resize=True)

    def _apply_collapsed_layout(self, resize: bool = True) -> None:
        self.collapse_btn.configure(text=self._glyph("expand" if self.collapsed else "collapse"))
        min_w, min_h = self._min_dims()
        def_w, _def_h = self._default_dims()
        if self.collapsed:
            self._close_popover()
            if self.body_frame.winfo_ismapped():
                self.body_frame.pack_forget()
            self._set_toolbar_visible(True)
            if resize:
                w = max(min_w, self.root.winfo_width() or def_w)
                x, y = self.root.winfo_x(), self.root.winfo_y()
                self.root.geometry(f"{w}x{self._collapsed_height()}+{x}+{y}")
        else:
            if not self.body_frame.winfo_ismapped():
                self.body_frame.pack(fill="both", expand=True)
            if resize:
                w = max(min_w, self.root.winfo_width() or def_w)
                h = max(min_h, self._expanded_height)
                x, y = self.root.winfo_x(), self.root.winfo_y()
                self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _open_tasks_file(self) -> None:
        ok, msg = self.controller.open_tasks_file()
        if not ok and msg:
            self._set_status_flash(msg, is_error=True)

    def _pick_vault_folder(self) -> None:
        was_top = self.pinned
        try:
            if was_top:
                self.root.attributes("-topmost", False)
            chosen = filedialog.askdirectory(parent=self.root, title="选择 Obsidian Vault 根目录")
        finally:
            if was_top:
                try:
                    self.root.attributes("-topmost", True)
                except Exception:
                    pass
        if chosen:
            self.controller.set_local_vault(chosen)
            self.reload_tasks(force=True)
            self._set_status_flash("已关联 Vault，双向同步中")

    # ---- auto-sized HiDPI appearance & sync popover ------------------------

    def _close_popover(self) -> None:
        if self._popover is not None:
            try:
                self._popover.destroy()
            except Exception:
                pass
            self._popover = None
            self._check_pointer_outside()

    def _toggle_options_popover(self) -> None:
        if self._popover is not None:
            self._close_popover()
            return
        pal = self.palette
        pop = tk.Toplevel(self.root)
        pop.wm_overrideredirect(True)
        pop.attributes("-topmost", True)
        self._popover = pop
        self._set_toolbar_visible(True)

        pad_x = self._px(14)
        pad_y = self._px(12)
        gap = self._px(10)

        outer = tk.Frame(pop, bg=pal["BORDER"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        body = tk.Frame(outer, bg=pal["SURFACE"], padx=pad_x, pady=pad_y)
        body.pack(fill="both", expand=True)

        # 1. Opacity with sleek custom pill slider
        op_hdr = tk.Frame(body, bg=pal["SURFACE"])
        op_hdr.pack(fill="x")
        tk.Label(
            op_hdr, text="不透明度", bg=pal["SURFACE"], fg=pal["TEXT_BOLD"],
            font=ui_theme.make_font(9, bold=True),
        ).pack(side="left")
        op_val_lbl = tk.Label(
            op_hdr, text=f"{self.opacity}%", bg=pal["SURFACE"], fg=pal["ACCENT"],
            font=ui_theme.make_font(9, bold=True),
        )
        op_val_lbl.pack(side="right")

        def on_opacity_change(num: int) -> None:
            self.opacity = num
            op_val_lbl.configure(text=f"{num}%")
            try:
                self.root.attributes("-alpha", num / 100.0)
            except Exception:
                pass

        slider = _PillSlider(
            body,
            value=self.opacity,
            min_val=state_store.OPACITY_MIN,
            max_val=state_store.OPACITY_MAX,
            palette=pal,
            dpi=self._dpi,
            on_change=on_opacity_change,
            on_release=lambda num: self.controller.set_opacity(num, sync_host=True),
        )
        slider.canvas.pack(fill="x", pady=(self._px(4), gap))

        # 2. Font size
        font_row = tk.Frame(body, bg=pal["SURFACE"])
        font_row.pack(fill="x", pady=(0, gap))
        tk.Label(
            font_row, text="字号大小", bg=pal["SURFACE"], fg=pal["TEXT_BOLD"],
            font=ui_theme.make_font(9, bold=True),
        ).pack(side="left", padx=(0, self._px(16)))
        f_ctrl = tk.Frame(font_row, bg=pal["SURFACE"])
        f_ctrl.pack(side="right")
        f_lbl = tk.Label(
            f_ctrl, text=f"{self.font_size}pt", bg=pal["SURFACE"], fg=pal["MUTED"],
            font=ui_theme.make_font(9), width=5, anchor="center",
        )

        def change_font(delta: int) -> None:
            ns = state_store.clamp_font_size(self.font_size + delta, self.font_size)
            if ns == self.font_size:
                return
            self.font_size = ns
            self._measure_font.configure(size=ns)
            f_lbl.configure(text=f"{ns}pt")
            self.controller.update_state(font_size=ns)
            self._apply_theme_to_widgets()
            self._render_tasks(force=True)

        for label, delta in (("A-", -1), ("A+", 1)):
            b = tk.Label(
                f_ctrl, text=label, bg=pal["SURFACE_ALT"], fg=pal["TEXT"],
                font=ui_theme.make_font(9, bold=True),
                padx=self._px(8), pady=self._px(2), cursor="hand2",
            )
            if delta == -1:
                b.pack(side="left")
                f_lbl.pack(side="left", padx=self._px(4))
            else:
                b.pack(side="left")
            b.bind("<Button-1>", lambda _e, d=delta: change_font(d))

        # 3. Style preset
        style_row = tk.Frame(body, bg=pal["SURFACE"])
        style_row.pack(fill="x", pady=(0, gap))
        tk.Label(
            style_row, text="磁贴配色", bg=pal["SURFACE"], fg=pal["TEXT_BOLD"],
            font=ui_theme.make_font(9, bold=True),
        ).pack(side="left", padx=(0, self._px(14)))
        s_ctrl = tk.Frame(style_row, bg=pal["SURFACE"])
        s_ctrl.pack(side="right")

        def choose_preset(preset: str) -> None:
            self.style_preset = preset
            self.controller.set_style_preset(preset, sync_host=True)
            self.palette, self.theme_name = ui_theme.build_palette(
                self.appearance, self.theme_name, preset=self.style_preset
            )
            self._apply_theme_to_widgets()
            self._render_tasks(force=True)
            self._close_popover()

        for key, label in (("huajian", "花笺暖纸"), ("winshun", "Win顺")):
            active = self.style_preset == key
            b = tk.Label(
                s_ctrl, text=label,
                bg=pal["TAG_BG"] if active else pal["SURFACE_ALT"],
                fg=pal["ACCENT"] if active else pal["MUTED"],
                font=ui_theme.make_font(9, bold=active),
                padx=self._px(8), pady=self._px(3), cursor="hand2",
            )
            b.pack(side="left", padx=(self._px(4), 0))
            b.bind("<Button-1>", lambda _e, k=key: choose_preset(k))

        # 4. Clean custom toggle row for timestamps (no boxy native Win32 checkbutton!)
        ts_row = tk.Frame(body, bg=pal["SURFACE"], cursor="hand2")
        ts_row.pack(fill="x", pady=(0, gap))
        ts_lbl = tk.Label(
            ts_row, text="显示创建时间", bg=pal["SURFACE"], fg=pal["TEXT"],
            font=ui_theme.make_font(9), cursor="hand2",
        )
        ts_lbl.pack(side="left")
        ts_pill = tk.Label(
            ts_row,
            text="已开启" if self.show_timestamps else "已隐藏",
            bg=pal["TAG_BG"] if self.show_timestamps else pal["SURFACE_ALT"],
            fg=pal["ACCENT"] if self.show_timestamps else pal["MUTED"],
            font=ui_theme.make_font(8, bold=self.show_timestamps),
            padx=self._px(8),
            pady=self._px(2),
            cursor="hand2",
        )
        ts_pill.pack(side="right")

        def toggle_ts(_e: Any = None) -> None:
            self.show_timestamps = not self.show_timestamps
            self.controller.set_show_timestamps(self.show_timestamps, sync_host=True)
            ts_pill.configure(
                text="已开启" if self.show_timestamps else "已隐藏",
                bg=pal["TAG_BG"] if self.show_timestamps else pal["SURFACE_ALT"],
                fg=pal["ACCENT"] if self.show_timestamps else pal["MUTED"],
                font=ui_theme.make_font(8, bold=self.show_timestamps),
            )
            self._render_tasks(force=True)

        for w in (ts_row, ts_lbl, ts_pill):
            w.bind("<Button-1>", toggle_ts)

        # Divider
        tk.Frame(body, bg=pal["BORDER"], height=1).pack(fill="x", pady=(0, gap))

        # 5. Footer buttons
        btn_row = tk.Frame(body, bg=pal["SURFACE"])
        btn_row.pack(fill="x")
        for label, fn in (
            ("切换 Vault…", lambda: (self._close_popover(), self._pick_vault_folder())),
            ("同步", lambda: (self.reload_tasks(force=True), self._set_status_flash("已重新同步"), self._close_popover())),
        ):
            b = tk.Label(
                btn_row, text=label, bg=pal["SURFACE_ALT"], fg=pal["TEXT"],
                font=ui_theme.make_font(9), padx=self._px(9), pady=self._px(4), cursor="hand2",
            )
            b.pack(side="left", padx=(0, self._px(6)))
            b.bind("<Button-1>", lambda _e, f=fn: f())

        close_b = tk.Label(
            btn_row, text="关闭", bg=pal["TAG_BG"], fg=pal["ACCENT"],
            font=ui_theme.make_font(9, bold=True), padx=self._px(10), pady=self._px(4), cursor="hand2",
        )
        close_b.pack(side="right")
        close_b.bind("<Button-1>", lambda _e: self._close_popover())

        # Auto-size popover dynamically from true HiDPI requested dimensions!
        pop.update_idletasks()
        req_w = max(pop.winfo_reqwidth(), self._px(230))
        req_h = pop.winfo_reqheight()
        sw, sh = pop.winfo_screenwidth(), pop.winfo_screenheight()

        anchor_y = self.header.winfo_rooty() + self.header.winfo_height() + self._px(4)
        anchor_x = self.root.winfo_rootx() + self.root.winfo_width() - req_w - self._px(12)
        px = max(8, min(sw - req_w - 8, anchor_x))
        py = max(8, min(sh - req_h - 8, anchor_y))
        pop.wm_geometry(f"{req_w}x{req_h}+{px}+{py}")
        pop.update_idletasks()
        ui_theme.apply_frameless_tile_chrome(pop, pal, self.theme_name)

        pop.bind("<FocusOut>", lambda _e: self.root.after(140, self._maybe_close_popover))
        _activate_hwnd(pop)
        pop.focus_set()

    def _maybe_close_popover(self) -> None:
        if self._popover is None:
            return
        try:
            focused = self.root.focus_get()
            if focused is None or focused.winfo_toplevel() is not self._popover:
                self._close_popover()
        except Exception:
            self._close_popover()

    # ---- loading & editorial HuaJian rendering -----------------------------

    def reload_tasks(self, *, force: bool = False) -> None:
        snapshot = self.controller.load_snapshot(force=force)
        self._vault_configured = bool(snapshot["vault_configured"])
        self._doc_title = str(snapshot.get("doc_title") or "")
        self._tasks = list(snapshot["tasks"])
        self._file_exists = bool(snapshot["file_exists"])
        self._target_path = snapshot["target_path"]
        self._error_message = str(snapshot["error"] or "")

        self._update_header_title()
        self._style_tabs()

        if self._editing_task_id is not None and not force:
            return
        self._render_tasks(force=force or snapshot["changed"])

    def _set_status_flash(self, message: str, *, is_error: bool = False) -> None:
        if self._flash_job is not None:
            try:
                self.root.after_cancel(self._flash_job)
            except Exception:
                pass
        color = self.palette["DANGER"] if is_error else self.palette["MUTED"]
        self.status_lbl.configure(text=message, fg=color)
        self._flash_job = self.root.after(2600, self._clear_status_flash)

    def _clear_status_flash(self) -> None:
        self._flash_job = None
        if self._undo_payload is None:
            self.status_lbl.configure(text="")

    def _fit_text_widget(self, widget: Any) -> None:
        """Set exact display line height for a wrapped tk.Text (never over-allocating!)."""
        try:
            if not widget.winfo_exists():
                return
            w = int(widget.winfo_width())
            if w <= 24:
                return
            # On Tk Text, count('1.0', 'end', 'displaylines') returns exact rendered line count (1, 2, 3...)
            cnt = widget.count("1.0", "end", "displaylines")
            lines = int(cnt[0]) if cnt else 1
            lines = max(1, lines)
            if int(widget.cget("height")) != lines:
                widget.configure(height=lines)
        except Exception:
            pass

    def _render_tasks(self, *, force: bool = False) -> None:
        visible_tasks = markdown_store.filter_tasks(
            self._tasks,
            mode=self.filter_mode,
            hide_completed=self.hide_completed,
            search=self._current_search(),
        )
        sig = (
            self._vault_configured,
            self._file_exists,
            self._error_message,
            self.theme_name,
            self.style_preset,
            self.font_size,
            self.show_timestamps,
            self._editing_task_id,
            tuple(
                (t.id, t.line_index, t.done, t.text, t.created, t.section, t.section_level, t.indent_level)
                for t in visible_tasks
            ),
        )
        if not force and sig == self._last_render_sig:
            return
        self._last_render_sig = sig

        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._section_labels = []

        try:
            cw = int(self.canvas.winfo_width())
            if cw > self._px(80):
                self._content_width = cw
        except Exception:
            pass

        pal = self.palette

        if not self._vault_configured:
            self._render_setup_card()
            return

        if self._error_message:
            box = tk.Frame(self.rows_frame, bg=pal["DANGER_BG"], padx=self._px(14), pady=self._px(12))
            box.pack(fill="x", pady=self._px(10))
            tk.Label(
                box, text="无法读取待办文件", bg=pal["DANGER_BG"], fg=pal["DANGER"],
                font=ui_theme.make_font(self.font_size, bold=True), anchor="w",
            ).pack(fill="x")
            tk.Label(
                box, text=self._error_message, bg=pal["DANGER_BG"], fg=pal["TEXT"],
                font=ui_theme.make_font(max(9, self.font_size - 1)),
                wraplength=max(self._px(200), self._content_width - self._px(36)), justify="left", anchor="w",
            ).pack(fill="x", pady=(self._px(4), self._px(8)))
            retry = tk.Label(
                box, text="重新检测", bg=pal["SURFACE"], fg=pal["TEXT"],
                font=ui_theme.make_font(max(9, self.font_size - 1)),
                padx=self._px(10), pady=self._px(4), cursor="hand2",
            )
            retry.pack(anchor="w")
            retry.bind("<Button-1>", lambda _e: self.reload_tasks(force=True))
            return

        if not self._file_exists:
            self._render_empty_message(
                f"{self.controller.todo_file_rel()} 尚未创建",
                "悬停顶部点 +（或按 Ctrl+N）添加第一条待办，也可通过 Win顺 todo 命令写入。",
            )
            return

        if not visible_tasks:
            if self._current_search():
                self._render_empty_message("没有匹配的待办", f"未找到包含「{self._current_search()}」的任务")
            elif self.filter_mode == "active" and self._tasks:
                self._render_empty_message("今日待办已全部完成", "悬停顶部点搜索图标可切换查看全部或已完成")
            elif self.filter_mode == "done":
                self._render_empty_message("暂无已完成待办", "点击左侧小方框即可标记完成")
            else:
                self._render_empty_message("当前没有待办事项", "悬停顶部点 + 或按 Ctrl+N 新建一条")
            return

        last_section: Optional[str] = None
        first_section = True
        for task in visible_tasks:
            show_sec = bool(task.section) and (task.section_level >= 2 or task.section != self._doc_title)
            if show_sec and task.section != last_section:
                last_section = task.section
                top_pad = self._px(8) if first_section else self._px(20)
                first_section = False
                heading_size = self.font_size + (2 if task.section_level <= 2 else 1)
                sec_lbl = tk.Label(
                    self.rows_frame,
                    text=task.section,
                    bg=pal["BG"],
                    fg=pal["HEADING"],
                    font=ui_theme.make_font(heading_size, bold=True),
                    wraplength=max(self._px(160), self._content_width - self._px(12)),
                    justify="left",
                    anchor="w",
                )
                sec_lbl.pack(fill="x", pady=(top_pad, self._px(8)))
                self._section_labels.append(sec_lbl)
            else:
                first_section = False
            self._render_task_row(task)

        tk.Frame(self.rows_frame, bg=pal["BG"], height=self._px(10)).pack(fill="x")
        self.rows_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _render_setup_card(self) -> None:
        pal = self.palette
        card = tk.Frame(self.rows_frame, bg=pal["SURFACE"], padx=self._px(16), pady=self._px(16))
        card.pack(fill="x", pady=self._px(14))
        tk.Label(
            card, text="连接你的 Obsidian Vault", bg=pal["SURFACE"], fg=pal["HEADING"],
            font=ui_theme.make_font(self.font_size + 1, bold=True), anchor="w",
        ).pack(fill="x")
        tk.Label(
            card,
            text="选择与 888bik.obsidian-quicknote 相同的 Obsidian 仓库目录后，桌面磁贴将与 Tasks.md 实时双向同步。",
            bg=pal["SURFACE"], fg=pal["MUTED"],
            font=ui_theme.make_font(max(9, self.font_size - 1)),
            wraplength=max(self._px(200), self._content_width - self._px(44)), justify="left", anchor="w",
        ).pack(fill="x", pady=(self._px(6), self._px(14)))
        pick_btn = tk.Label(
            card, text="选择 Obsidian Vault 文件夹…",
            bg=pal["ACCENT_FILL"], fg=pal["ACCENT_TEXT"],
            font=ui_theme.make_font(self.font_size, bold=True),
            padx=self._px(12), pady=self._px(6), cursor="hand2",
        )
        pick_btn.pack(anchor="w")
        pick_btn.bind("<Button-1>", lambda _e: self._pick_vault_folder())

    def _render_empty_message(self, title: str, subtitle: str) -> None:
        pal = self.palette
        box = tk.Frame(self.rows_frame, bg=pal["BG"], pady=self._px(36))
        box.pack(fill="x")
        tk.Label(
            box, text=title, bg=pal["BG"], fg=pal["HEADING"],
            font=ui_theme.make_font(self.font_size + 1, bold=True),
        ).pack()
        tk.Label(
            box, text=subtitle, bg=pal["BG"], fg=pal["MUTED"],
            font=ui_theme.make_font(max(9, self.font_size - 1)),
            wraplength=max(self._px(200), self._content_width - self._px(24)), justify="center",
        ).pack(pady=(self._px(8), 0))

    def _get_checkbox_photo(self, *, checked: bool, hover: bool) -> Any:
        """Return a cached silky anti-aliased SDF PhotoImage for (• ☐) / (• ☑)."""
        pal = self.palette
        line_h = max(self._px(20), int(self._measure_font.metrics("linespace")))
        ascent = max(self._px(14), int(self._measure_font.metrics("ascent")))
        box_sz = max(self._px(13), int(round(ascent * 0.74)))
        dot_r = max(2.2, round(box_sz * 0.145, 2))
        gap_dot_box = max(self._px(6), int(round(box_sz * 0.42)))
        cb_w = int(round(dot_r * 2 + gap_dot_box + box_sz + self._px(5)))
        cb_h = line_h

        border_c = pal["MUTED"] if hover else pal["CHECK_BORDER"]
        fill_c = pal["CHECK_FILL"] if checked else pal["INPUT_BG"]
        key = (cb_w, cb_h, box_sz, checked, hover, pal["FAINT"], border_c, fill_c)
        cached = self._cb_cache.get(key)
        if cached is not None:
            return cached

        dot_x = dot_r + self._px(1.5)
        box_cx = dot_x + dot_r + gap_dot_box + box_sz * 0.5
        box_cy = cb_h * 0.50
        stroke_w = max(1.4, 1.25 * self._dpi)
        png_bytes = checkbox_art.render_bullet_checkbox_png(
            width=cb_w,
            height=cb_h,
            dot_x=dot_x,
            dot_r=dot_r,
            box_cx=box_cx,
            box_cy=box_cy,
            box_size=float(box_sz),
            stroke_w=stroke_w,
            checked=checked,
            dot_color=pal["FAINT"],
            border_color=border_c,
            fill_color=fill_c,
            check_color="#FFFFFF",
        )
        img = tk.PhotoImage(master=self.root, data=png_bytes, format="png")
        if len(self._cb_cache) > 48:
            self._cb_cache.clear()
        self._cb_cache[key] = img
        return img

    def _render_task_row(self, task: markdown_store.TaskItem) -> None:
        pal = self.palette
        indent_px = task.indent_level * self._px(18)

        row = tk.Frame(self.rows_frame, bg=pal["BG"], padx=self._px(4), pady=self._px(4))
        row.pack(fill="x", padx=(indent_px, 0), pady=self._px(1))

        # Silky anti-aliased SDF bullet + generously rounded checkbox
        img_rest = self._get_checkbox_photo(checked=task.done, hover=False)
        img_hover = self._get_checkbox_photo(checked=task.done, hover=True)
        cb_lbl = tk.Label(row, image=img_rest, bg=pal["BG"], bd=0, highlightthickness=0, cursor="hand2")
        cb_lbl.pack(side="left", anchor="n", padx=(0, self._px(5)))
        cb_lbl.bind("<Button-1>", lambda _e, t=task: self._on_toggle_task(t))
        cb_lbl.bind("<Enter>", lambda _e, w=cb_lbl, im=img_hover: w.configure(image=im))
        cb_lbl.bind("<Leave>", lambda _e, w=cb_lbl, im=img_rest: w.configure(image=im))

        # Right hover-only quiet Fluent icons (invisible at rest!)
        actions = tk.Frame(row, bg=pal["BG"])
        actions.pack(side="right", anchor="n", padx=(self._px(4), 0))
        edit_btn = tk.Label(
            actions, text=self._glyph("edit"), bg=pal["BG"], fg=pal["BG"],
            font=self._icon_font(-2), padx=self._px(4), pady=self._px(3), cursor="hand2",
        )
        edit_btn.pack(side="left")
        edit_btn.bind("<Button-1>", lambda _e, t=task: self._start_inline_edit(t))

        del_btn = tk.Label(
            actions, text=self._glyph("delete"), bg=pal["BG"], fg=pal["BG"],
            font=self._icon_font(-2), padx=self._px(4), pady=self._px(3), cursor="hand2",
        )
        del_btn.pack(side="left")
        del_btn.bind("<Button-1>", lambda _e, t=task: self._on_delete_task(t))

        # Center rich text or clean accent-bordered inline edit
        center = tk.Frame(row, bg=pal["BG"])
        center.pack(side="left", fill="x", expand=True)

        row_widgets = [row, cb_lbl, center, actions, edit_btn, del_btn]

        if self._editing_task_id == task.id:
            edit_var = tk.StringVar(value=task.text)
            edit_border = tk.Frame(center, bg=pal["ACCENT"], padx=1, pady=1)
            edit_border.pack(fill="x", pady=self._px(2))
            edit_inner = tk.Frame(edit_border, bg=pal["INPUT_BG"])
            edit_inner.pack(fill="x")
            entry = tk.Entry(
                edit_inner,
                textvariable=edit_var,
                bg=pal["INPUT_BG"],
                fg=pal["TEXT_BOLD"],
                insertbackground=pal["TEXT_BOLD"],
                relief="flat",
                bd=0,
                font=ui_theme.make_font(self.font_size),
            )
            entry.pack(fill="x", padx=self._px(6), pady=self._px(3))
            entry.bind("<Button-1>", lambda _e: self._focus_widget_ime(entry), add="+")
            entry.bind("<Return>", lambda _e, t=task, v=edit_var: self._commit_inline_edit(t, v.get()))
            entry.bind("<Escape>", lambda _e: self._cancel_inline_edit())
            self._editing_entry = entry

            def _focus_edit(e_widget: Any = entry) -> None:
                try:
                    if e_widget.winfo_exists():
                        self._focus_widget_ime(e_widget)
                        e_widget.icursor("end")
                except Exception:
                    pass

            self.root.after(15, _focus_edit)
        else:
            spans = markdown_store.parse_inline_spans(task.text, auto_bold_prefix=True)
            txt = tk.Text(
                center,
                bg=pal["BG"],
                fg=pal["FAINT"] if task.done else pal["TEXT"],
                font=ui_theme.make_font(self.font_size, overstrike=task.done),
                relief="flat",
                bd=0,
                highlightthickness=0,
                wrap="word",
                height=1,
                width=1,
                padx=0,
                pady=0,
                spacing2=self._px(2),
                cursor="hand2",
            )
            txt.tag_configure(
                "bold",
                font=ui_theme.make_font(self.font_size, bold=True, overstrike=task.done),
                foreground=pal["FAINT"] if task.done else pal["TEXT_BOLD"],
            )
            txt.tag_configure(
                "normal",
                font=ui_theme.make_font(self.font_size, bold=False, overstrike=task.done),
                foreground=pal["FAINT"] if task.done else pal["TEXT"],
            )
            txt.tag_configure(
                "code",
                font=("Consolas", max(9, self.font_size - 1)),
                background=pal["CODE_BG"],
                foreground=pal["FAINT"] if task.done else pal["TEXT"],
            )
            txt.tag_configure(
                "tag",
                font=ui_theme.make_font(self.font_size, overstrike=task.done),
                foreground=pal["FAINT"] if task.done else pal["ACCENT"],
            )
            for kind, seg in spans:
                txt.insert("end", seg, kind)
            txt.configure(state="disabled")
            txt.pack(fill="x", expand=True)
            txt.bind("<Double-Button-1>", lambda _e, t=task: self._start_inline_edit(t))
            # Dynamically sync exact line count on every real layout/resize width!
            txt.bind("<Configure>", lambda _e, w=txt: self.root.after_idle(lambda: self._fit_text_widget(w)), add="+")
            self.root.after_idle(lambda w=txt: self._fit_text_widget(w))
            row_widgets.append(txt)

            if self.show_timestamps and task.short_created:
                meta = tk.Label(
                    center,
                    text=task.short_created,
                    bg=pal["BG"],
                    fg=pal["FAINT"],
                    font=ui_theme.make_font(max(8, self.font_size - 3)),
                    anchor="w",
                )
                meta.pack(fill="x", pady=(self._px(1), 0))
                row_widgets.append(meta)
            elif task.short_created:
                _Tooltip(txt, lambda c=task.created: f"创建于 {c}（双击编辑）", lambda: self.palette, dpi=self._dpi)

        # Gentle row hover highlight & reveal edit/delete icons only on hover
        def set_row_hover(active: bool) -> None:
            bg = self.palette["HOVER"] if active else self.palette["BG"]
            for w in row_widgets:
                try:
                    w.configure(bg=bg)
                except Exception:
                    pass
            try:
                edit_btn.configure(fg=self.palette["MUTED"] if active else bg)
                del_btn.configure(fg=self.palette["MUTED"] if active else bg)
            except Exception:
                pass

        for w in row_widgets:
            w.bind("<Enter>", lambda _e: set_row_hover(True), add="+")
            w.bind("<Leave>", lambda _e: set_row_hover(False), add="+")
        edit_btn.bind("<Enter>", lambda _e: edit_btn.configure(fg=self.palette["ACCENT"]), add="+")
        del_btn.bind("<Enter>", lambda _e: del_btn.configure(fg=self.palette["DANGER"]), add="+")

    # ---- task mutations & undo ---------------------------------------------

    def _on_toggle_task(self, task: markdown_store.TaskItem) -> None:
        ok, err = self.controller.toggle_task(task.id, expected_line=task.line_index)
        if not ok and err:
            self._set_status_flash(err, is_error=True)
        self.reload_tasks(force=True)

    def _start_inline_edit(self, task: markdown_store.TaskItem) -> None:
        self._editing_task_id = task.id
        self._render_tasks(force=True)

    def _cancel_inline_edit(self) -> None:
        self._editing_task_id = None
        self._editing_entry = None
        self._render_tasks(force=True)

    def _commit_inline_edit(self, task: markdown_store.TaskItem, new_text: str) -> None:
        cleaned = str(new_text or "").strip()
        self._editing_task_id = None
        self._editing_entry = None
        if not cleaned or cleaned == task.text:
            self._render_tasks(force=True)
            return
        ok, err = self.controller.edit_task(task.id, cleaned, expected_line=task.line_index)
        if not ok and err:
            self._set_status_flash(err, is_error=True)
        self.reload_tasks(force=True)

    def _on_delete_task(self, task: markdown_store.TaskItem) -> None:
        res, err = self.controller.delete_task(task.id, expected_line=task.line_index)
        if err:
            self._set_status_flash(err, is_error=True)
            self.reload_tasks(force=True)
            return
        if res is not None:
            line_idx, raw_line, removed = res
            self._show_undo_bar(line_idx, raw_line, removed.plain_text)
        self.reload_tasks(force=True)

    def _show_undo_bar(self, line_index: int, raw_line: str, label: str) -> None:
        if self._undo_job is not None:
            try:
                self.root.after_cancel(self._undo_job)
            except Exception:
                pass
        self._undo_payload = (line_index, raw_line, label)
        short = label if len(label) <= 18 else label[:17] + "…"
        self.status_lbl.configure(text=f"已删除「{short}」", fg=self.palette["TEXT"])
        self.undo_btn.pack(side="right", padx=(self._px(4), self._px(8)), before=self.resize_grip)
        self._undo_job = self.root.after(6000, self._clear_undo_bar)

    def _clear_undo_bar(self) -> None:
        self._undo_job = None
        self._undo_payload = None
        self.undo_btn.pack_forget()
        self.status_lbl.configure(text="")

    def _undo_delete(self) -> None:
        payload = self._undo_payload
        self._clear_undo_bar()
        if not payload:
            return
        line_index, raw_line, _label = payload
        ok, err = self.controller.restore_line(line_index, raw_line)
        if not ok and err:
            self._set_status_flash(err, is_error=True)
        else:
            self._set_status_flash("已恢复待办")
        self.reload_tasks(force=True)

    # ---- theme application -------------------------------------------------

    def _apply_theme_to_widgets(self) -> None:
        pal = self.palette
        self._measure_font.configure(size=self.font_size)
        for frame in (
            self.root, self.outer_frame, self.header, self.header_spacer, self.tools_frame,
            self.body_frame, self.add_drawer, self.filter_drawer, self.filter_inner_wrap,
            self.tabs_row, self.list_wrap, self.canvas, self.rows_frame, self.bottom_bar,
            self.resize_grip, *self._edge_frames,
        ):
            frame.configure(bg=pal["BG"])
        self._cb_cache.clear()

        self.title_lbl.configure(bg=pal["BG"], fg=pal["TITLE_MUTED"], font=ui_theme.make_font(self.font_size))
        self.close_btn.configure(bg=pal["BG"], fg=pal["TITLE_MUTED"], font=self._icon_font(-1))
        for b in (
            self.add_toggle_btn, self.filter_toggle_btn, self.pin_btn,
            self.open_btn, self.opts_btn, self.collapse_btn,
        ):
            b.configure(font=self._icon_font(-1))
            self._restore_tool_btn(b)

        self.add_border.configure(bg=pal["BORDER"])
        self.add_inner.configure(bg=pal["INPUT_BG"])
        self.add_entry.configure(
            bg=pal["INPUT_BG"],
            fg=pal["FAINT"] if self._add_placeholder_active else pal["TEXT"],
            insertbackground=pal["TEXT"],
            font=ui_theme.make_font(max(9, self.font_size - 1)),
        )
        self.add_btn.configure(bg=pal["SURFACE"], fg=pal["MUTED"])
        self.search_border.configure(bg=pal["BORDER"])
        self.search_inner.configure(bg=pal["INPUT_BG"])
        self.search_entry.configure(
            bg=pal["INPUT_BG"],
            fg=pal["FAINT"] if self._search_placeholder_active else pal["TEXT"],
            insertbackground=pal["TEXT"],
            font=ui_theme.make_font(max(8, self.font_size - 2)),
        )
        self.search_clear_btn.configure(bg=pal["INPUT_BG"], fg=pal["MUTED"])
        self.status_lbl.configure(bg=pal["BG"], fg=pal["MUTED"], font=ui_theme.make_font(max(8, self.font_size - 3)))
        self.undo_btn.configure(bg=pal["TAG_BG"], fg=pal["ACCENT"], font=ui_theme.make_font(max(8, self.font_size - 3), bold=True))
        self._draw_resize_grip(pal["FAINT"])
        self._apply_window_chrome()
        self._style_tabs()
        if self._countdown_tile is not None:
            self._countdown_tile.set_shape(st.get("countdown_shape", "square"))
            self._countdown_tile.apply_theme(self.palette, self.theme_name)

    # ---- thread command queue & external poll ------------------------------

    def wake_commands(self) -> None:
        try:
            self.root.event_generate("<<StickyCommand>>", when="tail")
        except Exception:
            pass

    def _on_command_event(self, _event: Any = None) -> None:
        self._drain_commands()

    def _drain_commands(self) -> None:
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                break
            if not cmd:
                continue
            kind = cmd[0]
            if kind == "show":
                _, focus, focus_add = cmd
                self._show_window(focus=focus, focus_add=focus_add)
            elif kind == "hide":
                self._hide_window()
            elif kind == "refresh":
                self.reload_tasks(force=True)
            elif kind == "settings":
                self._sync_from_controller_state()
                self.reload_tasks(force=True)
            elif kind == "appearance":
                _, app_dict, theme_name = cmd
                self.appearance = dict(app_dict or {})
                self.palette, self.theme_name = ui_theme.build_palette(
                    self.appearance, theme_name, preset=self.style_preset
                )
                self._apply_theme_to_widgets()
                self._render_tasks(force=True)
            elif kind == "pinned":
                self.pinned = bool(cmd[1])
                self._apply_window_chrome()
                self._restore_tool_btn(self.pin_btn)
            elif kind == "hide_completed":
                self.hide_completed = bool(cmd[1])
                self._style_tabs()
                self._render_tasks(force=True)
            elif kind == "collapsed":
                self.collapsed = bool(cmd[1])
                self._apply_collapsed_layout(resize=True)
            elif kind == "countdown_show":
                self._ensure_countdown_tile()
                _lock_foreground(True)
                try:
                    self._countdown_tile.show()
                finally:
                    _lock_foreground(False)
            elif kind == "countdown_sync":
                if self._countdown_tile is not None:
                    self._countdown_tile.sync_view()
            elif kind == "countdown_hide":
                if self._countdown_tile is not None:
                    self._countdown_tile.hide()
                    self._countdown_tile._surface.destroy()
            elif kind == "shutdown":
                self._shutting_down = True
                self._close_popover()
                if self._countdown_tile is not None:
                    self._countdown_tile.hide()
                    self._countdown_tile._surface.destroy()
                try:
                    self.root.destroy()
                except Exception:
                    pass
                return

    def _ensure_countdown_tile(self) -> Any:
        if self._countdown_tile is None:
            self._countdown_tile = countdown_tile.CountdownTile(
                tk,
                self.root,
                self.controller,
                palette=self.palette,
                theme_name=self.theme_name,
                dpi=self._dpi,
                icon_font_family=self._icon_font_family,
                glyphs=self._glyphs,
                hide_from_taskbar_fn=_hide_from_taskbar,
            )
        return self._countdown_tile

    def _sync_from_controller_state(self) -> None:
        st = self.controller.get_state()
        self.pinned = bool(st.get("pinned", True))
        self.opacity = state_store.clamp_opacity(st.get("opacity"), 96)
        self.hide_completed = bool(st.get("hide_completed", False))
        self.show_timestamps = bool(st.get("show_timestamps", False))
        new_preset = state_store.normalize_style_preset(st.get("style_preset"), "huajian")
        if new_preset != self.style_preset:
            self.style_preset = new_preset
            self.palette, self.theme_name = ui_theme.build_palette(
                self.appearance, self.theme_name, preset=self.style_preset
            )
            self._apply_theme_to_widgets()
        else:
            self._apply_window_chrome()
            self._restore_tool_btn(self.pin_btn)
            self._style_tabs()
            if self._countdown_tile is not None:
                self._countdown_tile.apply_theme(self.palette, self.theme_name)

    def _show_window(self, *, focus: bool = False, focus_add: bool = False) -> None:
        self._sync_from_controller_state()
        self.reload_tasks(force=True)
        if focus_add and self.collapsed:
            self.collapsed = False
            self.controller.update_state(collapsed=False)
            self._apply_collapsed_layout(resize=True)
        if not focus:
            _lock_foreground(True)
        try:
            self.root.deiconify()
            self.root.lift()
            self._apply_window_chrome()
            if focus:
                _activate_hwnd(self.root)
                try:
                    self.root.focus_force()
                except Exception:
                    self.root.focus_set()
                if focus_add:
                    self._toggle_add_drawer(True)
        finally:
            if not focus:
                _lock_foreground(False)
        _ui_state["visible"] = True

    def _hide_window(self) -> None:
        self._close_popover()
        self._cancel_inline_edit()
        try:
            self.root.withdraw()
        except Exception:
            pass
        _ui_state["visible"] = False

    def poll_timer(self) -> None:
        if self._shutting_down:
            return
        self._drain_commands()
        if _ui_state.get("visible"):
            try:
                self.reload_tasks(force=False)
            except Exception:
                pass
        self.root.after(800, self.poll_timer)
