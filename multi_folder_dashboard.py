#!/usr/bin/env python3
from __future__ import annotations

"""
Gerenciador de terminais com dashboard por pasta.

Melhorias principais:
- UI separada da logica de sistema e configuracao
- compatibilidade GTK3/GTK4 sem monkey patch global
- atalhos configuraveis via ~/.config/multi-folder-dashboard/settings.json
- persistencia de pastas abertas
- monitor de portas com filtro, refresh leve e confirmacao mais segura
"""

import os
import sys
import time
from pathlib import Path
from typing import Callable

from app_config import (
    AppSettings,
    AppState,
    FolderLauncherSpec,
    ShortcutSpec,
    load_settings,
    load_state,
    save_settings,
    save_state,
)
from gtk_compat import (
    Gdk,
    Gio,
    GLib,
    Gtk,
    Pango,
    Vte,
    USING_GTK4,
    add_css_class,
    apply_css,
    box_append,
    button_set_icon_name,
    get_root,
    listbox_append,
    paned_set_end_child,
    paned_set_start_child,
    present,
    set_child,
    set_label_wrap,
    set_application_identity,
    set_window_icon_from_file,
    minimize_window,
    show_all_if_needed,
)
from system_utils import (
    get_listening_ports,
    get_process_details,
    terminate_process_tree,
)


CSS = b"""
.sidebar {
    border-right: 1px solid rgba(130, 130, 130, 0.55);
    background-color: #2b2d31;
}
.sidebar-title {
    font-size: 0.75em;
    font-weight: bold;
    letter-spacing: 1px;
    opacity: 0.5;
    padding: 14px 12px 6px 12px;
}
.folder-row-box {
    padding: 8px 12px;
}
.folder-row-name {
    font-weight: bold;
    font-size: 0.95em;
}
.folder-row-shortcut {
    opacity: 0.72;
    font-size: 0.78em;
}
.folder-row-path {
    font-size: 0.78em;
    opacity: 0.55;
}
.terminal-topbar {
    padding: 8px 12px;
    border-bottom: 1px solid rgba(130, 130, 130, 0.35);
    background-color: rgba(48, 52, 60, 0.35);
}
.terminal-topbar-title {
    font-weight: bold;
}
.shortcut-bar {
    border-top: 1px solid rgba(130, 130, 130, 0.45);
    background-color: rgba(48, 52, 60, 0.65);
    padding: 4px 10px;
    min-height: 36px;
}
.section-title {
    font-weight: bold;
    font-size: 0.9em;
}
.monitor-header {
    padding: 10px 14px 6px 14px;
    border-bottom: 1px solid rgba(130, 130, 130, 0.40);
}
.helper-label {
    opacity: 0.7;
}
.empty-state {
    font-size: 1.15em;
    font-weight: bold;
}
"""

PROGRAM_NAME = "multi-folder-dashboard"
APPLICATION_NAME = "Multi Folder Dashboard"
APPLICATION_ID = "io.github.rafael.MultiFolderDashboard"
DESKTOP_FILE_NAME = f"{APPLICATION_ID}.desktop"
DIRECTORY_CONTENT_TYPE = "inode/directory"


def resolve_icon_path() -> str:
    bundled_base = getattr(sys, "_MEIPASS", "")
    candidate_paths = [
        Path(bundled_base) / "icon.png" if bundled_base else None,
        Path(__file__).resolve().parent / "icon.png",
        Path("/usr/share/icons/hicolor/1024x1024/apps/io.github.rafael.MultiFolderDashboard.png"),
        Path("/usr/share/pixmaps/io.github.rafael.MultiFolderDashboard.png"),
        Path("/home/rafael/Downloads/icon.png"),
    ]
    for candidate in candidate_paths:
        if candidate and candidate.exists():
            return str(candidate)
    return ""


def make_button(label: str, on_click=None, css_classes: list[str] | None = None) -> Gtk.Button:
    button = Gtk.Button(label=label)
    for css_class in css_classes or []:
        add_css_class(button, css_class)
    if on_click is not None:
        button.connect("clicked", on_click)
    return button


def make_label(
    text: str,
    css_classes: list[str] | None = None,
    *,
    halign=Gtk.Align.START,
    hexpand: bool = False,
) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_halign(halign)
    label.set_hexpand(hexpand)
    for css_class in css_classes or []:
        add_css_class(label, css_class)
    return label


def make_scrolled_window(child, *, hexpand: bool = True, vexpand: bool = True) -> Gtk.ScrolledWindow:
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_hexpand(hexpand)
    scrolled.set_vexpand(vexpand)
    set_child(scrolled, child)
    return scrolled


def show_message_dialog(
    parent,
    title: str,
    message: str,
    *,
    details: str = "",
    message_type=Gtk.MessageType.INFO,
) -> None:
    dialog = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=message_type,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dialog.format_secondary_text(f"{message}\n\n{details}".strip())
    dialog.connect("response", lambda d, _r: d.destroy())
    present(dialog)


def truncate_text(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3].rstrip() + "..."


def get_app_info_name(app_info: Gio.AppInfo) -> str:
    try:
        name = app_info.get_display_name()
    except AttributeError:
        name = ""
    return name or app_info.get_name() or "Aplicativo"


def build_folder_launcher_spec(app_info: Gio.AppInfo) -> FolderLauncherSpec:
    return FolderLauncherSpec(
        app_id=app_info.get_id() or "",
        app_name=get_app_info_name(app_info),
        app_exec=app_info.get_executable() or "",
    )


