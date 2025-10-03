#!/usr/bin/env python3
"""
Systemd Tray Manager

A tiny Qt tray app to start/stop/restart user systemd services and view logs.
- Config file: ~/.config/systemd-tray/services.yaml
- Dependencies: PySide6, PyYAML

"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict

from PySide6 import QtGui, QtWidgets

from .config import ensure_config, save_config
from .configurator_dialog import ConfiguratorDialog
from .icon_utils import create_svg_icon, icon_has_pixmaps
from .log_window import LogWindow
from .services_panel import ServicesPanel
from .systemd_backend import SystemdBackend

APP_NAME = "Systemd Tray"

class TrayApp(QtWidgets.QSystemTrayIcon):
    def __init__(self, icon: QtGui.QIcon, app: QtWidgets.QApplication, config: Dict):
        super().__init__(icon, app)
        self.app = app
        self.config = config
        self.setToolTip(APP_NAME)

        self.menu = QtWidgets.QMenu()
        self.setContextMenu(self.menu)
        self.log_windows: Dict[str, LogWindow] = {}
        self.panel = ServicesPanel(self)
        self.config_dialog: ConfiguratorDialog | None = None
        self.backend = SystemdBackend(self)
        self.backend.statusFetched.connect(self.on_status_fetched)
        self.backend.commandFinished.connect(self.on_command_finished)

        self.status_cache: Dict[str, tuple[float, str]] = {}
        self.status_ttl = 3.0
        self.last_status: Dict[str, str] = {}
        self.suppressed_until: Dict[str, float] = {}

        self.reload_menu()
        self.panel.set_services(self.config)
        self.activated.connect(self.on_activated)
        self.refresh_all_statuses()

    # UI wiring -------------------------------------------------------------
    def reload_menu(self) -> None:
        self.menu.clear()
        act_manage = self.menu.addAction("Manage services…")
        act_manage.triggered.connect(self.open_configurator)
        self.menu.addSeparator()
        self.menu.addAction("Reload config").triggered.connect(self.reload_config)
        self.menu.addAction("Quit").triggered.connect(self.app.quit)

    def on_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            if self.panel.isVisible():
                self.panel.hide()
            else:
                self.panel.show_at(QtGui.QCursor.pos())

    def notify(self, title: str, msg: str) -> None:
        self.showMessage(title, msg, QtWidgets.QSystemTrayIcon.Information, 4000)

    # Status handling -------------------------------------------------------
    def query_status(self, unit: str) -> str:
        entry = self.status_cache.get(unit)
        if entry:
            return entry[1]
        return "unknown"

    def request_status_update(self, unit: str) -> None:
        if not unit:
            return
        entry = self.status_cache.get(unit)
        if entry and time.monotonic() - entry[0] <= self.status_ttl:
            return
        self.backend.request_status(unit)

    def refresh_all_statuses(self) -> None:
        for svc in self.config.get("services", []):
            unit = svc.get("unit")
            if unit:
                self.request_status_update(unit)

    def handle_status_update(self, unit: str, status: str | None) -> None:
        normalized = (status or "unknown").strip().lower() or "unknown"
        previous = self.last_status.get(unit)
        self.last_status[unit] = normalized

        expiry = self.suppressed_until.get(unit)
        if expiry is not None and time.time() < expiry:
            return
        self.suppressed_until.pop(unit, None)

        if previous != "active" or normalized not in {"inactive", "failed"}:
            return
        self.notify(unit, f"Service became {normalized}")

    def suppress_unit_notifications(self, unit: str, duration: float = 10.0) -> None:
        self.suppressed_until[unit] = time.time() + duration

    def prune_state_cache(self) -> None:
        now = time.monotonic()
        active_units = {svc.get("unit") for svc in self.config.get("services", []) if svc.get("unit")}
        self.last_status = {k: v for k, v in self.last_status.items() if k in active_units}
        self.status_cache = {
            unit: (ts, status)
            for unit, (ts, status) in self.status_cache.items()
            if unit in active_units and now - ts <= self.status_ttl * 2
        }
        self.suppressed_until = {k: v for k, v in self.suppressed_until.items() if k in active_units}

    # Backend callbacks -----------------------------------------------------
    def on_status_fetched(self, unit: str, status: str) -> None:
        status = (status or "unknown").strip() or "unknown"
        self.status_cache[unit] = (time.monotonic(), status)
        self.handle_status_update(unit, status)
        self.panel.update_unit_status(unit, status)

    def on_command_finished(self, unit: str, action: str, success: bool, message: str) -> None:
        if success:
            if action == "start":
                self.suppress_unit_notifications(unit)
            elif action == "stop":
                self.suppress_unit_notifications(unit)
            elif action == "restart":
                self.suppress_unit_notifications(unit, duration=12.0)
            elif action == "daemon-reload":
                if self.config_dialog is not None:
                    self.config_dialog._reset_reload_button()
            self.status_cache.pop(unit, None)
            self.request_status_update(unit)
        else:
            detail = message or "Unknown error"
            if action == "daemon-reload":
                self.notify(APP_NAME, f"Daemon reload failed: {detail}")
                if self.config_dialog is not None:
                    self.config_dialog._reset_reload_button()
            else:
                self.notify(unit, f"{action.capitalize()} failed: {detail}")
                self.suppressed_until.pop(unit, None)
        self.panel.schedule_refresh(800)

    # Actions ---------------------------------------------------------------
    def status(self, unit: str) -> None:
        state = self.query_status(unit)
        self.notify(unit, f"Status: {state}")
        self.request_status_update(unit)

    def start(self, unit: str) -> bool:
        self.backend.start_unit(unit)
        return True

    def stop(self, unit: str) -> bool:
        self.backend.stop_unit(unit)
        return True

    def restart(self, unit: str) -> bool:
        self.backend.restart_unit(unit)
        return True

    def show_logs(self, unit: str, lines: int, follow: bool) -> None:
        win = self.log_windows.get(unit)
        if win is None:
            win = LogWindow(unit, lines=lines, follow=follow)
            self.log_windows[unit] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def open_configurator(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        if self.config_dialog is not None:
            if not self.config_dialog.isVisible():
                self.config_dialog.show()
            self.config_dialog.raise_()
            self.config_dialog.activateWindow()
            return

        dialog = ConfiguratorDialog(self, self.config)
        self.config_dialog = dialog
        try:
            if dialog.exec() == QtWidgets.QDialog.Accepted:
                services = dialog.selected_services()
                self.config = {"services": services}
                save_config(self.config)
                self.panel.set_services(self.config)
                self.panel.refresh()
                self.prune_state_cache()
                self.refresh_all_statuses()
        finally:
            if self.config_dialog is dialog:
                self.config_dialog = None

    def reload_config(self) -> None:
        self.config = ensure_config()
        self.panel.set_services(self.config)
        self.panel.refresh()
        self.prune_state_cache()
        self.refresh_all_statuses()

def main() -> None:
    config = ensure_config()

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    icon = QtGui.QIcon.fromTheme("systemd-tray")
    if not icon_has_pixmaps(icon):
        icon = QtGui.QIcon.fromTheme("systemd-tray-symbolic")

    if not icon_has_pixmaps(icon):
        icons_dir = Path(__file__).resolve().parent.parent / "icons"
        window_color = app.palette().color(QtGui.QPalette.Window)
        lightness = window_color.lightnessF()
        fallback_name = "systemd-tray-dark.svg" if lightness < 0.45 else "systemd-tray-light.svg"
        svg_icon = create_svg_icon(icons_dir / fallback_name)
        if svg_icon is not None:
            icon = svg_icon

    if icon_has_pixmaps(icon):
        app.setWindowIcon(icon)

    tray = TrayApp(icon, app, config)
    tray.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    if sys.platform != "linux":
        print("This app is intended for Linux systemd user sessions.")
    main()
