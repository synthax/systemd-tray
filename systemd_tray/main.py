#!/usr/bin/env python3
"""
Systemd Tray Manager

A tiny Qt tray app to start/stop/restart user systemd services and view logs.
- Config file: ~/.config/systemd-tray/services.yaml
- Dependencies: PySide6, PyYAML

Arch/Manjaro:
  sudo pacman -S python-pyside6 python-yaml

Run:
  python -m systemd_tray

"""
from __future__ import annotations
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:  # pragma: no cover - optional component
    QSvgRenderer = None

try:
    import yaml  # type: ignore
except Exception as e:
    yaml = None

CONFIG_DIR = Path.home() / ".config" / "systemd-tray"
CONFIG_PATH = CONFIG_DIR / "services.yaml"
APP_NAME = "Systemd Tray"
MANAGEABLE_STATES = {
    "enabled",
    "disabled",
    "generated",
    "enabled-runtime",
    "disabled-runtime",
    "linked",
    "linked-runtime",
    "transient",
}
DEFAULT_EXCLUDE_PREFIXES = (
    "dbus-",
    "org.",
    "gnome-",
    "kde-",
    "plasma-",
    "xdg-",
    "systemd-",
    "pipewire",
    "evolution-",
    "tracker-",
)

DEFAULT_CONFIG = {
    "services": [
        {
            "name": "ComfyUI",
            "unit": "comfyui.service",
            "logs": {
                "follow": True,
                "lines": 200
            }
        }
    ]
}


def create_svg_icon(path: Path) -> Optional[QtGui.QIcon]:
    if QSvgRenderer is None:
        return None
    if not path.exists():
        return None
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return None

    icon = QtGui.QIcon()
    for size in (16, 20, 24, 32, 48, 64, 96, 128):
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        inset = max(1, int(size * 0.0625))  # ~1/16 padding to avoid clipping
        target = QtCore.QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        renderer.render(painter, target)
        painter.end()
        icon.addPixmap(pix)
    return icon


def ensure_config() -> Dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if yaml is None:
            # minimal fallback: write a trivial YAML ourselves
            CONFIG_PATH.write_text("services:\n  - name: ComfyUI\n    unit: comfyui.service\n    logs:\n      follow: true\n      lines: 200\n", encoding="utf-8")
        else:
            CONFIG_PATH.write_text(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False), encoding="utf-8")
    # Load
    if yaml is None:
        # crude parser for our tiny schema
        services: List[Dict] = []
        name = None
        unit = None
        lines = 200
        follow = True
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s.startswith("- "):
                    if name and unit:
                        services.append({"name": name, "unit": unit, "logs": {"follow": follow, "lines": lines}})
                    name = unit = None
                    lines = 200
                    follow = True
                elif s.startswith("name:"):
                    name = s.split(":", 1)[1].strip()
                elif s.startswith("unit:"):
                    unit = s.split(":", 1)[1].strip()
                elif s.startswith("lines:"):
                    try:
                        lines = int(s.split(":", 1)[1].strip())
                    except Exception:
                        lines = 200
                elif s.startswith("follow:"):
                    follow = s.split(":", 1)[1].strip().lower() in {"true", "1", "yes", "on"}
        if name and unit:
            services.append({"name": name, "unit": unit, "logs": {"follow": follow, "lines": lines}})
        return {"services": services}
    else:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {"services": []}


def save_config(config: Dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    services = config.get("services", [])
    data = {"services": services}
    if yaml is None:
        lines = ["services:"]
        for svc in services:
            lines.append("  - unit: {}".format(svc.get("unit", "")))
            name = svc.get("name")
            if name:
                lines.append("    name: {}".format(name))
            logs = svc.get("logs", {}) or {}
            if logs:
                lines.append("    logs:")
                follow = logs.get("follow")
                if follow is not None:
                    lines.append(f"      follow: {'true' if follow else 'false'}")
                lines_val = logs.get("lines")
                if lines_val is not None:
                    lines.append(f"      lines: {lines_val}")
        CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@dataclass
class ServiceCandidate:
    unit: str
    state: str
    description: str
    fragment_path: Optional[str]


def _should_expose_unit(unit: str, state: str, fragment_path: Optional[str]) -> bool:
    if state not in MANAGEABLE_STATES:
        return False
    if unit.endswith("@.service"):
        return False
    lowered = unit.lower()
    for prefix in DEFAULT_EXCLUDE_PREFIXES:
        if lowered.startswith(prefix):
            return False
    return True


def describe_unit(unit: str) -> tuple[str, Optional[str]]:
    cp = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=Description", "--property=FragmentPath"],
        capture_output=True,
        text=True,
    )
    description = ""
    fragment_path: Optional[str] = None
    if cp.returncode == 0:
        for line in cp.stdout.splitlines():
            if line.startswith("Description="):
                description = line.split("=", 1)[1].strip()
            elif line.startswith("FragmentPath="):
                value = line.split("=", 1)[1].strip()
                fragment_path = value or None
    return description, fragment_path


