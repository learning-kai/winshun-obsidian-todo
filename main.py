# -*- coding: utf-8 -*-
"""Obsidian 待办便签 — resident Win顺 plugin synced bidirectionally with Obsidian Tasks.md."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import countdown_tile
import markdown_store
import state_store
import sticky_window
import theme as ui_theme
import winshun

PLUGIN_ID = "learning-kai.obsidian-todo"
PLUGIN_NAME = "Obsidian 待办便签"
DEFAULT_HOTKEY = "Ctrl+Alt+B"
MAX_LAUNCHER_TASKS = 12
SHOW_HIDE_WORDS = {"show", "hide", "toggle", "open", "显示", "隐藏", "打开", "关闭", "便签", "收起"}
COUNTDOWN_PRESETS = (15, 25, 40, 60)


def _stable_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:14]


class ObsidianTodoApp:
    """Thread-safe application controller shared by JSON-RPC handlers and Tk sticky UI."""

    def __init__(self, data_dir: str = "") -> None:
        self.lock = threading.RLock()
        self.data_dir: str = str(data_dir or "")
        self.plugin: Any = None
        self.settings: Dict[str, Any] = {}
        self.state: Dict[str, Any] = dict(state_store.DEFAULT_STATE)
        self.store = markdown_store.MarkdownTodoStore()
        self.countdown = countdown_tile.CountdownState()
        self._cancelled_queries: set[int] = set()
        self._registered_hotkey: str = ""
        self._remember_window_size: bool = True

    # ---- lifecycle & settings ---------------------------------------------

    def initialize(self, plugin: Any) -> None:
        with self.lock:
            self.plugin = plugin
            self.data_dir = str(getattr(plugin, "data_dir", "") or winshun.data_dir())
            self.state = state_store.load_state(self.data_dir)
            self.settings = dict(getattr(plugin, "settings", None) or {})
            self._sync_settings_into_state_locked(initial=True)

            appearance = getattr(plugin, "appearance", None)
            if not isinstance(appearance, dict):
                appearance = {}
            theme_name = str(getattr(plugin, "theme", "light") or "light")
            self._apply_remember_size_locked(appearance)

        sticky_window.apply_appearance(appearance, theme_name)
        if bool(self.settings.get("show_on_start", True)):
            self.show_sticky(focus=False)

        threading.Thread(target=self.register_hotkeys, daemon=True, name="obsidian-todo-hotkey").start()

    def apply_settings(self, new_settings: Dict[str, Any]) -> None:
        with self.lock:
            prev_show = bool(self.settings.get("show_on_start", True))
            self.settings = dict(new_settings or {})
            self._sync_settings_into_state_locked(initial=False)
            now_show = bool(self.settings.get("show_on_start", True))

        sticky_window.apply_settings()
        if now_show != prev_show:
            if now_show:
                self.show_sticky(focus=False)
            else:
                self.hide_sticky()
        threading.Thread(target=self.register_hotkeys, daemon=True, name="obsidian-todo-hotkey").start()

    def apply_appearance(self, appearance: Dict[str, Any], theme_name: str) -> None:
        app_dict = dict(appearance or {})
        with self.lock:
            self._apply_remember_size_locked(app_dict)
        sticky_window.apply_appearance(app_dict, theme_name)

    def _apply_remember_size_locked(self, appearance: Dict[str, Any]) -> None:
        val = appearance.get("rememberWindowSize")
        remember = True if val is None else bool(val)
        self._remember_window_size = remember
        if not remember:
            changed = (
                self.state.get("window_width") is not None
                or self.state.get("window_height") is not None
            )
            self.state["window_width"] = None
            self.state["window_height"] = None
            if changed:
                state_store.save_state(self.data_dir, self.state)

    def _sync_settings_into_state_locked(self, *, initial: bool) -> None:
        if "opacity" in self.settings and self.settings.get("opacity") is not None:
            self.state["opacity"] = state_store.clamp_opacity(self.settings.get("opacity"), self.state["opacity"])
        if "always_on_top" in self.settings and isinstance(self.settings.get("always_on_top"), bool):
            if not initial or "pinned" not in self.state:
                self.state["pinned"] = bool(self.settings["always_on_top"])
            elif initial:
                self.state["pinned"] = bool(self.settings["always_on_top"])
        if "hide_completed" in self.settings and isinstance(self.settings.get("hide_completed"), bool):
            self.state["hide_completed"] = bool(self.settings["hide_completed"])
        if "style_preset" in self.settings and self.settings.get("style_preset"):
            self.state["style_preset"] = state_store.normalize_style_preset(
                self.settings.get("style_preset"), self.state["style_preset"]
            )
        if "show_timestamps" in self.settings and isinstance(self.settings.get("show_timestamps"), bool):
            self.state["show_timestamps"] = bool(self.settings["show_timestamps"])
        if "countdown_shape" in self.settings and self.settings.get("countdown_shape"):
            self.state["countdown_shape"] = state_store.normalize_countdown_shape(
                self.settings.get("countdown_shape"), self.state["countdown_shape"]
            )
        state_store.save_state(self.data_dir, self.state)

    def _push_host_settings(self, updates: Dict[str, Any]) -> None:
        plugin = self.plugin
        if plugin is None or not updates:
            return
        try:
            plugin._notify("winshun/settings/update", {"settings": updates})
        except Exception as exc:
            winshun.debug(f"{PLUGIN_ID}: settings update failed: {exc}")

    # ---- state & paths ----------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        with self.lock:
            return dict(self.state)

    def update_state(self, **kwargs: Any) -> Dict[str, Any]:
        with self.lock:
            for k, v in kwargs.items():
                if k in state_store.DEFAULT_STATE:
                    self.state[k] = v
            state_store.save_state(self.data_dir, self.state)
            return dict(self.state)

    def save_window_geometry(self, x: int, y: int, width: Optional[int], height: Optional[int]) -> None:
        with self.lock:
            self.state["window_x"] = int(x)
            self.state["window_y"] = int(y)
            if self._remember_window_size and width is not None and height is not None:
                self.state["window_width"] = max(state_store.MIN_WIDTH, int(width))
                self.state["window_height"] = max(state_store.MIN_HEIGHT, int(height))
            state_store.save_state(self.data_dir, self.state)

    def set_pinned(self, pinned: bool, *, sync_host: bool = False) -> bool:
        val = bool(pinned)
        self.update_state(pinned=val)
        if sync_host:
            self._push_host_settings({"always_on_top": val})
        return val

    def set_opacity(self, opacity: int, *, sync_host: bool = False) -> int:
        val = state_store.clamp_opacity(opacity)
        self.update_state(opacity=val)
        if sync_host:
            self._push_host_settings({"opacity": val})
        return val

    def set_hide_completed(self, hide_done: bool, *, sync_host: bool = False) -> bool:
        val = bool(hide_done)
        self.update_state(hide_completed=val)
        if sync_host:
            self._push_host_settings({"hide_completed": val})
        return val

    def set_style_preset(self, preset: str, *, sync_host: bool = False) -> str:
        val = state_store.normalize_style_preset(preset)
        self.update_state(style_preset=val)
        if sync_host:
            self._push_host_settings({"style_preset": val})
        return val

    def set_show_timestamps(self, show_ts: bool, *, sync_host: bool = False) -> bool:
        val = bool(show_ts)
        self.update_state(show_timestamps=val)
        if sync_host:
            self._push_host_settings({"show_timestamps": val})
        return val

    def set_local_vault(self, folder: str) -> None:
        self.update_state(local_vault=str(folder or "").strip())

    def configured_vault(self) -> str:
        with self.lock:
            host_vault = str(self.settings.get("vault") or "").strip().strip('"')
            if host_vault:
                return host_vault
            return str(self.state.get("local_vault") or "").strip().strip('"')

    def todo_file_rel(self) -> str:
        with self.lock:
            raw = str(self.settings.get("todo_file") or markdown_store.DEFAULT_TODO_FILE).strip()
            return raw or markdown_store.DEFAULT_TODO_FILE

    def add_timestamp_source(self) -> bool:
        with self.lock:
            return bool(self.settings.get("add_timestamp_source", True))

    def current_hotkey(self) -> str:
        with self.lock:
            if "hotkey" in self.settings:
                return str(self.settings.get("hotkey") or "").strip()
            return DEFAULT_HOTKEY

    def vault_display_name(self) -> str:
        raw = self.configured_vault()
        if not raw:
            return ""
        try:
            p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
            return p.name or str(p)
        except Exception:
            return raw

    def last_sync_time(self) -> str:
        return self.store.last_sync

    def _plugin_folder(self) -> str:
        return os.path.dirname(os.path.abspath(__file__))

    def icon_png_path(self) -> str:
        path = os.path.join(self._plugin_folder(), "icon.png")
        return path if os.path.isfile(path) else ""

    def icon_ico_path(self) -> str:
        path = os.path.join(self._plugin_folder(), "icon.ico")
        return path if os.path.isfile(path) else ""

    # ---- tasks operations -------------------------------------------------

    def load_snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return {
                "vault_configured": False,
                "doc_title": "",
                "tasks": [],
                "file_exists": False,
                "target_path": None,
                "changed": False,
                "error": "",
            }
        try:
            tasks, target, exists, changed = self.store.load(vault, rel, force=force)
            return {
                "vault_configured": True,
                "doc_title": self.store.doc_title,
                "tasks": tasks,
                "file_exists": exists,
                "target_path": str(target) if target else None,
                "changed": changed,
                "error": "",
            }
        except (markdown_store.StoreError, OSError) as exc:
            return {
                "vault_configured": True,
                "doc_title": "",
                "tasks": [],
                "file_exists": False,
                "target_path": None,
                "changed": True,
                "error": str(exc),
            }

    def add_task(self, text: str) -> Tuple[bool, str]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return False, "尚未配置 Obsidian Vault"
        try:
            self.store.add_task(vault, rel, text, add_timestamp_source=self.add_timestamp_source())
            return True, ""
        except (markdown_store.StoreError, OSError) as exc:
            winshun.debug(f"{PLUGIN_ID}: add_task failed: {exc}")
            return False, str(exc)

    def toggle_task(self, task_id: str, *, expected_line: Optional[int] = None) -> Tuple[Optional[markdown_store.TaskItem], str]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return None, "尚未配置 Obsidian Vault"
        try:
            updated = self.store.toggle_task(vault, rel, task_id, expected_line=expected_line)
            if updated is None:
                return None, "该待办已不存在或已被外部删除"
            return updated, ""
        except (markdown_store.StoreError, OSError) as exc:
            winshun.debug(f"{PLUGIN_ID}: toggle_task failed: {exc}")
            return None, str(exc)

    def edit_task(self, task_id: str, new_text: str, *, expected_line: Optional[int] = None) -> Tuple[bool, str]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return False, "尚未配置 Obsidian Vault"
        try:
            updated = self.store.edit_task(vault, rel, task_id, new_text, expected_line=expected_line)
            if updated is None:
                return False, "未找到要修改的待办（可能已被外部改动）"
            return True, ""
        except (markdown_store.StoreError, OSError) as exc:
            winshun.debug(f"{PLUGIN_ID}: edit_task failed: {exc}")
            return False, str(exc)

    def delete_task(
        self, task_id: str, *, expected_line: Optional[int] = None
    ) -> Tuple[Optional[Tuple[int, str, markdown_store.TaskItem]], str]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return None, "尚未配置 Obsidian Vault"
        try:
            removed = self.store.delete_task(vault, rel, task_id, expected_line=expected_line)
            if removed is None:
                return None, "未找到要删除的待办"
            return removed, ""
        except (markdown_store.StoreError, OSError) as exc:
            winshun.debug(f"{PLUGIN_ID}: delete_task failed: {exc}")
            return None, str(exc)

    def restore_line(self, line_index: int, raw_line: str) -> Tuple[bool, str]:
        vault = self.configured_vault()
        rel = self.todo_file_rel()
        if not vault:
            return False, "尚未配置 Obsidian Vault"
        try:
            self.store.restore_line(vault, rel, line_index, raw_line)
            return True, ""
        except (markdown_store.StoreError, OSError) as exc:
            winshun.debug(f"{PLUGIN_ID}: restore_line failed: {exc}")
            return False, str(exc)

    def open_tasks_file(self) -> Tuple[bool, str]:
        snap = self.load_snapshot(force=False)
        if not snap["vault_configured"]:
            return False, "尚未配置 Obsidian Vault"
        if snap["error"]:
            return False, snap["error"]
        target = snap["target_path"]
        if not target:
            return False, "无法确定待办文件路径"
        if not snap["file_exists"]:
            return False, f"{self.todo_file_rel()} 尚未创建，请先添加一条待办"
        plugin = self.plugin
        if plugin is None:
            return False, "未连接到 Win顺 宿主"
        try:
            res = plugin.tool("open_item", path=str(target))
            if isinstance(res, dict) and res.get("error"):
                msg = res["error"].get("message") or "无法打开文件"
                return False, str(msg)
            return True, ""
        except Exception as exc:
            winshun.debug(f"{PLUGIN_ID}: open_item failed: {exc}")
            return False, f"打开失败：{exc}"

    # ---- sticky window control --------------------------------------------

    def show_sticky(self, *, focus: bool = False, focus_add: bool = False) -> None:
        sticky_window.show(self, focus=focus, focus_add=focus_add)

    def hide_sticky(self) -> None:
        sticky_window.hide()

    def toggle_sticky(self, *, focus: bool = True) -> bool:
        if sticky_window.is_visible():
            self.hide_sticky()
            return False
        self.show_sticky(focus=focus)
        return True

    # ---- minimalist countdown tile ----------------------------------------

    def start_countdown(self, seconds: int, label: str = "") -> Dict[str, Any]:
        with self.lock:
            self.countdown.start(seconds, label)
            snap = self.countdown.snapshot()
        sticky_window.show_countdown(self)
        return snap

    def toggle_countdown_pause(self) -> Dict[str, Any]:
        with self.lock:
            self.countdown.toggle_pause()
            snap = self.countdown.snapshot()
        sticky_window.sync_countdown()
        return snap

    def reset_countdown(self) -> Dict[str, Any]:
        with self.lock:
            self.countdown.reset()
            snap = self.countdown.snapshot()
        sticky_window.show_countdown(self)
        return snap

    def stop_countdown(self) -> None:
        with self.lock:
            self.countdown.stop()
        sticky_window.hide_countdown()

    def adjust_countdown_minutes(self, delta_minutes: int) -> bool:
        with self.lock:
            ok = self.countdown.adjust_minutes(delta_minutes)
        if ok:
            sticky_window.sync_countdown()
        return ok

    def countdown_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return self.countdown.snapshot()

    def consume_countdown_finish(self) -> bool:
        with self.lock:
            return self.countdown.consume_finish_event()

    def countdown_sound_enabled(self) -> bool:
        with self.lock:
            return bool(self.settings.get("countdown_sound", True))

    def notify_countdown_finished(self) -> None:
        snap = self.countdown_snapshot()
        title = snap["label"] or countdown_tile.format_duration_zh(snap["total_seconds"])
        plugin = self.plugin
        if plugin is not None:
            try:
                plugin.notify_user(f"倒计时结束：{title}")
            except Exception:
                pass

    def save_countdown_position(self, x: int, y: int) -> None:
        self.update_state(countdown_x=int(x), countdown_y=int(y))

    def set_countdown_shape(self, shape: str, *, sync_host: bool = False) -> str:
        val = state_store.normalize_countdown_shape(shape)
        self.update_state(countdown_shape=val)
        if sync_host:
            self._push_host_settings({"countdown_shape": val})
        return val

    # ---- host hotkeys -----------------------------------------------------

    def register_hotkeys(self, attempt: int = 0) -> bool:
        plugin = self.plugin
        if plugin is None:
            return False
        key = self.current_hotkey()
        keys = [key] if key else []
        with self.lock:
            self._registered_hotkey = key

        try:
            plugin.hotkeys(keys)
        except Exception as exc:
            winshun.debug(f"{PLUGIN_ID}: winshun/hotkeys failed: {exc}")

        caps = getattr(plugin, "capabilities", None) or {}
        if not caps.get("hostHotkeys"):
            return False

        try:
            res = plugin.tool("register_hotkeys", keys=keys)
        except Exception as exc:
            res = {"error": {"message": str(exc)}}

        ok = isinstance(res, dict) and not res.get("error") and not (res.get("errors") or {})
        if ok:
            return True
        if attempt == 0 and keys:
            time.sleep(0.3)
            return self.register_hotkeys(attempt=1)
        errors = res.get("errors") if isinstance(res, dict) else None
        if isinstance(errors, dict) and any(code == 1409 for code in errors.values()):
            winshun.debug(f"{PLUGIN_ID}: 快捷键 {keys!r} 已被其他程序占用 (1409)，可在插件设置中更换")
        else:
            winshun.debug(f"{PLUGIN_ID}: register_hotkeys failed for {keys!r}: {res!r}")
        with self.lock:
            self._registered_hotkey = ""
        return False

    def handle_hotkey(self, key: str) -> bool:
        with self.lock:
            bound = self._registered_hotkey
        if not bound or str(key or "").strip().casefold() != bound.casefold():
            # Ignore stale / late hotkey notifications not matching current binding.
            return False
        self.toggle_sticky(focus=True)
        return True

    # ---- cancel tracking --------------------------------------------------

    def cancel_query(self, query_id: int) -> None:
        with self.lock:
            self._cancelled_queries.add(int(query_id))
            if len(self._cancelled_queries) > 128:
                self._cancelled_queries = set(list(self._cancelled_queries)[-64:])

    def is_cancelled(self, query_id: int) -> bool:
        with self.lock:
            return int(query_id) in self._cancelled_queries


app = ObsidianTodoApp()


# ---- launcher items & protocol handlers -----------------------------------

def _sticky_toggle_item(vault_label: str, active_count: int, total_count: int) -> dict:
    visible = sticky_window.is_visible()
    title = "隐藏 Obsidian 待办便签" if visible else "显示 Obsidian 待办便签"
    subtitle = (
        f"{vault_label} · 未完成 {active_count} / 共 {total_count} · "
        f"Enter {'隐藏' if visible else '显示'} · Ctrl+Enter 打开 Tasks.md · Shift+Enter 新建待办"
    )
    return winshun.item(
        "sticky-toggle",
        title,
        subtitle,
        icon="ph:note-pencil",
        score=95,
        copy=title,
        data={"kind": "toggle-sticky"},
        actions=[
            winshun.action("toggle-sticky", "隐藏便签" if visible else "显示便签", "Enter"),
            winshun.action("open-file", "打开 Tasks.md", "Ctrl+Enter"),
            winshun.action("focus-add", "在便签中新建待办", "Shift+Enter"),
        ],
    )


def _open_file_item(target_path: str, rel: str, exists: bool) -> dict:
    if exists:
        return winshun.item(
            "open-tasks-file",
            f"打开 {rel}",
            f"{target_path} · 用默认程序（Obsidian）打开",
            icon="ext:md",
            path=target_path,
            copy=target_path,
            score=40,
        )
    return winshun.item(
        "tasks-file-missing",
        f"{rel} 尚未创建",
        "输入待办内容后按 Enter 会自动创建文件",
        icon="ph:file-plus",
        valid=False,
        score=35,
    )


def _task_row_item(task: markdown_store.TaskItem, rel: str) -> dict:
    prefix = "[✓] " if task.done else "[ ] "
    meta_parts = []
    if task.section and task.section != "待办事项":
        meta_parts.append(task.section)
    if task.short_created:
        meta_parts.append(task.short_created)
    meta_parts.append("Enter 取消完成" if task.done else "Enter 标记完成")
    meta_parts.append("Ctrl+Enter 显示便签")
    meta_parts.append(f"Shift+Enter 打开 {rel}")
    return winshun.item(
        f"task-{task.id}",
        prefix + task.plain_text,
        " · ".join(meta_parts),
        icon="ph:check-square" if task.done else "ph:square",
        copy=task.plain_text,
        score=70 if not task.done else 50,
        data={"kind": "toggle-task", "task_id": task.id, "line_index": task.line_index, "text": task.plain_text},
        actions=[
            winshun.action("toggle-task", "取消完成" if task.done else "标记完成", "Enter"),
            winshun.action("show-sticky", "显示桌面便签", "Ctrl+Enter"),
            winshun.action("open-file", f"打开 {rel}", "Shift+Enter"),
        ],
    )


def _add_task_item(text: str, rel: str, add_ts: bool) -> dict:
    preview = markdown_store.format_new_task_line(text, add_timestamp_source=add_ts)
    return winshun.item(
        "add-" + _stable_id(rel, text),
        f"添加待办：{text}",
        f"写入 {rel} · Enter 添加 · Ctrl+Enter 添加并显示便签 · Shift+Enter 打开 {rel}",
        icon="ph:plus-circle",
        preview=preview,
        copy=text,
        score=92,
        data={"kind": "add-task", "text": text},
        actions=[
            winshun.action("add-task", "添加到 Tasks.md", "Enter"),
            winshun.action("add-task-show", "添加并显示便签", "Ctrl+Enter"),
            winshun.action("open-file", f"打开 {rel}", "Shift+Enter"),
        ],
    )


def _countdown_active_item(snap: Dict[str, Any]) -> dict:
    status = snap["status"]
    label = snap["label"] or "倒计时"
    clock = snap["clock"]
    if status == "paused":
        title = f"{label} · {clock}（已暂停）"
        enter_title = "继续倒计时"
    elif status == "finished":
        title = f"{label} · 00:00（时间到）"
        enter_title = "重新开始"
    else:
        title = f"{label} · {clock}"
        enter_title = "暂停倒计时"
    return winshun.item(
        "cd-active",
        title,
        f"Enter {enter_title} · Ctrl+Enter 重置 · Shift+Enter 结束并关闭磁贴",
        icon="ph:timer",
        copy=clock,
        score=100,
        data={"kind": "cd-toggle"},
        actions=[
            winshun.action("cd-toggle", enter_title, "Enter"),
            winshun.action("cd-reset", "重置倒计时", "Ctrl+Enter"),
            winshun.action("cd-stop", "结束并关闭磁贴", "Shift+Enter"),
        ],
    )


def _countdown_preset_items() -> List[dict]:
    rows = []
    for idx, mins in enumerate(COUNTDOWN_PRESETS):
        secs = mins * 60
        clock = countdown_tile.format_clock(secs)
        rows.append(
            winshun.item(
                f"cd-preset-{mins}",
                f"倒计时 {mins} 分钟",
                f"回车启动 {clock} 桌面倒计时磁贴",
                icon="ph:timer",
                copy=clock,
                score=70 - idx,
                data={"kind": "cd-start", "seconds": secs, "label": ""},
                actions=[winshun.action("cd-start", f"开始 {mins} 分钟倒计时", "Enter")],
            )
        )
    return rows


def _query_countdown(q: winshun.Query, plugin: winshun.Plugin) -> Any:
    """Read-only handler for the minimalist countdown command."""
    if app.is_cancelled(q.query_id):
        return []

    text = str(q.text or "").strip()
    snap = app.countdown_snapshot()
    is_active = snap["status"] in ("running", "paused", "finished")
    rerun = 1 if snap["status"] == "running" else 0

    if not text:
        items: List[dict] = []
        if is_active:
            items.append(_countdown_active_item(snap))
        else:
            items.append(
                winshun.item(
                    "cd-hint",
                    "输入分钟数开始倒计时（如：25 或 40 政治）",
                    "支持纯数字分钟、小数分钟、90s / 1.5h / 10:30；也可直接回车选择下方快捷时长",
                    icon="ph:timer",
                    copy="倒计时 25",
                    valid=False,
                    score=95,
                )
            )
        items.extend(_countdown_preset_items())
        return {"items": items, "rerun": rerun} if rerun else items

    parsed, err = countdown_tile.parse_countdown_input(text)
    items = []
    if parsed is not None:
        clock = countdown_tile.format_clock(parsed.seconds)
        suffix = f" · {parsed.label}" if parsed.label else ""
        items.append(
            winshun.item(
                "cd-start-" + _stable_id(str(parsed.seconds), parsed.label),
                f"开始倒计时：{parsed.display_duration}{suffix}",
                f"回车启动 {clock} 桌面倒计时磁贴",
                icon="ph:timer",
                copy=clock,
                score=100,
                data={"kind": "cd-start", "seconds": parsed.seconds, "label": parsed.label},
                actions=[winshun.action("cd-start", "开始倒计时", "Enter")],
            )
        )
        if is_active:
            items.append(_countdown_active_item(snap))
        return {"items": items, "rerun": rerun} if rerun else items

    items.append(
        winshun.item(
            "cd-error-" + _stable_id(err),
            err,
            "示例：倒计时 25 · 倒计时 40 政治 · 倒计时 90s",
            icon="ph:warning",
            copy=text,
            valid=False,
            score=100,
        )
    )
    if is_active:
        items.append(_countdown_active_item(snap))
    items.extend(_countdown_preset_items())
    return {"items": items, "rerun": rerun} if rerun else items


def on_query(q: winshun.Query, plugin: winshun.Plugin) -> Any:
    """Read-only query handler: never mutates files, clipboard, timers, or external apps."""
    if app.is_cancelled(q.query_id):
        return []
    if q.command == "countdown":
        return _query_countdown(q, plugin)

    text = str(q.text or "").strip()
    snap = app.load_snapshot(force=False)
    rel = app.todo_file_rel()

    if not snap["vault_configured"]:
        return [
            winshun.item(
                "setup-pick-vault",
                "尚未配置 Obsidian Vault · 打开便签选择目录",
                "Enter 打开桌面便签，可直接选择与 888bik.obsidian-quicknote 相同的 Vault",
                icon="ph:folder-open",
                score=100,
                data={"kind": "show-sticky"},
                actions=[winshun.action("show-sticky", "打开便签并选择 Vault", "Enter")],
            ),
            winshun.item(
                "setup-open-settings",
                "打开 Win顺 插件设置",
                "在「Obsidian 待办便签」设置中配置 Vault 路径与待办文件",
                icon="ph:gear",
                url="winshun:settings",
                score=90,
            ),
        ]

    if snap["error"]:
        return [
            winshun.item(
                "error-" + _stable_id(snap["error"]),
                f"无法读取待办文件：{snap['error']}",
                "请检查 Obsidian Vault 路径与相对待办路径设置",
                icon="ph:warning",
                copy=snap["error"],
                valid=False,
                score=100,
            ),
            winshun.item(
                "error-open-settings",
                "打开 Win顺 插件设置",
                "修正 Vault 目录或待办文件路径",
                icon="ph:gear",
                url="winshun:settings",
                score=90,
            ),
        ]

    tasks: List[markdown_store.TaskItem] = snap["tasks"]
    active_count = sum(1 for t in tasks if not t.done)
    total_count = len(tasks)
    vault_label = app.vault_display_name()
    target_path = snap["target_path"] or ""
    exists = bool(snap["file_exists"])

    # Empty input: show sticky toggle + active tasks first + open Tasks.md
    if not text:
        items = [_sticky_toggle_item(vault_label, active_count, total_count)]
        ordered = [t for t in tasks if not t.done] + [t for t in tasks if t.done]
        for task in ordered[:MAX_LAUNCHER_TASKS]:
            items.append(_task_row_item(task, rel))
        if not tasks:
            items.append(
                winshun.item(
                    "empty-tasks",
                    "当前没有待办事项" if exists else f"{rel} 尚未创建",
                    "继续输入待办内容并按 Enter 添加，或按上方第一项打开桌面便签",
                    icon="ph:check-circle",
                    valid=False,
                )
            )
        if target_path:
            items.append(_open_file_item(target_path, rel, exists))
        return items

    if app.is_cancelled(q.query_id):
        return []

    lowered = text.casefold()
    wants_toggle = lowered in SHOW_HIDE_WORDS
    matched = markdown_store.filter_tasks(tasks, mode="all", search=text)
    matched.sort(key=lambda t: (t.done, t.line_index))

    items: List[dict] = []
    if wants_toggle:
        items.append(_sticky_toggle_item(vault_label, active_count, total_count))

    items.append(_add_task_item(text, rel, app.add_timestamp_source()))

    for task in matched[:MAX_LAUNCHER_TASKS]:
        items.append(_task_row_item(task, rel))

    if not wants_toggle:
        items.append(_sticky_toggle_item(vault_label, active_count, total_count))

    if target_path:
        items.append(_open_file_item(target_path, rel, exists))
    return items


def on_action(req: winshun.ActionRequest, plugin: winshun.Plugin) -> List[dict]:
    data = (req.item or {}).get("data") or {}
    action_id = str(req.action or "default")
    kind = str(data.get("kind") or "")
    chosen = action_id if action_id != "default" else kind

    if chosen == "cd-start":
        try:
            secs = int(data.get("seconds") or 0)
        except (TypeError, ValueError):
            secs = 0
        if secs < countdown_tile.MIN_COUNTDOWN_SECONDS or secs > countdown_tile.MAX_COUNTDOWN_SECONDS:
            return [winshun.notify("倒计时时长无效"), winshun.keep()]
        label = str(data.get("label") or "").strip()
        snap = app.start_countdown(secs, label)
        desc = f"{label} · " if label else ""
        return [winshun.notify(f"已开始倒计时：{desc}{snap['clock']}"), winshun.hide()]

    if chosen == "cd-toggle":
        snap = app.toggle_countdown_pause()
        msg = "倒计时已暂停" if snap["status"] == "paused" else f"倒计时继续：{snap['clock']}"
        return [winshun.notify(msg), winshun.hide()]

    if chosen == "cd-reset":
        snap = app.reset_countdown()
        return [winshun.notify(f"倒计时已重置：{snap['clock']}"), winshun.hide()]

    if chosen == "cd-stop":
        app.stop_countdown()
        return [winshun.notify("已结束倒计时"), winshun.hide()]

    if chosen == "open-file":
        snap = app.load_snapshot(force=False)
        if snap["error"]:
            return [winshun.notify(f"无法打开：{snap['error']}"), winshun.keep()]
        if not snap["file_exists"] or not snap["target_path"]:
            return [winshun.notify(f"{app.todo_file_rel()} 尚未创建，请先添加一条待办"), winshun.keep()]
        return [winshun.open_path(snap["target_path"]), winshun.hide()]

    if chosen == "toggle-sticky":
        now_visible = app.toggle_sticky(focus=True)
        return [winshun.notify("已显示 Obsidian 待办便签" if now_visible else "已隐藏 Obsidian 待办便签"), winshun.hide()]

    if chosen == "show-sticky":
        app.show_sticky(focus=True)
        return [winshun.hide()]

    if chosen == "focus-add":
        app.show_sticky(focus=True, focus_add=True)
        return [winshun.hide()]

    if chosen in ("add-task", "add-task-show"):
        text = str(data.get("text") or "").strip()
        if not text:
            return [winshun.notify("待办内容不能为空"), winshun.keep()]
        ok, err = app.add_task(text)
        if not ok:
            return [winshun.notify(f"添加失败：{err}"), winshun.keep()]
        sticky_window.refresh()
        if chosen == "add-task-show":
            app.show_sticky(focus=True)
        return [winshun.notify(f"已添加待办：{text[:50]}"), winshun.hide()]

    if chosen == "toggle-task":
        task_id = str(data.get("task_id") or "")
        line_idx = data.get("line_index")
        updated, err = app.toggle_task(task_id, expected_line=line_idx if isinstance(line_idx, int) else None)
        if updated is None:
            return [winshun.notify(err or "操作失败"), winshun.keep()]
        sticky_window.refresh()
        label = "已完成：" if updated.done else "已取消完成："
        return [winshun.notify(label + updated.text[:50]), winshun.hide()]

    app.show_sticky(focus=True)
    return [winshun.hide()]


def on_cancel(query_id: int, plugin: winshun.Plugin) -> None:
    app.cancel_query(query_id)


def on_tray_menu(plugin: winshun.Plugin) -> List[dict]:
    """Memory-only tray submenu handler (answers in <1 ms)."""
    visible = sticky_window.is_visible()
    st = app.get_state()
    hk = app.current_hotkey()
    hk_suffix = f"\t{hk}" if hk else ""
    items: List[dict] = [
        {"id": "toggle", "title": ("隐藏便签" if visible else "显示便签") + hk_suffix, "checked": visible},
        {"id": "new", "title": "新建待办"},
        {},
        {"id": "pin", "title": "窗口置顶", "checked": bool(st.get("pinned", True))},
        {"id": "hide_done", "title": "隐藏已完成", "checked": bool(st.get("hide_completed", False))},
        {"id": "collapse", "title": "折叠为标题栏", "checked": bool(st.get("collapsed", False))},
        {},
        {"id": "open_file", "title": f"打开 {app.todo_file_rel()}"},
        {"id": "reload", "title": "立即重新同步"},
    ]
    cd = app.countdown_snapshot()
    if cd["status"] != "idle":
        lbl = cd["label"] or "倒计时"
        action_txt = "继续" if cd["status"] == "paused" else ("重开" if cd["status"] == "finished" else "暂停")
        items.extend([
            {},
            {"id": "cd_toggle", "title": f"{lbl} {cd['clock']}（{action_txt}）"},
            {"id": "cd_stop", "title": "结束倒计时"},
        ])
    return items


def on_menu(menu_id: str, plugin: winshun.Plugin) -> None:
    if menu_id == "toggle":
        app.toggle_sticky(focus=True)
    elif menu_id == "new":
        app.show_sticky(focus=True, focus_add=True)
    elif menu_id == "pin":
        new_val = app.set_pinned(not app.get_state().get("pinned", True), sync_host=True)
        sticky_window.set_pinned(new_val)
    elif menu_id == "hide_done":
        new_val = app.set_hide_completed(not app.get_state().get("hide_completed", False), sync_host=True)
        sticky_window.set_hide_completed(new_val)
    elif menu_id == "collapse":
        new_val = not bool(app.get_state().get("collapsed", False))
        app.update_state(collapsed=new_val)
        sticky_window.set_collapsed(new_val)
    elif menu_id == "open_file":
        ok, msg = app.open_tasks_file()
        if not ok and msg:
            plugin.notify_user(msg)
    elif menu_id == "reload":
        sticky_window.refresh()
        plugin.notify_user("已重新同步 Obsidian 待办")
    elif menu_id == "cd_toggle":
        app.toggle_countdown_pause()
    elif menu_id == "cd_stop":
        app.stop_countdown()


def on_hotkey(key: str, plugin: winshun.Plugin) -> None:
    app.handle_hotkey(key)


def on_settings(settings: Dict[str, Any], plugin: winshun.Plugin) -> None:
    app.apply_settings(settings)


def on_appearance(appearance: Dict[str, Any], plugin: winshun.Plugin) -> None:
    app.apply_appearance(appearance, str(getattr(plugin, "theme", "light") or "light"))


def on_initialized(plugin: winshun.Plugin) -> None:
    app.initialize(plugin)


def main() -> int:
    ui_theme.set_process_dpi_awareness()
    try:
        return winshun.serve(
            on_query,
            on_action,
            on_settings=on_settings,
            on_cancel=on_cancel,
            name=PLUGIN_ID,
            on_tray_menu=on_tray_menu,
            on_menu=on_menu,
            on_hotkey=on_hotkey,
            on_initialized=on_initialized,
            on_appearance=on_appearance,
        )
    finally:
        sticky_window.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