def list_directory_app_infos() -> list[Gio.AppInfo]:
    apps = Gio.AppInfo.get_all_for_type(DIRECTORY_CONTENT_TYPE) or []
    deduped: list[Gio.AppInfo] = []
    seen: set[tuple[str, str, str]] = set()

    for app_info in apps:
        if hasattr(app_info, "should_show") and not app_info.should_show():
            continue

        key = (
            app_info.get_id() or "",
            app_info.get_executable() or "",
            get_app_info_name(app_info),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(app_info)

    deduped.sort(key=lambda item: get_app_info_name(item).lower())
    return deduped


class ShortcutEditorRow(Gtk.ListBoxRow):
    def __init__(self, shortcut: ShortcutSpec, on_remove: Callable[["ShortcutEditorRow"], None]):
        super().__init__()
        if hasattr(self, "set_activatable"):
            self.set_activatable(False)
        if hasattr(self, "set_selectable"):
            self.set_selectable(False)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        container.set_margin_top(8)
        container.set_margin_bottom(8)
        container.set_margin_start(10)
        container.set_margin_end(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = make_label("Atalho", ["section-title"], hexpand=True)
        box_append(header, title)

        remove_button = make_button("Remover", lambda _btn: on_remove(self), ["flat"])
        remove_button.set_focus_on_click(False)
        box_append(header, remove_button)
        box_append(container, header)

        grid = Gtk.Grid()
        grid.set_column_spacing(10)
        grid.set_row_spacing(8)
        grid.set_hexpand(True)

        grid.attach(make_label("Label"), 0, 0, 1, 1)
        self.label_entry = Gtk.Entry()
        self.label_entry.set_hexpand(True)
        self.label_entry.set_text(shortcut.label)
        grid.attach(self.label_entry, 1, 0, 1, 1)

        grid.attach(make_label("Comando"), 0, 1, 1, 1)
        self.command_entry = Gtk.Entry()
        self.command_entry.set_hexpand(True)
        self.command_entry.set_text(shortcut.command)
        grid.attach(self.command_entry, 1, 1, 1, 1)

        self.submit_check = Gtk.CheckButton(label="Enviar Enter automaticamente")
        self.submit_check.set_active(shortcut.submit)
        grid.attach(self.submit_check, 0, 2, 2, 1)

        cursor_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box_append(cursor_box, make_label("Mover cursor"))
        adjustment = Gtk.Adjustment(
            value=max(0, shortcut.cursor_left),
            lower=0,
            upper=120,
            step_increment=1,
            page_increment=5,
            page_size=0,
        )
        self.cursor_spin = Gtk.SpinButton(adjustment=adjustment, digits=0)
        self.cursor_spin.set_numeric(True)
        self.cursor_spin.set_width_chars(3)
        box_append(cursor_box, self.cursor_spin)
        box_append(cursor_box, make_label("casas para a esquerda"))
        grid.attach(cursor_box, 0, 3, 2, 1)

        box_append(container, grid)
        set_child(self, container)


class ShortcutEditorDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        settings: AppSettings,
        on_save: Callable[[AppSettings], None],
    ):
        super().__init__(transient_for=parent, modal=True, title="Gerenciar atalhos")
        self._settings = settings
        self._on_save = on_save
        self._rows: list[ShortcutEditorRow] = []

        self.set_default_size(760, 460)

        self.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        save_button = self.add_button("Salvar", Gtk.ResponseType.OK)
        add_css_class(save_button, "suggested-action")

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        intro = make_label(
            "Edite os atalhos que aparecem abaixo do terminal. "
            "Use 'Mover cursor' para comandos como git commit -m \"\".",
            ["helper-label"],
        )
        set_label_wrap(intro, True)
        box_append(content, intro)

        self.error_label = make_label("", ["helper-label"])
        set_label_wrap(self.error_label, True)
        self.error_label.set_visible(False)
        box_append(content, self.error_label)

        add_button = make_button("Adicionar atalho", lambda _btn: self._add_row(), ["flat"])
        add_button.set_focus_on_click(False)
        box_append(content, add_button)

        self.shortcut_list = Gtk.ListBox()
        self.shortcut_list.set_selection_mode(Gtk.SelectionMode.NONE)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        set_child(scrolled, self.shortcut_list)
        box_append(content, scrolled)

        for shortcut in settings.shortcuts:
            self._add_row(shortcut)

        self.connect("response", self._on_response)

    def _add_row(self, shortcut: ShortcutSpec | None = None) -> None:
        row = ShortcutEditorRow(
            shortcut or ShortcutSpec("", "", submit=True, cursor_left=0),
            self._remove_row,
        )
        self._rows.append(row)
        listbox_append(self.shortcut_list, row)
        self._set_error("")
        show_all_if_needed(row)
        show_all_if_needed(self.shortcut_list)

    def _remove_row(self, row: ShortcutEditorRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
        self.shortcut_list.remove(row)
        self._set_error("")

    def _set_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def _collect_shortcuts(self) -> list[ShortcutSpec] | None:
        shortcuts: list[ShortcutSpec] = []
        for index, row in enumerate(self._rows, start=1):
            label = row.label_entry.get_text().strip()
            command = row.command_entry.get_text().strip()
            if not label or not command:
                self._set_error(
                    f"Preencha label e comando em todos os atalhos antes de salvar. Linha {index}."
                )
                return None

            shortcuts.append(
                ShortcutSpec(
                    label=label,
                    command=command,
                    submit=row.submit_check.get_active(),
                    cursor_left=max(0, row.cursor_spin.get_value_as_int()),
                )
            )

        self._set_error("")
        return shortcuts

    def _on_response(self, dialog, response) -> None:
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return

        shortcuts = self._collect_shortcuts()
        if shortcuts is None:
            return

        updated_settings = AppSettings(
            window_width=self._settings.window_width,
            window_height=self._settings.window_height,
            terminal_font=self._settings.terminal_font,
            terminal_fg=self._settings.terminal_fg,
            terminal_bg=self._settings.terminal_bg,
            refresh_seconds=self._settings.refresh_seconds,
            shortcuts=shortcuts,
        )
        self._on_save(updated_settings)
        dialog.destroy()


class DirectoryAppChooserDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        folder_path: Path,
        on_open: Callable[[Gio.AppInfo], None],
    ):
        super().__init__(
            transient_for=parent,
            modal=True,
            title=f"Open With - {folder_path.name}",
        )
        self._on_open = on_open
        self._apps = list_directory_app_infos()

        self.set_default_size(560, 420)

        self.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        self._open_button = self.add_button("Abrir", Gtk.ResponseType.OK)
        add_css_class(self._open_button, "suggested-action")

        content = self.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        intro = make_label(
            f"Escolha um aplicativo para abrir a pasta {folder_path}.",
            ["helper-label"],
        )
        set_label_wrap(intro, True)
        box_append(content, intro)

        self.error_label = make_label("", ["helper-label"])
        set_label_wrap(self.error_label, True)
        self.error_label.set_visible(False)
        box_append(content, self.error_label)

        self.app_list = Gtk.ListBox()
        self.app_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.app_list.connect("row-selected", self._on_row_selected)
        self.app_list.connect("row-activated", lambda *_args: self.response(Gtk.ResponseType.OK))

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        set_child(scrolled, self.app_list)
        box_append(content, scrolled)

        for app_info in self._apps:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box_append(box, make_label(get_app_info_name(app_info), ["section-title"]))

            executable = app_info.get_executable() or app_info.get_commandline() or ""
            if executable:
                exec_label = make_label(executable, ["helper-label"])
                set_label_wrap(exec_label, True)
                box_append(box, exec_label)

            set_child(row, box)
            row.app_info = app_info
            listbox_append(self.app_list, row)

        if not self._apps:
            self._set_error("Nenhum aplicativo compativel com pastas foi encontrado.")
            self._open_button.set_sensitive(False)
        else:
            first_row = self.app_list.get_row_at_index(0)
            if first_row is not None:
                self.app_list.select_row(first_row)

        self.connect("response", self._on_response)

    def _set_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(bool(message))

    def _on_row_selected(self, _listbox, row) -> None:
        self._open_button.set_sensitive(row is not None)
        if row is not None:
            self._set_error("")

    def _on_response(self, dialog, response) -> None:
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return

        row = self.app_list.get_selected_row()
        if row is None:
            self._set_error("Selecione um aplicativo antes de continuar.")
            return

        self._on_open(row.app_info)
        dialog.destroy()


class FolderTerminalPanel(Gtk.Box):
    def __init__(self, folder_path: Path, settings: AppSettings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.folder_path = folder_path
        self.settings = settings

        self.vte = Vte.Terminal()
        self.vte.set_scroll_on_output(True)
        self.vte.set_scroll_on_keystroke(True)
        self.vte.set_scrollback_lines(50_000)
        self.vte.set_font(Pango.FontDescription(settings.terminal_font))

        fg = Gdk.RGBA()
        fg.parse(settings.terminal_fg)
        bg = Gdk.RGBA()
        bg.parse(settings.terminal_bg)
        self.vte.set_colors(fg, bg, [])
        self.vte.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)

        box_append(self, make_scrolled_window(self.vte))
        self.shortcut_bar = self._build_shortcut_bar()
        box_append(self, self.shortcut_bar)

        self._spawn_shell()

    def _build_shortcut_bar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_css_class(bar, "shortcut-bar")

        for shortcut in self.settings.shortcuts:
            button = make_button(
                shortcut.label,
                lambda _btn, spec=shortcut: self._run_shortcut(spec),
                ["flat"],
            )
            box_append(bar, button)

        return bar

    def refresh_shortcut_bar(self, settings: AppSettings) -> None:
        self.settings = settings
        self.remove(self.shortcut_bar)
        self.shortcut_bar = self._build_shortcut_bar()
        box_append(self, self.shortcut_bar)
        show_all_if_needed(self.shortcut_bar)
        show_all_if_needed(self)

    def _run_shortcut(self, shortcut: ShortcutSpec) -> None:
        self.vte.grab_focus()
        payload = shortcut.command
        if shortcut.submit:
            payload += "\n"
        self.vte.feed_child(payload.encode())
        if shortcut.cursor_left:
            self.vte.feed_child(b"\x1b[D" * shortcut.cursor_left)

    def _spawn_shell(self) -> None:
        env_dict = dict(os.environ)
        env_dict.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        env_list = [f"{key}={value}" for key, value in env_dict.items()]
        argv = ["/bin/bash"]

        try:
            self._spawn_shell_async(argv, env_list)
        except Exception as exc:  # pragma: no cover - GUI branch
            GLib.idle_add(self._show_spawn_error, str(exc))

    def _spawn_shell_async(self, argv: list[str], env_list: list[str]) -> None:
        # VTE tem assinaturas diferentes entre versoes. Mantemos a compatibilidade
        # encapsulada aqui para nao espalhar esse detalhe pela UI.
        for extra_args_count in (2, 3, 1):
            try:
                extra = [None] * extra_args_count
                self.vte.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    str(self.folder_path),
                    argv,
                    env_list,
                    GLib.SpawnFlags.DEFAULT,
                    *extra,
                    -1,
                    None,
                    None,
                )
                return
            except TypeError:
                continue

        self.vte.spawn_sync(
            Vte.PtyFlags.DEFAULT,
            str(self.folder_path),
            argv,
            env_list,
            GLib.SpawnFlags.DEFAULT,
        )

    def _show_spawn_error(self, error_message: str) -> bool:
        parent = get_root(self)
        if parent is not None:
            show_message_dialog(
                parent,
                "Nao foi possivel abrir o terminal",
                f"A pasta {self.folder_path} nao conseguiu iniciar o bash.",
                details=error_message,
                message_type=Gtk.MessageType.ERROR,
            )
        return False