def list_user_services() -> List[ServiceCandidate]:
    cp = subprocess.run(
        ["systemctl", "--user", "list-unit-files", "--type=service", "--no-legend", "--no-pager"],
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        return []
    candidates: List[ServiceCandidate] = []
    for line in cp.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        unit, state = parts[0], parts[1]
        desc, frag = describe_unit(unit)
        if _should_expose_unit(unit, state, frag):
            candidates.append(ServiceCandidate(unit=unit, state=state, description=desc, fragment_path=frag))
    return candidates


class LogWindow(QtWidgets.QMainWindow):
    def __init__(self, unit: str, lines: int = 200, follow: bool = True):
        super().__init__()
        self.unit = unit
        self.setWindowTitle(f"Logs: {unit}")
        self.resize(900, 600)
        self.text = QtWidgets.QPlainTextEdit(self)
        self.text.setReadOnly(True)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        self.text.setFont(font)
        self.setCentralWidget(self.text)

        self.proc = QtCore.QProcess(self)
        args = ["--user", "-u", unit]
        if lines:
            args = ["--user", "-u", unit, "-n", str(lines)]
        if follow:
            args.append("-f")
        self.proc.setProgram("journalctl")
        self.proc.setArguments(args)
        self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.on_output)
        self.proc.finished.connect(self.on_finished)
        self.proc.start()

        toolbar = QtWidgets.QToolBar()
        self.addToolBar(toolbar)
        act_copy = QtGui.QAction("Copy all", self)
        act_copy.triggered.connect(self.copy_all)
        toolbar.addAction(act_copy)
        act_pause = QtGui.QAction("Pause", self)
        act_pause.setCheckable(True)
        act_pause.triggered.connect(self.toggle_pause)
        toolbar.addAction(act_pause)
        act_clear = QtGui.QAction("Clear", self)
        act_clear.triggered.connect(self.text.clear)
        toolbar.addAction(act_clear)

        self._paused = False

    def on_output(self):
        if self._paused:
            self.proc.readAllStandardOutput()  # discard
            return
        data = self.proc.readAllStandardOutput().data().decode(errors="replace")
        self.text.appendPlainText(data)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def on_finished(self):
        self.text.appendPlainText("\n[log stream ended]")

    def copy_all(self):
        self.text.selectAll()
        self.text.copy()

    def toggle_pause(self, checked: bool):
        self._paused = checked

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self.proc.state() == QtCore.QProcess.Running:
                self.proc.kill()
        except Exception:
            pass
        return super().closeEvent(event)


def status_indicator_color(status: Optional[str]) -> str:
    normalized = (status or "").strip().lower()
    mapping = {
        "active": "#4caf50",
        "activating": "#2196f3",
        "reloading": "#2196f3",
        "deactivating": "#ff9800",
        "inactive": "#f44336",
        "failed": "#f44336",
    }
    return mapping.get(normalized, "#9e9e9e")


def indicator_pixmap(color: str, diameter: int = 12) -> QtGui.QPixmap:
    pix = QtGui.QPixmap(diameter, diameter)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(QtGui.QColor(color))
    painter.setPen(QtGui.QPen(QtGui.QColor(color)))
    painter.drawEllipse(0, 0, diameter - 1, diameter - 1)
    painter.end()
    return pix


