#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Iterable


APP_DIR = Path.home() / ".config" / "multi-folder-dashboard"
SETTINGS_FILE = APP_DIR / "settings.json"
STATE_FILE = APP_DIR / "state.json"


@dataclass(frozen=True)
class ShortcutSpec:
    label: str
    command: str
    submit: bool = True
    cursor_left: int = 0


@dataclass(frozen=True)
class AppSettings:
    window_width: int = 1300
    window_height: int = 820
    terminal_font: str = "Monospace 11"
    terminal_fg: str = "#E8E3E3"
    terminal_bg: str = "#1C1C28"
    refresh_seconds: int = 3
    shortcuts: list[ShortcutSpec] = field(default_factory=list)


@dataclass(frozen=True)
class AppState:
    open_folders: list[str] = field(default_factory=list)


DEFAULT_SHORTCUTS = [
    ShortcutSpec("npm run dev", "npm run dev"),
    ShortcutSpec("npm install", "npm install"),
    ShortcutSpec("yarn dev", "yarn dev"),
    ShortcutSpec("git status", "git status"),
    ShortcutSpec("git add .", "git add ."),
    ShortcutSpec("desfazer add", "git restore --staged ."),
    ShortcutSpec('git commit -m ""', 'git commit -m ""', submit=False, cursor_left=1),
    ShortcutSpec("git push", "git push"),
    ShortcutSpec("ls -la", "ls -la"),
]


def ensure_app_dir() -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DIR


def _serialize_settings(settings: AppSettings) -> dict:
    data = asdict(settings)
    data["shortcuts"] = [asdict(shortcut) for shortcut in settings.shortcuts]
    return data


def _default_settings() -> AppSettings:
    return AppSettings(shortcuts=list(DEFAULT_SHORTCUTS))


def load_settings() -> AppSettings:
    ensure_app_dir()
    defaults = _default_settings()

    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(
            json.dumps(_serialize_settings(defaults), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return defaults

    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        shortcuts = [
            ShortcutSpec(
                label=item["label"],
                command=item["command"],
                submit=bool(item.get("submit", True)),
                cursor_left=int(item.get("cursor_left", 0)),
            )
            for item in raw.get("shortcuts", [])
            if item.get("label") and item.get("command")
        ]

        if not shortcuts:
            shortcuts = list(DEFAULT_SHORTCUTS)

        return AppSettings(
            window_width=int(raw.get("window_width", defaults.window_width)),
            window_height=int(raw.get("window_height", defaults.window_height)),
            terminal_font=str(raw.get("terminal_font", defaults.terminal_font)),
            terminal_fg=str(raw.get("terminal_fg", defaults.terminal_fg)),
            terminal_bg=str(raw.get("terminal_bg", defaults.terminal_bg)),
            refresh_seconds=max(1, int(raw.get("refresh_seconds", defaults.refresh_seconds))),
            shortcuts=shortcuts,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults


def load_state() -> AppState:
    ensure_app_dir()
    if not STATE_FILE.exists():
        return AppState()

    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        folders = [str(item) for item in raw.get("open_folders", []) if item]
        return AppState(open_folders=folders)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppState()


def save_state(folder_paths: Iterable[str]) -> None:
    ensure_app_dir()
    data = {"open_folders": list(folder_paths)}
    STATE_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
