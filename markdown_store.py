# -*- coding: utf-8 -*-
"""Lossless Markdown task parser and bidirectional sync store for Obsidian Tasks.md.

Compatible with 888bik.obsidian-quicknote's todo line format:
    - [ ] [2026-11-24 20:35] Task text <!-- source: Win顺 -->
as well as standard Obsidian / GFM Markdown task lines with headings and **bold** spans:
    # 今日TODO
    ## 🎯 核心任务（数学一 & 专业课）
    - [ ] **高数：** 中值定理相关题目
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_TODO_FILE = "Tasks.md"
MAX_TODO_FILE_BYTES = 20 * 1024 * 1024  # 20 MiB safety guard

_TASK_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<bullet>[-*+])\s+\[(?P<mark>[ xX])\]\s+(?P<body>.*)$"
)
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")
_CREATED_RE = re.compile(
    r"^\[(?P<created>\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)\]\s+"
)
_SOURCE_RE = re.compile(r"\s*(?P<source><!--\s*source:[^<>]*-->)\s*$")
_INLINE_TOKEN_RE = re.compile(
    r"(\*\*[^*\n]+?\*\*|__[^_\n]+?__|`[^`\n]+?`|(?<!\S)#[^\s#`.,;:!?()（）\[\]【】]+)"
)
_PREFIX_COLON_RE = re.compile(
    r"^(?P<prefix>[^：:\n]{1,16}(?:（[^）\n]{1,16}）|\([^)\n]{1,16}\))?[：:])(?P<rest>.*)$"
)


class StoreError(Exception):
    """Expected user-facing error (invalid path, missing vault, oversized file)."""


@dataclass(frozen=True)
class TaskItem:
    id: str
    line_index: int
    done: bool
    text: str
    created: str
    source_comment: str
    section: str
    indent: str
    bullet: str
    raw_line: str
    section_level: int = 0

    @property
    def indent_level(self) -> int:
        spaces = len(self.indent.replace("\t", "    "))
        return min(3, spaces // 2)

    @property
    def plain_text(self) -> str:
        """Task text stripped of Markdown bold/code markers for launcher display/copy."""
        return "".join(seg for _kind, seg in parse_inline_spans(self.text, auto_bold_prefix=False))

    @property
    def short_created(self) -> str:
        """Compact timestamp for UI display (e.g. '11-24 20:35')."""
        if not self.created:
            return ""
        m = re.match(r"^\d{4}-(\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?", self.created)
        if m:
            date_part, time_part = m.group(1), m.group(2)
            return f"{date_part} {time_part}" if time_part else date_part
        return self.created


def now_local() -> datetime:
    return datetime.now().astimezone()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("\ufeff", "")).strip().casefold()


def parse_inline_spans(text: str, *, auto_bold_prefix: bool = True) -> List[Tuple[str, str]]:
    """Split a Markdown task body into styled spans: ('bold'|'code'|'tag'|'normal', str).

    Supports explicit **bold** / __bold__, `code`, and #tags.
    When no explicit bold markers exist and auto_bold_prefix=True, gently bolds a short
    leading subject prefix such as '高数：中值定理' or '英语一（上限 70 min）：'.
    """
    raw = str(text or "")
    if not raw:
        return []

    spans: List[Tuple[str, str]] = []
    has_explicit_bold = False
    pos = 0
    for match in _INLINE_TOKEN_RE.finditer(raw):
        if match.start() > pos:
            spans.append(("normal", raw[pos:match.start()]))
        tok = match.group(0)
        if (tok.startswith("**") and tok.endswith("**")) or (tok.startswith("__") and tok.endswith("__")):
            inner = tok[2:-2]
            if inner:
                spans.append(("bold", inner))
                has_explicit_bold = True
        elif tok.startswith("`") and tok.endswith("`"):
            inner = tok[1:-1]
            if inner:
                spans.append(("code", inner))
        elif tok.startswith("#"):
            spans.append(("tag", tok))
        else:
            spans.append(("normal", tok))
        pos = match.end()
    if pos < len(raw):
        spans.append(("normal", raw[pos:]))

    if auto_bold_prefix and not has_explicit_bold and spans and spans[0][0] == "normal":
        first = spans[0][1]
        # Avoid bolding URLs like https://... or mailto:...
        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", first) and not first.lower().startswith("mailto:"):
            pm = _PREFIX_COLON_RE.match(first)
            if pm and not pm.group("rest").startswith("//"):
                prefix = pm.group("prefix")
                rest = pm.group("rest")
                # Keep colon and optional parenthetical nicely styled:
                # e.g. "英语一（上限 70 min）：" -> bold "英语一", normal "（上限 70 min）："
                paren = re.match(r"^(?P<head>[^（(：:]+)(?P<tail>[（(][^）)]+[）)][：:])$", prefix)
                replacement: List[Tuple[str, str]] = []
                if paren:
                    replacement.append(("bold", paren.group("head")))
                    replacement.append(("normal", paren.group("tail")))
                else:
                    replacement.append(("bold", prefix))
                if rest:
                    replacement.append(("normal", rest))
                spans = replacement + spans[1:]
    return spans


def safe_relative_path(raw: str) -> Path:
    """Reject empty, absolute, traversal, or illegal-character relative paths."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise StoreError(f"待办文件路径必须是 Vault 内的相对路径：{raw}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise StoreError(f"待办文件路径不允许为空或包含 ..：{raw}")
    if any("\x00" in part or any(ch in part for ch in '<>:"|?*') for part in parts):
        raise StoreError("待办文件路径包含 Windows 不允许的字符")
    return Path(*parts)