class ServiceRow(QtWidgets.QWidget):
    def __init__(self, panel: "ServicesPanel", service: Dict):
        super().__init__(panel)
        self.panel = panel
        self.tray = panel.tray
        self.service: Dict = {}
        self.unit: str = ""
        self.lines: int = 200
        self.follow: bool = True
        self.status: str = "unknown"

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.indicator = QtWidgets.QLabel()
        self.indicator.setFixedSize(12, 12)
        layout.addWidget(self.indicator)

        self.name_label = QtWidgets.QLabel()
        self.name_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.name_label)

        self.action_button = QtWidgets.QToolButton()
        self.action_button.setAutoRaise(True)
        self.action_button.clicked.connect(self.on_action)
        self.action_button.setIconSize(QtCore.QSize(16, 16))
        layout.addWidget(self.action_button)

        self.log_button = QtWidgets.QToolButton()
        self.log_button.setAutoRaise(True)
        self.log_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogInfoView))
        self.log_button.setToolTip("Show journal logs")
        self.log_button.clicked.connect(self.on_logs)
        self.log_button.setIconSize(QtCore.QSize(16, 16))
        layout.addWidget(self.log_button)

        self.update_config(service)
        self.update_status("unknown")

    def update_config(self, service: Dict) -> None:
        self.service = service
        self.unit = service.get("unit", self.unit)
        self.name_label.setText(service.get("name") or self.unit)

        logs = service.get("logs", {})
        try:
            self.lines = int(logs.get("lines", self.lines))
        except Exception:
            self.lines = 200
        self.follow = bool(logs.get("follow", self.follow))

    def update_status(self, status: Optional[str]) -> None:
        self.status = (status or "unknown").strip().lower() or "unknown"
        color = status_indicator_color(self.status)
        self.indicator.setPixmap(indicator_pixmap(color))

        if self.status == "active":
            icon = self.style().standardIcon(QtWidgets.QStyle.SP_MediaStop)
            tooltip = "Stop service"
        else:
            icon = self.style().standardIcon(QtWidgets.QStyle.SP_MediaPlay)
            tooltip = "Start service"
        self.action_button.setIcon(icon)
        self.action_button.setToolTip(tooltip)

    def on_action(self) -> None:
        if not self.unit:
            return
        if self.status == "active":
            self.tray.stop(self.unit)
        else:
            self.tray.start(self.unit)
        self.panel.schedule_refresh(1200)

    def on_logs(self) -> None:
        if not self.unit:
            return
        self.tray.show_logs(self.unit, self.lines, self.follow)


