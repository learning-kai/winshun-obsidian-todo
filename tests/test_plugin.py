# -*- coding: utf-8 -*-
"""Comprehensive automated tests for learning-kai.obsidian-todo."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR))

import countdown_tile
import main as plugin_main
import markdown_store
import state_store
import theme as ui_theme
import winshun


class TestMarkdownStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "Vault"
        self.vault.mkdir()
        self.store = markdown_store.MarkdownTodoStore()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_parse_quicknote_and_standard_tasks_ignoring_frontmatter_and_code(self) -> None:
        sample = (
            "---\n"
            "title: Tasks\n"
            "- [ ] not a task in frontmatter\n"
            "---\n\n"
            "# 待办事项\n\n"
            "- [ ] [2026-11-24 20:35] 中值定理相关题目 <!-- source: Win顺 -->\n"
            "- [x] [2026-11-24 20:35] 欧拉方程 <!-- source: Win顺 -->\n"
            "  - [ ] 子任务缩进\n"
            "* [X] 大写完成标记 #tag\n"
            "- [ ] \n"
            "```markdown\n"
            "- [ ] 代码块里的假待办\n"
            "```\n"
            "## 工作\n"
            "- [ ] 写周报 📅 2026-11-26\n"
        )
        tasks = markdown_store.parse_tasks(sample)
        self.assertEqual(len(tasks), 5)
        self.assertEqual(tasks[0].text, "中值定理相关题目")
        self.assertFalse(tasks[0].done)
        self.assertEqual(tasks[0].created, "2026-11-24 20:35")
        self.assertEqual(tasks[0].short_created, "11-24 20:35")
        self.assertEqual(tasks[0].source_comment, "<!-- source: Win顺 -->")
        self.assertEqual(tasks[0].section, "待办事项")

        self.assertTrue(tasks[1].done)
        self.assertEqual(tasks[2].indent_level, 1)
        self.assertTrue(tasks[3].done)
        self.assertEqual(tasks[4].section, "工作")

    def test_lossless_toggle_edit_delete_restore_and_crlf(self) -> None:
        tasks_file = self.vault / "Tasks.md"
        original = (
            "# 待办事项\r\n"
            "\r\n"
            "保留的说明段落。\r\n"
            "- [ ] [2026-11-24 20:35] 中值定理相关题目 <!-- source: Win顺 -->\r\n"
            "- [ ] 普通待办\r\n"
        )
        tasks_file.write_bytes(original.encode("utf-8"))

        tasks, _target, exists, _changed = self.store.load(str(self.vault), "Tasks.md")
        self.assertTrue(exists)
        self.assertEqual(len(tasks), 2)

        # Toggle first task
        toggled = self.store.toggle_task(str(self.vault), "Tasks.md", tasks[0].id, expected_line=tasks[0].line_index)
        self.assertIsNotNone(toggled)
        self.assertTrue(toggled.done)
        raw = tasks_file.read_bytes().decode("utf-8")
        self.assertIn("\r\n", raw)
        self.assertIn("保留的说明段落。\r\n", raw)
        self.assertIn("- [x] [2026-11-24 20:35] 中值定理相关题目 <!-- source: Win顺 -->\r\n", raw)

        # Inline edit preserves timestamp, checkbox, and source comment
        edited = self.store.edit_task(
            str(self.vault), "Tasks.md", toggled.id, "中值定理强化题", expected_line=toggled.line_index
        )
        self.assertIsNotNone(edited)
        raw2 = tasks_file.read_bytes().decode("utf-8")
        self.assertIn("- [x] [2026-11-24 20:35] 中值定理强化题 <!-- source: Win顺 -->\r\n", raw2)

        # Delete second task and restore via undo
        tasks_now, _, _, _ = self.store.load(str(self.vault), "Tasks.md", force=True)
        removed = self.store.delete_task(str(self.vault), "Tasks.md", tasks_now[1].id, expected_line=tasks_now[1].line_index)
        self.assertIsNotNone(removed)
        self.assertNotIn("普通待办", tasks_file.read_text(encoding="utf-8"))

        line_idx, raw_line, _task = removed
        self.store.restore_line(str(self.vault), "Tasks.md", line_idx, raw_line)
        self.assertEqual(tasks_file.read_bytes().decode("utf-8"), raw2)

    def test_add_task_creates_and_appends_safely(self) -> None:
        # Read-only load must NOT create the file
        tasks, target, exists, _ = self.store.load(str(self.vault), "Sub/Tasks.md")
        self.assertFalse(exists)
        self.assertEqual(tasks, [])
        self.assertFalse(target.exists())

        stamp = datetime(2026, 11, 24, 21, 0, tzinfo=timezone.utc)
        added, path = self.store.add_task(
            str(self.vault), "Sub/Tasks.md", "复习线代", add_timestamp_source=True, stamp=stamp
        )
        self.assertTrue(path.exists())
        self.assertEqual(added.text, "复习线代")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# 待办事项\n\n"))
        self.assertIn("- [ ] [2026-11-24 21:00] 复习线代 <!-- source: Win顺 -->\n", text)

        # Append without trailing newline in existing file and plain mode
        path.write_text("# 待办事项\n\n- [ ] 已有任务", encoding="utf-8")
        added2, _ = self.store.add_task(str(self.vault), "Sub/Tasks.md", "简洁任务", add_timestamp_source=False)
        self.assertEqual(added2.text, "简洁任务")
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "# 待办事项\n\n- [ ] 已有任务\n- [ ] 简洁任务\n",
        )

    def test_duplicate_task_texts_get_unique_stable_ids(self) -> None:
        f = self.vault / "Tasks.md"
        f.write_text("- [ ] 买牛奶\n- [ ] 买牛奶\n", encoding="utf-8")
        tasks, _, _, _ = self.store.load(str(self.vault), "Tasks.md")
        self.assertEqual(len(tasks), 2)
        self.assertNotEqual(tasks[0].id, tasks[1].id)

        self.store.toggle_task(str(self.vault), "Tasks.md", tasks[1].id, expected_line=tasks[1].line_index)
        self.assertEqual(f.read_text(encoding="utf-8"), "- [ ] 买牛奶\n- [x] 买牛奶\n")

    def test_security_and_invalid_inputs_rejected(self) -> None:
        for bad_rel in ("", "C:\\Tasks.md", "/etc/passwd", "../outside.md", "a/../../b.md", "bad:name.md"):
            with self.assertRaises(markdown_store.StoreError, msg=f"Should reject {bad_rel!r}"):
                markdown_store.resolve_todo_path(str(self.vault), bad_rel)

        with self.assertRaises(markdown_store.StoreError):
            markdown_store.resolve_todo_path("", "Tasks.md")
        with self.assertRaises(markdown_store.StoreError):
            markdown_store.resolve_todo_path(str(self.vault / "does-not-exist"), "Tasks.md")
        with self.assertRaises(markdown_store.StoreError):
            self.store.add_task(str(self.vault), "Tasks.md", "   ")

        # Directory at todo_file path is rejected
        (self.vault / "dir_as_file").mkdir()
        with self.assertRaises(markdown_store.StoreError):
            self.store.load(str(self.vault), "dir_as_file")

    def test_filter_tasks(self) -> None:
        sample = "- [ ] 高数 欧拉方程\n- [x] 高数 中值定理\n- [ ] 英语 单词\n"
        tasks = markdown_store.parse_tasks(sample)
        self.assertEqual(len(markdown_store.filter_tasks(tasks, mode="active")), 2)
        self.assertEqual(len(markdown_store.filter_tasks(tasks, mode="done")), 1)
        self.assertEqual(len(markdown_store.filter_tasks(tasks, mode="all", hide_completed=True)), 2)
        self.assertEqual(len(markdown_store.filter_tasks(tasks, mode="all", search="高数")), 2)
        self.assertEqual(len(markdown_store.filter_tasks(tasks, mode="active", search="高数 欧拉")), 1)

    def test_document_title_headings_and_inline_bold_spans(self) -> None:
        sample = (
            "# 今日TODO\n\n"
            "## 🎯 核心任务（数学一 & 专业课）\n"
            "- [ ] **高数：** 中值定理相关题目\n"
            "- [ ] 信号与系统：单双边频谱画图\n\n"
            "## ⏱ 公共课限时打卡\n"
            "- [ ] 英语一（上限 70 min）：\n"
            "- [ ] 查看 https://obsidian.md #资料 `Tasks.md`\n"
        )
        doc_title, tasks = markdown_store.parse_document(sample)
        self.assertEqual(doc_title, "今日TODO")
        self.assertEqual(len(tasks), 4)
        self.assertEqual(tasks[0].section, "🎯 核心任务（数学一 & 专业课）")
        self.assertEqual(tasks[0].section_level, 2)
        self.assertEqual(tasks[0].plain_text, "高数： 中值定理相关题目")

        spans0 = markdown_store.parse_inline_spans(tasks[0].text)
        self.assertEqual(spans0[0], ("bold", "高数："))

        spans1 = markdown_store.parse_inline_spans(tasks[1].text)
        self.assertEqual(spans1[0], ("bold", "信号与系统："))
        self.assertEqual(spans1[1], ("normal", "单双边频谱画图"))

        spans2 = markdown_store.parse_inline_spans(tasks[2].text)
        self.assertEqual(spans2[0], ("bold", "英语一"))
        self.assertEqual(spans2[1], ("normal", "（上限 70 min）："))

        spans3 = markdown_store.parse_inline_spans(tasks[3].text)
        kinds3 = [k for k, _ in spans3]
        self.assertIn("tag", kinds3)
        self.assertIn("code", kinds3)
        self.assertNotIn("bold", kinds3)


class TestStateAndTheme(unittest.TestCase):
    def test_state_roundtrip_and_corrupt_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            st = state_store.load_state(d)
            self.assertEqual(st["opacity"], 96)
            st["opacity"] = 80
            st["pinned"] = False
            st["window_x"] = 120
            st["window_y"] = 80
            st["style_preset"] = "winshun"
            self.assertTrue(state_store.save_state(d, st))
            reloaded = state_store.load_state(d)
            self.assertEqual(reloaded["opacity"], 80)
            self.assertFalse(reloaded["pinned"])
            self.assertEqual(reloaded["window_x"], 120)
            self.assertEqual(reloaded["style_preset"], "winshun")

            # Corrupt JSON falls back cleanly
            Path(d, state_store.STATE_FILENAME).write_text("{broken json", encoding="utf-8")
            recovered = state_store.load_state(d)
            self.assertEqual(recovered["opacity"], 96)

    def test_theme_huajian_and_native_look_argb_blending(self) -> None:
        hj_light, r1 = ui_theme.build_palette({}, theme="light", preset="huajian")
        self.assertEqual(r1, "light")
        self.assertEqual(hj_light["BG"], "#F5F4EE")

        pal, resolved = ui_theme.build_palette(
            {
                "nativeLook": {
                    "dark": True,
                    "background": "#FF1D1D20",
                    "layer": "#80FFFFFF",
                    "accent": "#FFA78BFA",
                }
            },
            theme="light",
            preset="winshun",
        )
        self.assertEqual(resolved, "dark")
        self.assertEqual(pal["BG"], "#1D1D20")
        self.assertEqual(len(pal["SURFACE"]), 7)
        self.assertTrue(pal["SURFACE"].startswith("#"))


class TestProtocolHandlers(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name) / "Vault"
        self.vault.mkdir()
        self.data_dir = Path(self.tmp.name) / "data"
        self.data_dir.mkdir()
        self.orig_app = plugin_main.app
        plugin_main.app = plugin_main.ObsidianTodoApp(str(self.data_dir))
        self.dummy_plugin = mock.MagicMock()
        self.dummy_plugin.data_dir = str(self.data_dir)
        self.dummy_plugin.capabilities = {"hostHotkeys": True}
        self.dummy_plugin.settings = {"vault": str(self.vault), "todo_file": "Tasks.md", "show_on_start": False}
        self.dummy_plugin.appearance = {"rememberWindowSize": True}
        self.dummy_plugin.theme = "light"

    def tearDown(self) -> None:
        plugin_main.app = self.orig_app
        self.tmp.cleanup()

    def test_unconfigured_vault_returns_setup_items(self) -> None:
        plugin_main.app.settings = {"vault": "", "todo_file": "Tasks.md"}
        items = plugin_main.on_query(winshun.Query(text="", command="otodo"), self.dummy_plugin)
        ids = [it["id"] for it in items]
        self.assertIn("setup-pick-vault", ids)
        self.assertIn("setup-open-settings", ids)

    def test_invalid_todo_path_returns_error_item(self) -> None:
        plugin_main.app.settings = {"vault": str(self.vault), "todo_file": "../escape.md"}
        items = plugin_main.on_query(winshun.Query(text="", command="otodo"), self.dummy_plugin)
        self.assertFalse(items[0].get("valid", True))
        self.assertEqual(items[1]["open"]["url"], "winshun:settings")

    def test_query_is_readonly_and_actions_order_valid(self) -> None:
        plugin_main.app.settings = {"vault": str(self.vault), "todo_file": "Tasks.md"}
        # Non-existent file: query must NOT create it
        items = plugin_main.on_query(winshun.Query(text="", command="otodo"), self.dummy_plugin)
        self.assertFalse((self.vault / "Tasks.md").exists())
        self.assertEqual(items[0]["id"], "sticky-toggle")
        keys = [a["key"] for a in items[0]["actions"]]
        self.assertEqual(keys, ["Enter", "Ctrl+Enter", "Shift+Enter"])

        # Add via action
        add_items = plugin_main.on_query(winshun.Query(text="新任务", command="otodo"), self.dummy_plugin)
        add_item = add_items[0]
        self.assertTrue(add_item["id"].startswith("add-"))
        self.assertEqual([a["key"] for a in add_item["actions"]], ["Enter", "Ctrl+Enter", "Shift+Enter"])

        effects = plugin_main.on_action(
            winshun.ActionRequest(action="add-task", item=add_item, command="otodo", text="新任务"),
            self.dummy_plugin,
        )
        self.assertTrue((self.vault / "Tasks.md").exists())
        self.assertIn("新任务", (self.vault / "Tasks.md").read_text(encoding="utf-8"))
        self.assertTrue(any(e.get("type") == "notify" for e in effects))

    def test_cancel_query_and_stale_hotkey_ignored(self) -> None:
        plugin_main.app.settings = {"vault": str(self.vault), "todo_file": "Tasks.md"}
        plugin_main.on_cancel(42, self.dummy_plugin)
        self.assertEqual(plugin_main.on_query(winshun.Query(text="abc", query_id=42), self.dummy_plugin), [])

        plugin_main.app._registered_hotkey = "Ctrl+Alt+B"
        self.assertFalse(plugin_main.app.handle_hotkey("Ctrl+Alt+T"))

    def test_remember_window_size_false_resets_saved_dimensions(self) -> None:
        plugin_main.app.save_window_geometry(100, 100, 520, 640)
        self.assertEqual(plugin_main.app.get_state()["window_width"], 520)
        plugin_main.app.apply_appearance({"rememberWindowSize": False}, "dark")
        self.assertIsNone(plugin_main.app.get_state()["window_width"])
        self.assertIsNone(plugin_main.app.get_state()["window_height"])


class TestJsonRpcSubprocessRoundTrip(unittest.TestCase):
    """Spawn main.py as a real WinShun resident process and speak JSON-RPC 2.0 over stdio."""

    def test_full_resident_protocol_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Vault"
            vault.mkdir()
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            (vault / "Tasks.md").write_text(
                "# 待办事项\n\n- [ ] [2026-11-24 20:35] 欧拉方程 <!-- source: Win顺 -->\n",
                encoding="utf-8",
            )

            env = dict(os.environ)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["WS_DATA_DIR"] = str(data_dir)
            proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", str(PLUGIN_DIR / "main.py")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

            def send(obj: dict) -> None:
                line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
                proc.stdin.write(line)
                proc.stdin.flush()

            def recv_response(expected_id: int) -> dict:
                while True:
                    raw = proc.stdout.readline()
                    self.assertTrue(raw, "Unexpected EOF from plugin stdout")
                    msg = json.loads(raw.decode("utf-8"))
                    # Respond if plugin sends winshun/tool (e.g. register_hotkeys)
                    if msg.get("method") == "winshun/tool" and "id" in msg:
                        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"errors": {}}})
                        continue
                    if "method" in msg and "id" not in msg:
                        # Notification such as winshun/hotkeys; valid JSON-RPC
                        continue
                    if msg.get("id") == expected_id:
                        return msg

            try:
                # 1. initialize
                send({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "1",
                        "winshun": "0.44.0",
                        "pluginDir": str(PLUGIN_DIR),
                        "dataDir": str(data_dir),
                        "locale": "zh",
                        "theme": "dark",
                        "appearance": {"theme": "dark", "rememberWindowSize": True},
                        "capabilities": {"hostHotkeys": True},
                        "settings": {
                            "vault": str(vault),
                            "todo_file": "Tasks.md",
                            "show_on_start": False,
                            "hotkey": "Ctrl+Alt+B",
                        },
                    },
                })
                init_resp = recv_response(1)
                self.assertEqual(init_resp["result"]["protocolVersion"], "1")
                self.assertEqual(init_resp["result"]["name"], "learning-kai.obsidian-todo")

                # 2. empty query -> returns sticky toggle + existing task + open file
                send({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "query",
                    "params": {"queryId": 10, "command": "otodo", "trigger": "keyword", "keyword": "otodo", "text": "", "raw": "otodo "},
                })
                q_resp = recv_response(2)
                items = q_resp["result"]["items"]
                self.assertGreaterEqual(len(items), 3)
                task_item = next(it for it in items if it["id"].startswith("task-"))
                self.assertIn("欧拉方程", task_item["title"])

                # 3. action toggle-task -> updates Tasks.md on disk
                send({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "action",
                    "params": {"command": "otodo", "action": "toggle-task", "item": task_item, "text": ""},
                })
                act_resp = recv_response(3)
                self.assertIn("effects", act_resp["result"])
                self.assertIn("- [x] [2026-11-24 20:35] 欧拉方程 <!-- source: Win顺 -->", (vault / "Tasks.md").read_text(encoding="utf-8"))

                # 4. trayMenu
                send({"jsonrpc": "2.0", "id": 4, "method": "trayMenu", "params": {}})
                tray_resp = recv_response(4)
                self.assertGreaterEqual(len(tray_resp["result"]["items"]), 5)

                # 5. shutdown
                send({"jsonrpc": "2.0", "id": 5, "method": "shutdown"})
                shut_resp = recv_response(5)
                self.assertEqual(shut_resp["result"], {})
            finally:
                proc.stdin.close()
                rc = proc.wait(timeout=5)
                stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
                proc.stdout.close()
                proc.stderr.close()

            self.assertEqual(rc, 0, f"Plugin exited with {rc}, stderr={stderr_out!r}")


class TestManifestStructure(unittest.TestCase):
    def test_plugin_json_valid_and_assets_exist(self) -> None:
        manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest"], 1)
        self.assertEqual(manifest["id"], plugin_main.PLUGIN_ID)
        self.assertIn(".", manifest["id"].strip("."))
        self.assertTrue(manifest["$schema"].startswith("https://"))
        self.assertEqual(manifest["runtime"]["mode"], "resident")
        self.assertEqual(manifest["runtime"]["start"], "eager")
        self.assertIn("ui", manifest["permissions"])
        self.assertIn("files:read", manifest["permissions"])
        self.assertIn("files:write", manifest["permissions"])
        self.assertIn("winshun:open", manifest["permissions"])
        self.assertNotIn("processes", manifest["permissions"])
        self.assertNotIn("elevated", manifest["permissions"])
        self.assertTrue((PLUGIN_DIR / manifest["icon"]).is_file())
        self.assertTrue((PLUGIN_DIR / "icon.dark.png").is_file())
        self.assertTrue((PLUGIN_DIR / "icon.ico").is_file())
        self.assertTrue((PLUGIN_DIR / "README.md").is_file())
        self.assertTrue((PLUGIN_DIR / "LICENSE").is_file())


class TestStickyWindowSmoke(unittest.TestCase):
    """Exercise StickyNoteWindow with a real withdrawn Tk root to catch widget/theme bugs."""

    def test_sticky_window_lifecycle_and_interactions(self) -> None:
        import queue
        import sticky_window

        sticky_window._load_tk()
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Vault"
            vault.mkdir()
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            tasks_file = vault / "Tasks.md"
            tasks_file.write_text(
                "# 今日TODO\n\n"
                "## 🎯 核心任务（数学一 & 专业课）\n"
                "- [ ] [2026-11-24 20:35] **高数：** 中值定理相关题目 <!-- source: Win顺 -->\n"
                "- [ ] 信号与系统：单双边频谱画图\n\n"
                "## ⏱ 公共课限时打卡\n"
                "- [ ] 英语一（上限 70 min）：\n",
                encoding="utf-8",
            )
            controller = plugin_main.ObsidianTodoApp(str(data_dir))
            controller.settings = {"vault": str(vault), "todo_file": "Tasks.md", "add_timestamp_source": True}

            ui_theme.set_process_dpi_awareness()
            root = sticky_window.tk.Tk()
            root.withdraw()
            dpi = ui_theme.apply_dpi_scaling(root)
            try:
                cmd_q: queue.Queue = queue.Queue()
                win = sticky_window.StickyNoteWindow(
                    root, cmd_q, controller, appearance={}, theme_name="light", dpi_scale=dpi
                )
                win.reload_tasks(force=True)
                root.update_idletasks()
                self.assertEqual(win._doc_title, "今日TODO")
                self.assertEqual(len(win._tasks), 3)

                # 1. Verify '×' close_btn stays strictly to the RIGHT of hover tools_frame!
                win._set_toolbar_visible(True)
                root.update()
                self.assertGreaterEqual(
                    win.close_btn.winfo_x(),
                    win.tools_frame.winfo_x() + win.tools_frame.winfo_width() - 2,
                    "close_btn must stay anchored on the far right of tools_frame",
                )

                # 2. Verify single-line task Text has exact height == 1 (no giant vertical gap!)
                text_widgets = []

                def collect_texts(widget):
                    for ch in widget.winfo_children():
                        if isinstance(ch, sticky_window.tk.Text):
                            text_widgets.append(ch)
                        collect_texts(ch)

                collect_texts(win.rows_frame)
                self.assertEqual(len(text_widgets), 3)
                for tw in text_widgets:
                    win._fit_text_widget(tw)
                    self.assertEqual(int(tw.cget("height")), 1, "Short task should occupy exactly 1 line")

                # 3. Verify popover auto-sizes cleanly with ZERO clipped children at HiDPI!
                win._toggle_options_popover()
                self.assertIsNotNone(win._popover)
                win._popover.update_idletasks()
                pop_w = win._popover.winfo_width()
                pop_h = win._popover.winfo_height()
                req_w = win._popover.winfo_reqwidth()
                req_h = win._popover.winfo_reqheight()
                self.assertGreaterEqual(pop_w, req_w, f"Popover width {pop_w} < requested {req_w}")
                self.assertGreaterEqual(pop_h, req_h, f"Popover height {pop_h} < requested {req_h}")
                win._close_popover()
                self.assertIsNone(win._popover)

                # 4. Open quiet drawers & add via UI entry
                win._toggle_add_drawer(True)
                win._on_add_focus_in()
                win.add_var.set("政治：肖八选择题")
                win._on_add_submit()
                self.assertEqual(len(win._tasks), 4)
                self.assertIn("政治：肖八选择题", tasks_file.read_text(encoding="utf-8"))

                # 5. Toggle, flat inline edit (no ugly solid black box!), delete & undo
                first = win._tasks[0]
                win._on_toggle_task(first)
                self.assertIn("- [x] [2026-11-24 20:35] **高数：** 中值定理相关题目", tasks_file.read_text(encoding="utf-8"))

                second = win._tasks[1]
                win._start_inline_edit(second)
                self.assertIsNotNone(win._editing_entry)
                self.assertEqual(str(win._editing_entry.cget("relief")), "flat")
                self.assertEqual(int(win._editing_entry.cget("bd")), 0)
                win._commit_inline_edit(second, "信号与系统：傅里叶变换")
                self.assertIn("信号与系统：傅里叶变换", tasks_file.read_text(encoding="utf-8"))

                target_del = win._tasks[3]
                win._on_delete_task(target_del)
                self.assertNotIn("政治：肖八选择题", tasks_file.read_text(encoding="utf-8"))
                win._undo_delete()
                self.assertIn("政治：肖八选择题", tasks_file.read_text(encoding="utf-8"))

                # 6. Verify 8-direction resizing computes and applies exact geometry strings
                class _FakeEv:
                    def __init__(self, xr: int, yr: int) -> None:
                        self.x_root = xr
                        self.y_root = yr

                applied_geoms = []
                real_geom = root.geometry

                def spy_geom(*args):
                    if args:
                        applied_geoms.append(args[0])
                    return real_geom(*args)

                root.geometry = spy_geom
                try:
                    w0, h0, x0, y0 = 800, 800, 200, 100
                    win._resize_origin = (x0 + w0, y0 + h0, x0, y0, w0, h0, "se")
                    win._do_edge_resize(_FakeEv(x0 + w0 + 90, y0 + h0 + 70))
                    win._end_edge_resize(_FakeEv(x0 + w0 + 90, y0 + h0 + 70))
                    self.assertEqual(applied_geoms[-1], "890x870+200+100")

                    win._resize_origin = (200, 150, 200, 100, 890, 870, "w")
                    win._do_edge_resize(_FakeEv(160, 150))
                    win._end_edge_resize(_FakeEv(160, 150))
                    self.assertEqual(applied_geoms[-1], "930x870+160+100")
                finally:
                    root.geometry = real_geom

                # 7. Filter drawer, theme switch, collapse/expand, setup card, error state
                win._toggle_filter_drawer(True)
                win.palette, win.theme_name = ui_theme.build_palette(
                    {"nativeLook": {"dark": True, "background": "#FF1E1F22", "layer": "#FF26282C"}},
                    "dark",
                    preset="huajian",
                )
                win._apply_theme_to_widgets()
                win._set_filter_mode("all")
                win._toggle_hide_completed()
                win._set_filter_mode("done")
                win._toggle_collapse()
                win._toggle_collapse()

                controller.settings = {"vault": "", "todo_file": "Tasks.md"}
                win.reload_tasks(force=True)
                self.assertFalse(win._vault_configured)

                controller.settings = {"vault": str(vault), "todo_file": "../bad.md"}
                win.reload_tasks(force=True)
                self.assertTrue(bool(win._error_message))
            finally:
                root.destroy()


class TestCountdownFeature(unittest.TestCase):
    def test_parse_countdown_inputs_normal_and_invalid(self) -> None:
        # Pure minutes (default)
        p, err = countdown_tile.parse_countdown_input("25")
        self.assertEqual(err, "")
        self.assertEqual(p.seconds, 1500)
        self.assertEqual(p.label, "")

        # Minutes + label
        p, err = countdown_tile.parse_countdown_input("40 政治")
        self.assertEqual(err, "")
        self.assertEqual(p.seconds, 2400)
        self.assertEqual(p.label, "政治")

        # Space-separated unit + label
        p, err = countdown_tile.parse_countdown_input("70 min 英语一")
        self.assertEqual(err, "")
        self.assertEqual(p.seconds, 4200)
        self.assertEqual(p.label, "英语一")

        # Fractional hours, seconds, mm:ss
        p, _ = countdown_tile.parse_countdown_input("1.5h")
        self.assertEqual(p.seconds, 5400)
        p, _ = countdown_tile.parse_countdown_input("90s")
        self.assertEqual(p.seconds, 90)
        p, _ = countdown_tile.parse_countdown_input("10:30")
        self.assertEqual(p.seconds, 630)

        # Invalid / out-of-range inputs
        for bad in ("0", "-5", "abc", "1500", "25h"):
            p, err = countdown_tile.parse_countdown_input(bad)
            self.assertIsNone(p, f"Should reject {bad!r}")
            self.assertTrue(bool(err))

    def test_countdown_state_lifecycle_and_finish_event(self) -> None:
        st = countdown_tile.CountdownState()
        st.start(10, "测试")
        self.assertEqual(st.status, "running")
        self.assertTrue(st.pause())
        self.assertEqual(st.status, "paused")
        self.assertTrue(st.resume())
        self.assertEqual(st.status, "running")

        # Force expiry cleanly
        st._end_mono = 0.0
        self.assertEqual(st.remaining(), 0.0)
        self.assertEqual(st.status, "finished")
        self.assertTrue(st.consume_finish_event())
        self.assertFalse(st.consume_finish_event())  # fires exactly once

        self.assertTrue(st.reset())
        self.assertEqual(st.status, "running")
        st.stop()
        self.assertEqual(st.status, "idle")

    def test_countdown_query_is_readonly_and_actions_work(self) -> None:
        orig_app = plugin_main.app
        with tempfile.TemporaryDirectory() as tmp:
            plugin_main.app = plugin_main.ObsidianTodoApp(tmp)
            dummy_plugin = mock.MagicMock()
            try:
                # Empty query returns hint + presets, keeps state idle
                items = plugin_main.on_query(winshun.Query(text="", command="countdown"), dummy_plugin)
                self.assertEqual(items[0]["id"], "cd-hint")
                self.assertEqual(plugin_main.app.countdown.status, "idle")

                # Normal query is read-only (does not start timer)
                q_items = plugin_main.on_query(winshun.Query(text="25 刷题", command="countdown"), dummy_plugin)
                self.assertTrue(q_items[0]["id"].startswith("cd-start-"))
                self.assertIn("25 分钟", q_items[0]["title"])
                self.assertIn("刷题", q_items[0]["title"])
                self.assertEqual(plugin_main.app.countdown.status, "idle")

                # Invalid query returns clear error + presets
                bad_items = plugin_main.on_query(winshun.Query(text="hello", command="countdown"), dummy_plugin)
                self.assertFalse(bad_items[0].get("valid", True))

                # Action starts countdown (mock Tk tile spawn so no window pops up)
                with mock.patch("sticky_window.show_countdown") as m_show, \
                     mock.patch("sticky_window.sync_countdown") as m_sync, \
                     mock.patch("sticky_window.hide_countdown") as m_hide:
                    effects = plugin_main.on_action(
                        winshun.ActionRequest(action="cd-start", item=q_items[0], command="countdown", text="25 刷题"),
                        dummy_plugin,
                    )
                    self.assertTrue(any(e.get("type") == "notify" for e in effects))
                    self.assertEqual(plugin_main.app.countdown.status, "running")
                    m_show.assert_called_once()

                    # Live empty query now returns rerun=1 and active item with Enter/Ctrl+Enter/Shift+Enter
                    live = plugin_main.on_query(winshun.Query(text="", command="countdown"), dummy_plugin)
                    self.assertIsInstance(live, dict)
                    self.assertEqual(live["rerun"], 1)
                    active_item = live["items"][0]
                    self.assertEqual(active_item["id"], "cd-active")
                    self.assertEqual([a["key"] for a in active_item["actions"]], ["Enter", "Ctrl+Enter", "Shift+Enter"])

                    # Pause & stop via action
                    plugin_main.on_action(
                        winshun.ActionRequest(action="cd-toggle", item=active_item, command="countdown"),
                        dummy_plugin,
                    )
                    self.assertEqual(plugin_main.app.countdown.status, "paused")
                    m_sync.assert_called_once()

                    plugin_main.on_action(
                        winshun.ActionRequest(action="cd-stop", item=active_item, command="countdown"),
                        dummy_plugin,
                    )
                    self.assertEqual(plugin_main.app.countdown.status, "idle")
                    m_hide.assert_called_once()
            finally:
                plugin_main.app = orig_app

    def test_countdown_tile_square_and_circle_morph_and_sdf_renderers(self) -> None:
        import queue
        import checkbox_art
        import sticky_window

        # Verify SDF art renderers produce valid PNG headers
        pill = checkbox_art.render_pill_progress_png(
            width=120, height=8, progress=0.6, track_color="#E7E5DC", fill_color="#6E7F6B"
        )
        dial = checkbox_art.render_circular_dial_png(
            size=160, progress=0.75, bg_color="#F5F4EE", border_color="#E2E0D6",
            track_color="#E7E5DC", arc_color="#6E7F6B", stroke_w=8.0, rim_w=2.0,
        )
        self.assertTrue(pill.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(dial.startswith(b"\x89PNG\r\n\x1a\n"))

        sticky_window._load_tk()
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = plugin_main.ObsidianTodoApp(tmp)
            ctrl.settings = {"countdown_sound": False}
            ctrl.countdown.start(600, "数学")
            root = sticky_window.tk.Tk()
            root.withdraw()
            try:
                win = sticky_window.StickyNoteWindow(root, queue.Queue(), ctrl, dpi_scale=1.0)
                cd = win._ensure_countdown_tile()
                cd._ensure_window()
                cd.sync_view()
                self.assertEqual(cd.shape, "square")

                # Morph square -> circle -> pill -> square cleanly + wheel ±1m adjust
                cd._on_toggle_shape()
                self.assertEqual(cd.shape, "circle")
                self.assertEqual(ctrl.get_state()["countdown_shape"], "circle")
                cd._hovered = True
                cd.render_now()

                cd._on_toggle_shape()
                self.assertEqual(cd.shape, "pill")
                self.assertEqual(ctrl.get_state()["countdown_shape"], "pill")
                cd.render_now()

                cd._on_toggle_shape()
                self.assertEqual(cd.shape, "square")
                self.assertEqual(ctrl.get_state()["countdown_shape"], "square")

                before = ctrl.countdown.remaining()
                self.assertTrue(ctrl.adjust_countdown_minutes(1))
                self.assertGreater(ctrl.countdown.remaining(), before + 55.0)
                self.assertTrue(ctrl.adjust_countdown_minutes(-1))
                cd.hide()
                cd._surface.destroy()
            finally:
                root.destroy()

    def test_bundled_gentle_chime_wav_and_async_playback(self) -> None:
        import struct
        import wave

        chime_path = Path(countdown_tile.CHIME_WAV_PATH)
        self.assertTrue(chime_path.is_file(), f"Missing bundled chime asset: {chime_path}")
        with wave.open(str(chime_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 2)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 44100)
            n_frames = wf.getnframes()
            self.assertGreater(n_frames, 44100)  # > 1s warm chime
            raw = wf.readframes(n_frames)

        # Verify soft peak amplitude (gentle, non-clipping) and silent tail (zero end click)
        samples = struct.unpack(f"<{len(raw) // 2}h", raw)
        peak = max(abs(v) for v in samples) / 32767.0
        self.assertGreater(peak, 0.30)
        self.assertLess(peak, 0.70)
        tail_peak = max(abs(v) for v in samples[-200:]) / 32767.0
        self.assertLess(tail_peak, 0.01)

        # Verify play_finish_chime uses SND_FILENAME | SND_ASYNC | SND_NODEFAULT on bundled WAV
        if os.name == "nt":
            import winsound

            with mock.patch.object(winsound, "PlaySound") as m_play:
                self.assertTrue(countdown_tile.play_finish_chime())
                m_play.assert_called_once_with(
                    str(chime_path),
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )


if __name__ == "__main__":
    unittest.main()