def resolve_vault_root(raw_vault: str) -> Path:
    cleaned = str(raw_vault or "").strip().strip('"')
    if not cleaned:
        raise StoreError("尚未配置 Obsidian Vault 路径")
    expanded = os.path.expandvars(os.path.expanduser(cleaned))
    vault = Path(expanded).resolve()
    if not vault.exists() or not vault.is_dir():
        raise StoreError(f"Obsidian Vault 目录不存在：{cleaned}")
    return vault


def resolve_todo_path(raw_vault: str, relative_todo: str = DEFAULT_TODO_FILE) -> Tuple[Path, Path, str]:
    """Return (vault_root, target_path, normalized_posix_rel) with traversal guard."""
    vault = resolve_vault_root(raw_vault)
    rel = safe_relative_path(relative_todo)
    target = (vault / rel).resolve(strict=False)
    try:
        target.relative_to(vault)
    except ValueError:
        raise StoreError("目标路径超出 Obsidian Vault 目录，已阻止访问")
    cur = vault
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            try:
                cur.resolve().relative_to(vault)
            except (OSError, ValueError):
                raise StoreError("检测到指向 Vault 外的链接，已阻止访问")
    return vault, target, rel.as_posix()


def _detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _make_task_id(norm_text: str, occurrence: int) -> str:
    raw = f"{norm_text}|{occurrence}".encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()[:12]


