#!/usr/bin/env python3
from __future__ import annotations

import gi


USING_GTK4 = False
try:
    gi.require_version("Vte", "3.91")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    USING_GTK4 = True
except (ValueError, ImportError):
    gi.require_version("Gtk", "3.0")
    gi.require_version("Vte", "2.91")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, Vte


def add_css_class(widget, css_class: str) -> None:
    if USING_GTK4:
        widget.add_css_class(css_class)
    else:
        widget.get_style_context().add_class(css_class)


def set_child(container, child) -> None:
    if USING_GTK4:
        container.set_child(child)
    else:
        container.add(child)


def box_append(box, child) -> None:
    if USING_GTK4:
        box.append(child)
        return

    expand = bool(
        getattr(child, "get_hexpand", lambda: False)()
        or getattr(child, "get_vexpand", lambda: False)()
    )
    box.pack_start(child, expand, expand, 0)


def listbox_append(listbox, row) -> None:
    if USING_GTK4:
        listbox.append(row)
    else:
        listbox.add(row)


def paned_set_start_child(paned, child) -> None:
    if USING_GTK4:
        paned.set_start_child(child)
    else:
        paned.pack1(child, True, False)


def paned_set_end_child(paned, child) -> None:
    if USING_GTK4:
        paned.set_end_child(child)
    else:
        paned.pack2(child, True, False)


def set_margin_start(widget, margin: int) -> None:
    if USING_GTK4:
        widget.set_margin_start(margin)
    else:
        widget.set_margin_left(margin)


def set_margin_end(widget, margin: int) -> None:
    if USING_GTK4:
        widget.set_margin_end(margin)
    else:
        widget.set_margin_right(margin)


def button_set_icon_name(button, icon_name: str) -> None:
    if USING_GTK4:
        button.set_icon_name(icon_name)
        return

    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    button.set_image(image)
    button.set_always_show_image(True)


def get_root(widget):
    if USING_GTK4:
        return widget.get_root()
    return widget.get_toplevel()


def present(widget) -> None:
    if hasattr(widget, "present"):
        widget.present()
    else:
        widget.show()


def set_label_wrap(label, enabled: bool = True) -> None:
    if hasattr(label, "set_wrap"):
        label.set_wrap(enabled)
        return
    if hasattr(label, "set_line_wrap"):
        label.set_line_wrap(enabled)


def show_all_if_needed(widget) -> None:
    if not USING_GTK4 and hasattr(widget, "show_all"):
        widget.show_all()


def apply_css(css_data: bytes) -> Gtk.CssProvider:
    provider = Gtk.CssProvider()
    provider.load_from_data(css_data)
    if USING_GTK4:
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    else:
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    return provider


def minimize_window(window) -> None:
    if hasattr(window, "minimize"):
        window.minimize()
        return
    if hasattr(window, "iconify"):
        window.iconify()


def set_window_icon_from_file(window, icon_path: str) -> None:
    if not icon_path:
        return

    if USING_GTK4:
        # GTK4 nao expoe uma API estavel de icone por janela como no GTK3.
        # Mantemos no-op aqui e deixamos o icone ser embutido no executavel.
        return

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
        window.set_icon(pixbuf)
        Gtk.Window.set_default_icon(pixbuf)
    except Exception:
        return


def set_application_identity(program_name: str, application_name: str) -> None:
    try:
        GLib.set_prgname(program_name)
    except Exception:
        pass

    try:
        GLib.set_application_name(application_name)
    except Exception:
        pass

    try:
        Gdk.set_program_class(program_name)
    except Exception:
        pass