class MonitorTab(Gtk.Box):
    def __init__(self, settings: AppSettings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.settings = settings
        self.last_snapshot: tuple[tuple[str, int, int, str, str], ...] = tuple()
        self.last_refresh_at = 0.0

        self.set_hexpand(True)
        self.set_vexpand(True)

        box_append(self, self._build_header())
        box_append(self, self._build_ports_table())

        GLib.timeout_add(1000, self._tick_refresh)
        self._refresh_ports(force=True)

    def _build_header(self) -> Gtk.Box:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css_class(header, "monitor-header")

        title = make_label("Portas abertas (LISTEN)", ["section-title"], hexpand=True)
        box_append(header, title)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filtrar por host, porta, PID ou processo")
        self.search_entry.set_size_request(260, -1)
        self.search_entry.connect("search-changed", lambda _entry: self.port_filter.refilter())
        box_append(header, self.search_entry)

        interval_label = make_label("Refresh (s):", ["helper-label"])
        box_append(header, interval_label)

        adjustment = Gtk.Adjustment(
            value=self.settings.refresh_seconds,
            lower=1,
            upper=60,
            step_increment=1,
            page_increment=5,
            page_size=0,
        )
        self.interval_spin = Gtk.SpinButton(adjustment=adjustment, digits=0)
        self.interval_spin.set_numeric(True)
        self.interval_spin.set_width_chars(3)
        box_append(header, self.interval_spin)

        refresh_button = make_button("Atualizar", lambda _btn: self._refresh_ports(force=True), ["flat"])
        box_append(header, refresh_button)

        kill_button = make_button(
            "Encerrar processo da porta",
            self._on_kill_port_clicked,
            ["destructive-action"],
        )
        box_append(header, kill_button)

        return header

    def _build_ports_table(self) -> Gtk.ScrolledWindow:
        self.port_store = Gtk.ListStore(str, int, int, str, str)
        self.port_filter = self.port_store.filter_new()
        self.port_filter.set_visible_func(self._port_filter_func)

        self.port_tree = Gtk.TreeView(model=self.port_filter)
        self.port_tree.set_headers_visible(True)
        self.port_tree.set_activate_on_single_click(False)

        columns = [
            ("Host", 140, False),
            ("Porta", 80, False),
            ("PID", 70, False),
            ("Processo", 150, False),
            ("Comando", -1, True),
        ]
        for index, (title, min_width, expand) in enumerate(columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(expand)
            if min_width > 0:
                column.set_min_width(min_width)
            self.port_tree.append_column(column)

        return make_scrolled_window(self.port_tree)

    def _tick_refresh(self) -> bool:
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE

        refresh_seconds = max(1, self.interval_spin.get_value_as_int())
        if time.monotonic() - self.last_refresh_at < refresh_seconds:
            return GLib.SOURCE_CONTINUE

        return self._refresh_ports()

    def _port_filter_func(self, model, tree_iter, _data) -> bool:
        query = self.search_entry.get_text().strip().lower()
        if not query:
            return True

        values = [
            str(model.get_value(tree_iter, 0)),
            str(model.get_value(tree_iter, 1)),
            str(model.get_value(tree_iter, 2)),
            str(model.get_value(tree_iter, 3)),
            str(model.get_value(tree_iter, 4)),
        ]
        haystack = " ".join(values).lower()
        return query in haystack

    def _refresh_ports(self, force: bool = False) -> bool:
        ports = get_listening_ports()
        snapshot = tuple(
            (port.host, port.port, port.pid, port.process_name, port.command)
            for port in ports
        )
        self.last_refresh_at = time.monotonic()

        if not force and snapshot == self.last_snapshot:
            return GLib.SOURCE_CONTINUE

        self.last_snapshot = snapshot
        self.port_store.clear()
        for port in ports:
            self.port_store.append(
                [port.host, port.port, port.pid, port.process_name, port.command]
            )

        show_all_if_needed(self.port_tree)
        return GLib.SOURCE_CONTINUE

    def _on_kill_port_clicked(self, _btn) -> None:
        selection = self.port_tree.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            show_message_dialog(
                get_root(self),
                "Nenhuma porta selecionada",
                "Selecione uma linha antes de tentar encerrar um processo.",
            )
            return

        host = model.get_value(tree_iter, 0)
        port = model.get_value(tree_iter, 1)
        pid = model.get_value(tree_iter, 2)
        process_name = model.get_value(tree_iter, 3)
        command = model.get_value(tree_iter, 4)

        if not pid:
            show_message_dialog(
                get_root(self),
                "PID indisponivel",
                f"A porta {host}:{port} nao expoe um PID acessivel.",
                details="Isso pode acontecer por permissao insuficiente ou porque o processo ja terminou.",
            )
            return

        details = get_process_details(pid)
        detail_lines = [f"Comando: {details.command if details else command}"]
        if details is not None:
            detail_lines.append(f"CWD: {details.cwd}")
            detail_lines.append(f"Usuario: {details.username}")
            if details.is_root:
                detail_lines.append("Aviso: processo executando como root.")

        dialog = Gtk.MessageDialog(
            transient_for=get_root(self),
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Encerrar '{process_name}' em {host}:{port} (PID {pid})?",
        )
        dialog.format_secondary_text("\n".join(detail_lines))

        def on_response(dlg, response) -> None:
            dlg.destroy()
            if response != Gtk.ResponseType.YES:
                return

            success, message = terminate_process_tree(pid)
            if not success:
                show_message_dialog(
                    get_root(self),
                    "Falha ao encerrar processo",
                    message,
                    details="\n".join(detail_lines),
                    message_type=Gtk.MessageType.ERROR,
                )
            self._refresh_ports(force=True)

        dialog.connect("response", on_response)
        present(dialog)


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, settings: AppSettings):
        super().__init__(application=app)
        self.settings = settings
        self._restored_state = load_state()
        self._panels: dict[str, FolderTerminalPanel] = {}
        self._folder_launchers: dict[str, FolderLauncherSpec] = dict(
            self._restored_state.folder_launchers
        )
        self._sidebar_rows: dict[str, Gtk.ListBoxRow] = {}
        self._row_keys: dict[int, str] = {}
        self._current_folder_key: str | None = None
        self._allow_close = False

        self.set_title("Gerenciador de Terminais")
        self.set_default_size(settings.window_width, settings.window_height)
        set_window_icon_from_file(self, resolve_icon_path())

        apply_css(CSS)
        self._setup_keyboard_shortcuts()
        self._build_ui()
        self._setup_close_behavior()
        self._restore_open_folders()

    def _setup_keyboard_shortcuts(self) -> None:
        app = self.get_application()
        if app is None:
            return

        next_action = Gio.SimpleAction.new("terminal-next", None)
        next_action.connect("activate", lambda *_args: self._cycle_terminal_tab(1))
        self.add_action(next_action)
        app.set_accels_for_action("win.terminal-next", ["<Primary>Page_Down"])

        prev_action = Gio.SimpleAction.new("terminal-prev", None)
        prev_action.connect("activate", lambda *_args: self._cycle_terminal_tab(-1))
        self.add_action(prev_action)
        app.set_accels_for_action("win.terminal-prev", ["<Primary>Page_Up"])

        minimize_action = Gio.SimpleAction.new("minimize-app", None)
        minimize_action.connect("activate", lambda *_args: self._minimize_window())
        self.add_action(minimize_action)
        app.set_accels_for_action("win.minimize-app", ["<Alt>F4"])

        quit_action = Gio.SimpleAction.new("quit-app", None)
        quit_action.connect("activate", lambda *_args: self._request_real_close())
        self.add_action(quit_action)
        app.set_accels_for_action("win.quit-app", ["<Super>F4"])

        for index in range(1, 10):
            action_name = f"terminal-tab-{index}"
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda *_args, tab_index=index - 1: self._go_to_terminal_tab(tab_index))
            self.add_action(action)
            app.set_accels_for_action(f"win.{action_name}", [f"<Alt>{index}"])

    def _setup_close_behavior(self) -> None:
        if USING_GTK4:
            self.connect("close-request", self._on_close_request)
        else:
            self.connect("delete-event", self._on_delete_event)

    def _minimize_window(self) -> None:
        minimize_window(self)

    def _request_real_close(self) -> None:
        self._allow_close = True
        self.close()

    def _on_close_request(self, _window) -> bool:
        if self._allow_close:
            return False
        self._minimize_window()
        return True

    def _on_delete_event(self, _window, _event) -> bool:
        if self._allow_close:
            return False
        self._minimize_window()
        return True

    def _build_ui(self) -> None:
        header_bar = Gtk.HeaderBar()
        self.set_titlebar(header_bar)

        self.add_button = make_button("Adicionar pasta", self._on_add_folder, ["suggested-action"])
        header_bar.pack_start(self.add_button)

        self.main_stack = Gtk.Stack()
        self.main_stack.set_hexpand(True)
        self.main_stack.set_vexpand(True)
        self.main_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        terminals_workspace = self._build_terminals_workspace()
        monitor_tab = MonitorTab(self.settings)

        self.main_stack.add_titled(terminals_workspace, "terminals", "Terminais")
        self.main_stack.add_titled(monitor_tab, "ports", "Portas abertas")
        self.main_stack.set_visible_child_name("terminals")

        self.stack_switcher = Gtk.StackSwitcher()
        self.stack_switcher.set_stack(self.main_stack)
        header_bar.pack_start(self.stack_switcher)

        set_child(self, self.main_stack)
        self._update_terminal_workspace()
        self._refresh_terminal_toolbar()

    def _build_terminals_workspace(self):
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(240)

        sidebar = self._build_sidebar()
        paned_set_start_child(paned, sidebar)

        terminal_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        terminal_area.set_hexpand(True)
        terminal_area.set_vexpand(True)

        self.terminal_toolbar = self._build_terminal_toolbar()
        box_append(terminal_area, self.terminal_toolbar)

        self.terminal_stack = Gtk.Stack()
        self.terminal_stack.set_hexpand(True)
        self.terminal_stack.set_vexpand(True)

        self.notebook = Gtk.Notebook()
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.notebook.set_scrollable(True)
        self.notebook.set_hexpand(True)
        self.notebook.set_vexpand(True)
        self.notebook.connect("switch-page", self._on_notebook_switch_page)

        empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty_state.set_hexpand(True)
        empty_state.set_vexpand(True)
        empty_state.set_halign(Gtk.Align.CENTER)
        empty_state.set_valign(Gtk.Align.CENTER)

        empty_title = make_label("Nenhuma pasta aberta", ["empty-state"])
        empty_hint = make_label(
            "Use 'Adicionar pasta' para abrir um terminal isolado por projeto.",
            ["helper-label"],
        )
        box_append(empty_state, empty_title)
        box_append(empty_state, empty_hint)

        self.terminal_stack.add_named(empty_state, "empty")
        self.terminal_stack.add_named(self.notebook, "notebook")
        self.terminal_stack.set_visible_child_name("empty")

        box_append(terminal_area, self.terminal_stack)
        paned_set_end_child(paned, terminal_area)
        return paned

    def _build_terminal_toolbar(self) -> Gtk.Box:
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css_class(toolbar, "terminal-topbar")

        self.current_folder_label = make_label(
            "Nenhuma pasta selecionada",
            ["terminal-topbar-title"],
            hexpand=True,
        )
        self.current_folder_label.set_max_width_chars(40)
        self.current_folder_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box_append(toolbar, self.current_folder_label)

        self.current_open_with_button = make_button(
            "Open With",
            lambda _btn: self._open_with_current_folder(),
            ["flat"],
        )
        self.current_open_with_button.set_focus_on_click(False)
        box_append(toolbar, self.current_open_with_button)

        self.current_quick_open_button = make_button(
            "",
            lambda _btn: self._open_current_folder_with_saved_launcher(),
            ["flat"],
        )
        self.current_quick_open_button.set_focus_on_click(False)
        self.current_quick_open_button.set_visible(False)
        box_append(toolbar, self.current_quick_open_button)

        self.manage_shortcuts_button = make_button(
            "Gerenciar atalhos",
            lambda _btn: self._open_shortcut_manager(),
            ["flat"],
        )
        self.manage_shortcuts_button.set_focus_on_click(False)
        box_append(toolbar, self.manage_shortcuts_button)

        return toolbar

    def _build_sidebar(self) -> Gtk.Box:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        add_css_class(sidebar, "sidebar")
        sidebar.set_size_request(230, -1)

        title = make_label("PASTAS ABERTAS", ["sidebar-title"])
        box_append(sidebar, title)

        self.folder_listbox = Gtk.ListBox()
        self.folder_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        if hasattr(self.folder_listbox, "set_activate_on_single_click"):
            self.folder_listbox.set_activate_on_single_click(True)
        self.folder_listbox.connect("row-activated", self._on_sidebar_activated)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        set_child(scrolled, self.folder_listbox)

        box_append(sidebar, scrolled)
        return sidebar

    def _open_shortcut_manager(self) -> None:
        try:
            dialog = ShortcutEditorDialog(self, self.settings, self._apply_shortcut_settings)
            show_all_if_needed(dialog)
            present(dialog)
        except Exception as exc:  # pragma: no cover - erro de runtime da GUI
            show_message_dialog(
                self,
                "Nao foi possivel abrir o gerenciador de atalhos",
                "O dialogo de atalhos falhou ao abrir.",
                details=str(exc),
                message_type=Gtk.MessageType.ERROR,
            )

    def _apply_shortcut_settings(self, settings: AppSettings) -> None:
        self.settings = settings
        save_settings(settings)

        app = self.get_application()
        if isinstance(app, App):
            app.settings = settings

        for panel in self._panels.values():
            panel.refresh_shortcut_bar(settings)

    def _get_current_panel(self) -> FolderTerminalPanel | None:
        if not self._current_folder_key:
            return None
        panel = self._panels.get(self._current_folder_key)
        if isinstance(panel, FolderTerminalPanel):
            return panel
        return None

    def _get_current_folder_key(self) -> str | None:
        panel = self._get_current_panel()
        if panel is None:
            return None
        return str(panel.folder_path)

    def _set_current_folder_key(self, folder_key: str | None) -> None:
        if folder_key is not None and folder_key not in self._panels:
            folder_key = None
        self._current_folder_key = folder_key

    def _build_app_state(self) -> AppState:
        open_folders = list(self._panels.keys())
        folder_launchers = {
            folder_key: spec
            for folder_key, spec in self._folder_launchers.items()
            if folder_key in self._panels
        }
        return AppState(
            open_folders=open_folders,
            folder_launchers=folder_launchers,
        )

    def _save_app_state(self) -> None:
        save_state(self._build_app_state())

    def _resolve_saved_launcher_app(self, launcher: FolderLauncherSpec) -> Gio.AppInfo | None:
        apps = list_directory_app_infos()

        if launcher.app_id:
            for app_info in apps:
                if (app_info.get_id() or "") == launcher.app_id:
                    return app_info

        if launcher.app_exec:
            for app_info in apps:
                if (app_info.get_executable() or "") == launcher.app_exec:
                    return app_info

        return None

    def _launch_folder_with_app(
        self,
        folder_key: str,
        app_info: Gio.AppInfo,
    ) -> tuple[bool, str]:
        folder_path = Path(folder_key)
        if not folder_path.exists() or not folder_path.is_dir():
            return False, f"{folder_path} nao e mais uma pasta valida."

        folder_file = Gio.File.new_for_path(str(folder_path))
        try:
            app_info.launch([folder_file], None)
            return True, ""
        except GLib.Error as exc:
            return False, str(exc)
        except Exception as exc:  # pragma: no cover - erro de runtime do SO
            return False, str(exc)

    def _sync_sidebar_selection_to_notebook(self) -> None:
        current_page = self.notebook.get_current_page()
        if current_page < 0:
            self.folder_listbox.select_row(None)
            return

        panel = self.notebook.get_nth_page(current_page)
        if isinstance(panel, FolderTerminalPanel):
            self._select_sidebar_for_panel(panel)
            return

        self.folder_listbox.select_row(None)

    def _open_with_dialog(self, folder_key: str) -> None:
        if folder_key not in self._panels:
            return

        try:
            dialog = DirectoryAppChooserDialog(
                self,
                Path(folder_key),
                lambda app_info, current_key=folder_key: self._apply_folder_launcher(current_key, app_info),
            )
            show_all_if_needed(dialog)
            present(dialog)
        except Exception as exc:  # pragma: no cover - erro de runtime da GUI
            show_message_dialog(
                self,
                "Nao foi possivel abrir o seletor de aplicativo",
                "O dialogo de Open With falhou ao abrir.",
                details=str(exc),
                message_type=Gtk.MessageType.ERROR,
            )

    def _open_with_current_folder(self) -> None:
        folder_key = self._get_current_folder_key()
        if folder_key is None:
            return
        self._open_with_dialog(folder_key)

    def _apply_folder_launcher(self, folder_key: str, app_info: Gio.AppInfo) -> None:
        if folder_key not in self._panels:
            return

        success, details = self._launch_folder_with_app(folder_key, app_info)
        if not success:
            show_message_dialog(
                self,
                "Nao foi possivel abrir a pasta",
                f"A pasta {folder_key} nao conseguiu abrir com {get_app_info_name(app_info)}.",
                details=details,
                message_type=Gtk.MessageType.ERROR,
            )
            return

        self._folder_launchers[folder_key] = build_folder_launcher_spec(app_info)
        self._save_app_state()
        self._refresh_terminal_toolbar()

    def _open_folder_with_saved_launcher(self, folder_key: str) -> None:
        launcher = self._folder_launchers.get(folder_key)
        if launcher is None:
            self._refresh_terminal_toolbar()
            return

        app_info = self._resolve_saved_launcher_app(launcher)
        if app_info is None:
            self._refresh_terminal_toolbar()
            show_message_dialog(
                self,
                "Aplicativo indisponivel",
                "O aplicativo salvo para essa pasta nao esta mais disponivel.",
                details=launcher.app_name or launcher.app_exec or launcher.app_id,
                message_type=Gtk.MessageType.WARNING,
            )
            return

        success, details = self._launch_folder_with_app(folder_key, app_info)
        if not success:
            show_message_dialog(
                self,
                "Nao foi possivel abrir a pasta",
                f"A pasta {folder_key} nao conseguiu abrir com {get_app_info_name(app_info)}.",
                details=details,
                message_type=Gtk.MessageType.ERROR,
            )
            return

        updated_launcher = build_folder_launcher_spec(app_info)
        if updated_launcher != launcher:
            self._folder_launchers[folder_key] = updated_launcher
            self._save_app_state()
        self._refresh_terminal_toolbar()

    def _open_current_folder_with_saved_launcher(self) -> None:
        folder_key = self._get_current_folder_key()
        if folder_key is None:
            return
        self._open_folder_with_saved_launcher(folder_key)

    def _on_add_folder(self, _btn) -> None:
        if USING_GTK4:
            chooser = Gtk.FileChooserNative(
                title="Escolha uma pasta",
                transient_for=self,
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            chooser.connect("response", self._on_folder_chosen)
            chooser.show()
            return

        chooser = Gtk.FileChooserDialog(
            title="Escolha uma pasta",
            transient_for=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN,
            Gtk.ResponseType.ACCEPT,
        )
        chooser.set_modal(True)
        response = chooser.run()
        if response == Gtk.ResponseType.ACCEPT:
            selected = chooser.get_filename()
            if selected:
                self._add_folder(Path(selected).resolve())
        chooser.destroy()

    def _on_folder_chosen(self, chooser, response) -> None:
        if response != Gtk.ResponseType.ACCEPT:
            return

        file_obj = chooser.get_file()
        if file_obj is None:
            return

        selected = file_obj.get_path()
        if selected:
            self._add_folder(Path(selected).resolve())

    def _add_folder(self, folder_path: Path) -> None:
        if not folder_path.exists() or not folder_path.is_dir():
            show_message_dialog(
                self,
                "Pasta invalida",
                f"{folder_path} nao e uma pasta valida.",
                message_type=Gtk.MessageType.ERROR,
            )
            return

        key = str(folder_path)
        existing_panel = self._panels.get(key)
        if existing_panel is not None:
            page = self.notebook.page_num(existing_panel)
            if page >= 0:
                self.notebook.set_current_page(page)
                self._set_current_folder_key(key)
            self.main_stack.set_visible_child_name("terminals")
            self._refresh_terminal_toolbar()
            return

        panel = FolderTerminalPanel(folder_path, self.settings)
        self._panels[key] = panel

        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tab_label = Gtk.Label(label=folder_path.name)
        tab_label.set_max_width_chars(18)
        tab_label.set_ellipsize(Pango.EllipsizeMode.END)
        box_append(tab_box, tab_label)

        close_button = Gtk.Button()
        button_set_icon_name(close_button, "window-close-symbolic")
        add_css_class(close_button, "flat")
        close_button.set_focus_on_click(False)
        close_button.connect("clicked", lambda _btn, folder_key=key: self._remove_folder(folder_key))
        box_append(tab_box, close_button)

        page = self.notebook.append_page(panel, tab_box)
        self.notebook.set_current_page(page)
        self._set_current_folder_key(key)

        row = self._make_sidebar_row(folder_path, key)
        self._sidebar_rows[key] = row
        self._row_keys[id(row)] = key
        listbox_append(self.folder_listbox, row)

        self._save_app_state()
        self._refresh_sidebar_labels()
        self._update_terminal_workspace()
        self._refresh_terminal_toolbar()
        self.main_stack.set_visible_child_name("terminals")
        self._select_sidebar_for_key(key)
        show_all_if_needed(panel)
        show_all_if_needed(tab_box)
        show_all_if_needed(row)
        show_all_if_needed(self.folder_listbox)
        show_all_if_needed(self.notebook)

    def _make_sidebar_row(self, folder_path: Path, folder_key: str) -> Gtk.ListBoxRow:
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        add_css_class(content, "folder-row-box")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        shortcut_label = make_label("", ["folder-row-shortcut"])
        shortcut_label.set_width_chars(3)
        shortcut_label.set_xalign(0.0)
        box_append(header, shortcut_label)

        name_label = make_label(folder_path.name, ["folder-row-name"], hexpand=True)
        name_label.set_max_width_chars(22)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_xalign(0.0)
        box_append(header, name_label)

        box_append(content, header)

        path_label = make_label(str(folder_path), ["folder-row-path"])
        path_label.set_max_width_chars(24)
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box_append(content, path_label)

        row = Gtk.ListBoxRow()
        set_child(row, content)
        row.shortcut_label = shortcut_label
        row.name_label = name_label
        row.path_label = path_label
        row.folder_key = folder_key
        return row

    def _on_sidebar_activated(self, _listbox, row) -> None:
        folder_key = self._row_keys.get(id(row))
        if not folder_key:
            return

        panel = self._panels.get(folder_key)
        if panel is None:
            return

        page = self.notebook.page_num(panel)
        if page >= 0:
            self._go_to_terminal_tab(page)

    def _remove_folder(self, folder_key: str) -> None:
        panel = self._panels.pop(folder_key, None)
        if panel is not None:
            page = self.notebook.page_num(panel)
            if page >= 0:
                self.notebook.remove_page(page)

        row = self._sidebar_rows.pop(folder_key, None)
        if row is not None:
            self._row_keys.pop(id(row), None)
            self.folder_listbox.remove(row)

        if self._current_folder_key == folder_key:
            current_page = self.notebook.get_current_page()
            current_panel = self.notebook.get_nth_page(current_page) if current_page >= 0 else None
            if isinstance(current_panel, FolderTerminalPanel):
                self._set_current_folder_key(str(current_panel.folder_path))
            else:
                self._set_current_folder_key(None)

        self._folder_launchers.pop(folder_key, None)
        self._save_app_state()
        self._refresh_sidebar_labels()
        self._update_terminal_workspace()
        self._refresh_terminal_toolbar()
        self._sync_sidebar_selection_to_notebook()

    def _update_terminal_workspace(self) -> None:
        has_folders = bool(self._panels)
        self.terminal_stack.set_visible_child_name("notebook" if has_folders else "empty")
        self._refresh_terminal_toolbar()

    def _cycle_terminal_tab(self, direction: int) -> None:
        page_count = self.notebook.get_n_pages()
        if page_count <= 0:
            return

        self.main_stack.set_visible_child_name("terminals")
        current_page = self.notebook.get_current_page()
        if current_page < 0:
            current_page = 0

        next_page = (current_page + direction) % page_count
        self._go_to_terminal_tab(next_page)

    def _go_to_terminal_tab(self, page_index: int) -> None:
        page_count = self.notebook.get_n_pages()
        if page_count <= 0 or not 0 <= page_index < page_count:
            return

        self.main_stack.set_visible_child_name("terminals")
        self.notebook.set_current_page(page_index)

        panel = self.notebook.get_nth_page(page_index)
        if isinstance(panel, FolderTerminalPanel):
            self._set_current_folder_key(str(panel.folder_path))
            panel.vte.grab_focus()
            self._select_sidebar_for_panel(panel)
            self._refresh_terminal_toolbar()

    def _on_notebook_switch_page(self, _notebook, page, page_index) -> None:
        if isinstance(page, FolderTerminalPanel):
            self._set_current_folder_key(str(page.folder_path))
            self._select_sidebar_for_panel(page)
        else:
            self._set_current_folder_key(None)
        self._refresh_terminal_toolbar()

    def _select_sidebar_for_panel(self, panel: FolderTerminalPanel) -> None:
        for folder_key, known_panel in self._panels.items():
            if known_panel is panel:
                self._select_sidebar_for_key(folder_key)
                return

    def _select_sidebar_for_key(self, folder_key: str) -> None:
        row = self._sidebar_rows.get(folder_key)
        if row is None:
            return
        self.folder_listbox.select_row(row)

    def _refresh_sidebar_labels(self) -> None:
        page_count = self.notebook.get_n_pages()
        for page_index in range(page_count):
            panel = self.notebook.get_nth_page(page_index)
            if not isinstance(panel, FolderTerminalPanel):
                continue

            folder_key = str(panel.folder_path)
            row = self._sidebar_rows.get(folder_key)
            if row is None:
                continue

            shortcut_number = page_index + 1
            if shortcut_number <= 9:
                row.shortcut_label.set_text(f"[{shortcut_number}]")
            else:
                row.shortcut_label.set_text("[ ]")

    def _refresh_terminal_toolbar(self) -> None:
        panel = self._get_current_panel()
        if panel is None:
            self.current_folder_label.set_text("Nenhuma pasta selecionada")
            self.current_folder_label.set_tooltip_text(None)
            self.current_open_with_button.set_sensitive(False)
            self.current_quick_open_button.set_visible(False)
            self.manage_shortcuts_button.set_sensitive(True)
            return

        folder_key = str(panel.folder_path)
        folder_path = panel.folder_path
        self.current_folder_label.set_text(folder_path.name)
        self.current_folder_label.set_tooltip_text(str(folder_path))
        self.current_open_with_button.set_sensitive(True)
        self.manage_shortcuts_button.set_sensitive(True)

        launcher = self._folder_launchers.get(folder_key)
        if launcher is None:
            self.current_quick_open_button.set_visible(False)
            return

        app_info = self._resolve_saved_launcher_app(launcher)
        if app_info is None:
            self.current_quick_open_button.set_visible(False)
            self.current_quick_open_button.set_sensitive(False)
            return

        app_name = get_app_info_name(app_info)
        self.current_quick_open_button.set_label(f"Abrir com {truncate_text(app_name, 20)}")
        self.current_quick_open_button.set_tooltip_text(f"Abrir com {app_name}")
        self.current_quick_open_button.set_sensitive(True)
        self.current_quick_open_button.set_visible(True)

    def _restore_open_folders(self) -> None:
        missing_paths: list[str] = []

        for folder in self._restored_state.open_folders:
            path = Path(folder)
            if path.exists() and path.is_dir():
                self._add_folder(path.resolve())
            else:
                missing_paths.append(folder)
                self._folder_launchers.pop(folder, None)

        if missing_paths:
            self._save_app_state()

        if missing_paths:
            GLib.idle_add(
                self._show_restore_warning,
                "\n".join(missing_paths[:8]),
                len(missing_paths),
            )

    def _show_restore_warning(self, missing_paths: str, missing_count: int) -> bool:
        suffix = ""
        if missing_count > 8:
            suffix = f"\n... e mais {missing_count - 8} pasta(s)."
        show_message_dialog(
            self,
            "Algumas pastas nao foram restauradas",
            "Elas nao existem mais ou nao estao acessiveis.",
            details=missing_paths + suffix,
        )
        return False


class App(Gtk.Application):
    def __init__(self):
        set_application_identity(PROGRAM_NAME, APPLICATION_NAME)
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.settings = load_settings()

    def do_activate(self) -> None:
        window = MainWindow(self, self.settings)
        show_all_if_needed(window)
        present(window)


if __name__ == "__main__":
    App().run(sys.argv)