def parse_document(content: str) -> Tuple[str, List[TaskItem]]:
    """Parse (document_h1_title, tasks) while ignoring YAML frontmatter and fenced code."""
    clean = content.lstrip("\ufeff")
    lines = clean.splitlines()
    tasks: List[TaskItem] = []
    seen_counts: Dict[str, int] = {}

    in_frontmatter = False
    in_fence = False
    fence_char = ""
    fence_len = 0
    doc_title = ""
    current_section = ""
    current_section_level = 0

    for idx, line in enumerate(lines):
        if idx == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() in ("---", "..."):
                in_frontmatter = False
            continue

        fence_match = _FENCE_RE.match(line)
        if fence_match:
            token = fence_match.group("fence")
            if not in_fence:
                in_fence = True
                fence_char = token[0]
                fence_len = len(token)
                continue
            if token[0] == fence_char and len(token) >= fence_len and line.strip() == token:
                in_fence = False
                continue
        if in_fence:
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group("hashes"))
            title = heading_match.group("title").strip()
            if level == 1 and not doc_title:
                doc_title = title
            current_section = title
            current_section_level = level
            continue

        task_match = _TASK_RE.match(line)
        if not task_match:
            continue

        indent = task_match.group("indent")
        bullet = task_match.group("bullet")
        done = task_match.group("mark").lower() == "x"
        body = task_match.group("body")

        created = ""
        created_match = _CREATED_RE.match(body)
        if created_match:
            created = created_match.group("created")
            body = body[created_match.end():]

        source_comment = ""
        source_match = _SOURCE_RE.search(body)
        if source_match:
            source_comment = source_match.group("source")
            body = body[:source_match.start()]

        display_text = body.strip()
        if not display_text:
            continue

        norm = normalize_text(display_text)
        occ = seen_counts.get(norm, 0)
        seen_counts[norm] = occ + 1
        task_id = _make_task_id(norm, occ)

        tasks.append(
            TaskItem(
                id=task_id,
                line_index=idx,
                done=done,
                text=display_text,
                created=created,
                source_comment=source_comment,
                section=current_section,
                indent=indent,
                bullet=bullet,
                raw_line=line,
                section_level=current_section_level,
            )
        )
    return doc_title, tasks


def parse_tasks(content: str) -> List[TaskItem]:
    """Parse Markdown task checkboxes while ignoring YAML frontmatter and fenced code."""
    _title, tasks = parse_document(content)
    return tasks


def format_new_task_line(
    text: str,
    *,
    stamp: Optional[datetime] = None,
    add_timestamp_source: bool = True,
    done: bool = False,
) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        raise StoreError("待办内容不能为空")
    mark = "x" if done else " "
    if add_timestamp_source:
        ts = stamp or now_local()
        return f"- [{mark}] [{ts:%Y-%m-%d %H:%M}] {cleaned} <!-- source: Win顺 -->"
    return f"- [{mark}] {cleaned}"


def _rebuild_task_line(task: TaskItem, *, done: Optional[bool] = None, new_text: Optional[str] = None) -> str:
    is_done = task.done if done is None else bool(done)
    mark = "x" if is_done else " "
    text = task.text if new_text is None else re.sub(r"\s+", " ", str(new_text)).strip()
    if not text:
        raise StoreError("待办内容不能为空")
    parts = [f"{task.indent}{task.bullet} [{mark}]"]
    if task.created:
        parts.append(f"[{task.created}]")
    parts.append(text)
    if task.source_comment:
        parts.append(task.source_comment)
    return " ".join(parts)