class ServicesPanel(QtWidgets.QFrame):
    def __init__(self, tray: "TrayApp"):
        flags = QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint
        super().__init__(None, flags)
        self.tray = tray
        self.setObjectName("servicesPanel")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Raised)
        self.setMinimumWidth(320)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QtWidgets.QLabel("Services")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        self.list_widget = QtWidgets.QWidget()
        self.list_layout = QtWidgets.QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        layout.addWidget(self.list_widget)

        self.empty_label = QtWidgets.QLabel("No services configured")
        self.empty_label.setEnabled(False)
        self.list_layout.addWidget(self.empty_label)

        self.rows: Dict[str, ServiceRow] = {}

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.refresh)

    def set_services(self, config: Dict) -> None:
        services = config.get("services", [])
        seen_units = set()
        for svc in services:
            unit = svc.get("unit")
            if not unit:
                continue
            seen_units.add(unit)
            row = self.rows.get(unit)
            if row is None:
                row = ServiceRow(self, svc)
                self.rows[unit] = row
                self.list_layout.addWidget(row)
            else:
                row.update_config(svc)

        for unit, row in list(self.rows.items()):
            if unit not in seen_units:
                row.setParent(None)
                row.deleteLater()
                del self.rows[unit]

        self.empty_label.setVisible(not self.rows)

    def show_at(self, global_pos: QtCore.QPoint) -> None:
        self.set_services(self.tray.config)
        self.refresh()
        self.adjustSize()
        screen = QtWidgets.QApplication.screenAt(global_pos) or QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
        else:
            geometry = QtCore.QRect(global_pos.x(), global_pos.y(), self.width(), self.height())

        x = max(geometry.left(), min(global_pos.x() - self.width() // 2, geometry.right() - self.width()))
        y = max(geometry.top(), min(global_pos.y() - self.height(), geometry.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        services = self.tray.config.get("services", [])
        if not services:
            self.empty_label.show()
            return

        for svc in services:
            unit = svc.get("unit")
            if not unit:
                continue
            row = self.rows.get(unit)
            if row is None:
                row = ServiceRow(self, svc)
                self.rows[unit] = row
                self.list_layout.addWidget(row)
            status = self.tray.query_status(unit)
            row.update_config(svc)
            row.update_status(status)
            self.tray.handle_status_update(unit, status)

        self.empty_label.setVisible(not self.rows)

    def schedule_refresh(self, delay_ms: int = 800) -> None:
        QtCore.QTimer.singleShot(delay_ms, self.refresh)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self.poll_timer.start()
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.poll_timer.stop()
        super().hideEvent(event)


class ConfiguratorDialog(QtWidgets.QDialog):
    def __init__(self, tray: "TrayApp", config: Dict):
        super().__init__(tray.contextMenu())
        self.tray = tray
        self.setWindowTitle("Manage Services")
        self.resize(480, 520)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Filter services…")
        layout.addWidget(self.search_edit)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        layout.addWidget(self.list_widget, 1)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.current_config = config
        self.unit_to_config = {svc.get("unit"): svc for svc in self.current_config.get("services", []) if svc.get("unit")}

        self.candidates = list_user_services()
        self._populate_list()
        self.search_edit.textChanged.connect(self._apply_filter)

    def _display_text(self, candidate: ServiceCandidate) -> str:
        parts = [candidate.unit]
        if candidate.description:
            parts.append(f"— {candidate.description}")
        return " ".join(parts)

    def _populate_list(self) -> None:
        self.list_widget.clear()
        existing_units = set(self.unit_to_config.keys())
        for candidate in sorted(self.candidates, key=lambda c: (c.description.lower() if c.description else c.unit.lower())):
            item = QtWidgets.QListWidgetItem(self._display_text(candidate))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if candidate.unit in existing_units else QtCore.Qt.Unchecked)
            item.setData(QtCore.Qt.UserRole, candidate)
            tooltip_lines = [candidate.unit]
            if candidate.description:
                tooltip_lines.append(candidate.description)
            if candidate.fragment_path:
                tooltip_lines.append(candidate.fragment_path)
            tooltip_lines.append(f"State: {candidate.state}")
            item.setToolTip("\n".join(tooltip_lines))
            self.list_widget.addItem(item)

        if not self.candidates:
            self.status_label.setText("No manageable services were detected. Create user units under ~/.config/systemd/user/ to add them here.")
        else:
            self.status_label.setText("Select the services you want quick access to. Static and system helper units are hidden by default.")

    def _apply_filter(self, text: str) -> None:
        text = text.strip().lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            candidate: ServiceCandidate = item.data(QtCore.Qt.UserRole)
            display = self._display_text(candidate).lower()
            matches = text in display if text else True
            item.setHidden(not matches)

    def selected_services(self) -> List[Dict]:
        selected: List[Dict] = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() != QtCore.Qt.Checked:
                continue
            candidate: ServiceCandidate = item.data(QtCore.Qt.UserRole)
            existing = self.unit_to_config.get(candidate.unit, {})
            name = existing.get("name") or (candidate.description or candidate.unit)
            logs_config = existing.get("logs") or {}
            follow_val = logs_config.get("follow", True)
            if isinstance(follow_val, str):
                follow = follow_val.strip().lower() in {"true", "1", "yes", "on"}
            else:
                follow = bool(follow_val)
            lines_val = logs_config.get("lines", 200)
            try:
                lines_int = int(lines_val)
            except Exception:
                lines_int = 200
            logs = {"follow": follow, "lines": lines_int}
            selected.append({"name": name, "unit": candidate.unit, "logs": logs})
        return selected


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
        self.last_status: Dict[str, str] = {}
        self.suppressed_until: Dict[str, float] = {}

        self.reload_menu()
        self.panel.set_services(self.config)
        self.activated.connect(self.on_activated)

    def on_activated(self, reason):
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            if self.panel.isVisible():
                self.panel.hide()
            else:
                self.panel.show_at(QtGui.QCursor.pos())

    def reload_menu(self):
        self.menu.clear()
        act_manage = self.menu.addAction("Manage services…")
        act_manage.triggered.connect(self.open_configurator)
        self.menu.addSeparator()
        act_reload = self.menu.addAction("Reload config")
        act_reload.triggered.connect(self.reload_config)
        act_quit = self.menu.addAction("Quit")
        act_quit.triggered.connect(self.app.quit)

    def notify(self, title: str, msg: str):
        self.showMessage(title, msg, QtWidgets.QSystemTrayIcon.Information, 4000)

    def _run(self, args: List[str]) -> subprocess.CompletedProcess:
        # Always run as user unit
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)

    def query_status(self, unit: str) -> str:
        cp = self._run(["is-active", unit])
        output = cp.stdout.strip() or cp.stderr.strip()
        return output or "unknown"

    def handle_status_update(self, unit: str, status: Optional[str]) -> None:
        normalized = (status or "unknown").strip().lower() or "unknown"
        previous = self.last_status.get(unit)
        self.last_status[unit] = normalized

        expiry = self.suppressed_until.get(unit)
        if expiry is not None:
            if time.time() < expiry:
                return
            self.suppressed_until.pop(unit, None)

        if previous == normalized:
            return

        if previous == "active" and normalized in {"inactive", "failed"}:
            self.notify(unit, f"Service became {normalized}")

    def suppress_unit_notifications(self, unit: str, duration: float = 10.0) -> None:
        self.suppressed_until[unit] = time.time() + duration

    def prune_state_cache(self) -> None:
        active_units = {svc.get("unit") for svc in self.config.get("services", []) if svc.get("unit")}
        self.last_status = {unit: status for unit, status in self.last_status.items() if unit in active_units}
        self.suppressed_until = {unit: expiry for unit, expiry in self.suppressed_until.items() if unit in active_units}

    def open_configurator(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
        dialog = ConfiguratorDialog(self, self.config)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            services = dialog.selected_services()
            self.config = {"services": services}
            save_config(self.config)
            self.panel.set_services(self.config)
            self.panel.refresh()
            self.prune_state_cache()

    def status(self, unit: str):
        state = self.query_status(unit)
        self.notify(unit, f"Status: {state}")

    def start(self, unit: str) -> bool:
        cp = self._run(["start", unit])
        if cp.returncode == 0:
            self.suppress_unit_notifications(unit)
            return True
        self.notify(unit, f"Start failed: {cp.stderr.strip() or cp.stdout.strip()}")
        self.suppressed_until.pop(unit, None)
        return False

    def stop(self, unit: str) -> bool:
        cp = self._run(["stop", unit])
        if cp.returncode == 0:
            self.suppress_unit_notifications(unit)
            return True
        self.notify(unit, f"Stop failed: {cp.stderr.strip() or cp.stdout.strip()}")
        self.suppressed_until.pop(unit, None)
        return False

    def restart(self, unit: str) -> bool:
        cp = self._run(["restart", unit])
        if cp.returncode == 0:
            self.suppress_unit_notifications(unit, duration=12.0)
            return True
        self.notify(unit, f"Restart failed: {cp.stderr.strip() or cp.stdout.strip()}")
        self.suppressed_until.pop(unit, None)
        return False

    def show_logs(self, unit: str, lines: int, follow: bool):
        win = self.log_windows.get(unit)
        if win is None:
            win = LogWindow(unit, lines=lines, follow=follow)
            self.log_windows[unit] = win
        win.show()
        win.raise_()
        win.activateWindow()

    def reload_config(self):
        self.config = ensure_config()
        self.panel.set_services(self.config)
        self.panel.refresh()
        self.prune_state_cache()


def main():
    # Ensure config exists
    config = ensure_config()

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Pick an icon
    icon = QtGui.QIcon.fromTheme("systemd-tray")
    if icon.isNull():
        icon = QtGui.QIcon.fromTheme("systemd-tray-symbolic")

    if icon.isNull():
        icons_dir = Path(__file__).resolve().parent.parent / "icons"
        window_color = app.palette().color(QtGui.QPalette.Window)
        lightness = window_color.lightnessF()
        fallback_name = "systemd-tray-dark.svg" if lightness < 0.45 else "systemd-tray-light.svg"
        svg_icon = create_svg_icon(icons_dir / fallback_name)
        if svg_icon is not None:
            icon = svg_icon

    tray = TrayApp(icon, app, config)
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    if sys.platform != "linux":
        print("This app is intended for Linux systemd user sessions.")
    main()