class MarkdownTodoStore:
    """Thread-safe, mtime-cached, lossless Markdown task repository."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cached_key: Optional[Tuple[str, int, int]] = None
        self._cached_title: str = ""
        self._cached_tasks: List[TaskItem] = []
        self._cached_exists: bool = False
        self._last_sync: str = ""

    def _read_raw_locked(self, target: Path) -> Tuple[str, bool, str]:
        if not target.exists():
            return "", False, "\n"
        if not target.is_file():
            raise StoreError("待办目标路径不是普通文件")
        size = target.stat().st_size
        if size > MAX_TODO_FILE_BYTES:
            raise StoreError("待办文件超过 20 MB，已停止读写")
        raw_bytes = target.read_bytes()
        has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")
        content = raw_bytes.decode("utf-8-sig", errors="replace")
        return content, has_bom, _detect_newline(content)

    def _write_raw_locked(self, target: Path, content: str, has_bom: bool) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        encoding = "utf-8-sig" if has_bom else "utf-8"
        with target.open("w", encoding=encoding, newline="") as handle:
            handle.write(content)

    def _signature(self, target: Path) -> Optional[Tuple[str, int, int]]:
        try:
            st = target.stat()
            return (os.path.normcase(str(target)), int(st.st_mtime_ns), int(st.st_size))
        except OSError:
            return None

    def load(
        self,
        raw_vault: str,
        relative_todo: str = DEFAULT_TODO_FILE,
        *,
        force: bool = False,
    ) -> Tuple[List[TaskItem], Optional[Path], bool, bool]:
        """Load tasks with mtime/size caching. Read-only: never creates files."""
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            sig = self._signature(target)
            if not force and sig == self._cached_key:
                return list(self._cached_tasks), target, self._cached_exists, False

            if sig is None:
                changed = (self._cached_key is not None) or bool(self._cached_tasks) or self._cached_exists
                self._cached_key = None
                self._cached_title = ""
                self._cached_tasks = []
                self._cached_exists = False
                self._last_sync = now_local().strftime("%H:%M:%S")
                return [], target, False, changed

            content, _has_bom, _nl = self._read_raw_locked(target)
            doc_title, tasks = parse_document(content)
            changed = (
                (tasks != self._cached_tasks)
                or (doc_title != self._cached_title)
                or (not self._cached_exists)
                or (self._cached_key != sig)
            )
            self._cached_key = sig
            self._cached_title = doc_title
            self._cached_tasks = tasks
            self._cached_exists = True
            self._last_sync = now_local().strftime("%H:%M:%S")
            return list(tasks), target, True, changed

    @property
    def doc_title(self) -> str:
        with self._lock:
            return self._cached_title

    @property
    def last_sync(self) -> str:
        with self._lock:
            return self._last_sync

    def add_task(
        self,
        raw_vault: str,
        relative_todo: str,
        text: str,
        *,
        add_timestamp_source: bool = True,
        stamp: Optional[datetime] = None,
    ) -> Tuple[TaskItem, Path]:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            raise StoreError("待办内容不能为空")
        new_line = format_new_task_line(
            cleaned, stamp=stamp, add_timestamp_source=add_timestamp_source, done=False
        )
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            if not target.exists():
                content = f"# 待办事项\n\n{new_line}\n"
                self._write_raw_locked(target, content, has_bom=False)
            else:
                content, has_bom, nl = self._read_raw_locked(target)
                if content and not content.endswith(("\n", "\r")):
                    updated = content + nl + new_line + nl
                elif not content:
                    updated = f"# 待办事项{nl}{nl}{new_line}{nl}"
                else:
                    updated = content + new_line + nl
                self._write_raw_locked(target, updated, has_bom=has_bom)

            tasks, _t, _exists, _changed = self.load(raw_vault, relative_todo, force=True)
            for task in reversed(tasks):
                if normalize_text(task.text) == normalize_text(cleaned):
                    return task, target
            return tasks[-1], target

    def _find_task(self, tasks: List[TaskItem], task_id: str, expected_line: Optional[int] = None) -> Optional[TaskItem]:
        if expected_line is not None:
            for t in tasks:
                if t.line_index == expected_line and t.id == task_id:
                    return t
        for t in tasks:
            if t.id == task_id:
                return t
        return None

    def toggle_task(
        self,
        raw_vault: str,
        relative_todo: str,
        task_id: str,
        *,
        expected_line: Optional[int] = None,
    ) -> Optional[TaskItem]:
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            if not target.exists():
                return None
            content, has_bom, nl = self._read_raw_locked(target)
            tasks = parse_tasks(content)
            found = self._find_task(tasks, task_id, expected_line=expected_line)
            if found is None:
                return None

            trailing_nl = content.endswith(("\n", "\r"))
            lines = content.splitlines()
            lines[found.line_index] = _rebuild_task_line(found, done=not found.done)
            updated = nl.join(lines) + (nl if trailing_nl else "")
            self._write_raw_locked(target, updated, has_bom=has_bom)

            new_tasks, _t, _ex, _ch = self.load(raw_vault, relative_todo, force=True)
            return self._find_task(new_tasks, task_id, expected_line=found.line_index)

    def edit_task(
        self,
        raw_vault: str,
        relative_todo: str,
        task_id: str,
        new_text: str,
        *,
        expected_line: Optional[int] = None,
    ) -> Optional[TaskItem]:
        cleaned = re.sub(r"\s+", " ", str(new_text or "")).strip()
        if not cleaned:
            raise StoreError("待办内容不能为空")
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            if not target.exists():
                return None
            content, has_bom, nl = self._read_raw_locked(target)
            tasks = parse_tasks(content)
            found = self._find_task(tasks, task_id, expected_line=expected_line)
            if found is None:
                return None

            trailing_nl = content.endswith(("\n", "\r"))
            lines = content.splitlines()
            lines[found.line_index] = _rebuild_task_line(found, new_text=cleaned)
            updated = nl.join(lines) + (nl if trailing_nl else "")
            self._write_raw_locked(target, updated, has_bom=has_bom)

            new_tasks, _t, _ex, _ch = self.load(raw_vault, relative_todo, force=True)
            for t in new_tasks:
                if t.line_index == found.line_index:
                    return t
            return None

    def delete_task(
        self,
        raw_vault: str,
        relative_todo: str,
        task_id: str,
        *,
        expected_line: Optional[int] = None,
    ) -> Optional[Tuple[int, str, TaskItem]]:
        """Delete a single task line and return (line_index, raw_line, task) for undo."""
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            if not target.exists():
                return None
            content, has_bom, nl = self._read_raw_locked(target)
            tasks = parse_tasks(content)
            found = self._find_task(tasks, task_id, expected_line=expected_line)
            if found is None:
                return None

            trailing_nl = content.endswith(("\n", "\r"))
            lines = content.splitlines()
            removed_line = lines.pop(found.line_index)
            updated = nl.join(lines) + (nl if trailing_nl and lines else "")
            self._write_raw_locked(target, updated, has_bom=has_bom)
            self.load(raw_vault, relative_todo, force=True)
            return found.line_index, removed_line, found

    def restore_line(
        self,
        raw_vault: str,
        relative_todo: str,
        line_index: int,
        raw_line: str,
    ) -> bool:
        """Undo a deletion by reinserting the exact original Markdown line."""
        with self._lock:
            _vault, target, _rel = resolve_todo_path(raw_vault, relative_todo)
            if not target.exists():
                content = f"# 待办事项\n\n{raw_line}\n"
                self._write_raw_locked(target, content, has_bom=False)
                self.load(raw_vault, relative_todo, force=True)
                return True
            content, has_bom, nl = self._read_raw_locked(target)
            trailing_nl = content.endswith(("\n", "\r")) or not content
            lines = content.splitlines()
            pos = max(0, min(int(line_index), len(lines)))
            lines.insert(pos, raw_line)
            updated = nl.join(lines) + (nl if trailing_nl else "")
            self._write_raw_locked(target, updated, has_bom=has_bom)
            self.load(raw_vault, relative_todo, force=True)
            return True


def filter_tasks(
    tasks: List[TaskItem],
    *,
    mode: str = "all",
    hide_completed: bool = False,
    search: str = "",
) -> List[TaskItem]:
    """Filter tasks by status mode ('active' | 'done' | 'all'), hide_completed, and keywords."""
    words = [w.casefold() for w in re.findall(r"[^\s]+", str(search or "").strip())]
    out: List[TaskItem] = []
    for task in tasks:
        if mode == "active" and task.done:
            continue
        if mode == "done" and not task.done:
            continue
        if mode == "all" and hide_completed and task.done:
            continue
        if words:
            haystack = f"{task.text} {task.plain_text} {task.section} {task.created}".casefold()
            if not all(w in haystack for w in words):
                continue
        out.append(task)
    return out
