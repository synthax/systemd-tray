
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from .icon_utils import create_svg_icon, icon_has_pixmaps
from .systemd_backend import ServiceCandidate

if TYPE_CHECKING:
    from .main import TrayApp

class ConfiguratorDialog(QtWidgets.QDialog):
    def __init__(self, tray: "TrayApp", config: Dict):
        super().__init__(tray.contextMenu())
        self.tray = tray
        self.setWindowTitle("Manage Services")

        icon = tray.icon()
        if not icon_has_pixmaps(icon):
            icon = QtGui.QIcon.fromTheme("systemd-tray")
        if not icon_has_pixmaps(icon):
            icon = QtGui.QIcon.fromTheme("systemd-tray-symbolic")
        if not icon_has_pixmaps(icon):
            icons_dir = Path(__file__).resolve().parent.parent / "icons"
            icon = create_svg_icon(icons_dir / "systemd-tray-dark.svg")
        if not icon_has_pixmaps(icon):
            icon = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        if icon_has_pixmaps(icon):
            self.setWindowIcon(icon)
        self.resize(520, 560)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Filter services…")
        layout.addWidget(self.search_edit)

        self.show_hidden_box = QtWidgets.QCheckBox("Show filtered units")
        layout.addWidget(self.show_hidden_box)

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        layout.addWidget(self.list_widget, 1)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.reload_button = QtWidgets.QPushButton("Reload service daemon")
        self.reload_button.clicked.connect(self.on_reload_daemon)
        layout.addWidget(self.reload_button)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.current_config = config
        self.unit_to_config = {svc.get("unit"): svc for svc in self.current_config.get("services", []) if svc.get("unit")}

        self.search_edit.textChanged.connect(self._apply_filter)
        self.show_hidden_box.toggled.connect(self._on_show_hidden_toggled)
        self._populate_list(force_refresh=True)

    def _display_text(self, candidate: ServiceCandidate) -> str:
        parts = [candidate.unit]
        if candidate.description:
            parts.append(f"— {candidate.description}")
        return " ".join(parts)

    def _populate_list(self, force_refresh: bool = False) -> None:
        current_states: Dict[str, QtCore.Qt.CheckState] = {}
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            candidate: ServiceCandidate = item.data(QtCore.Qt.UserRole)
            current_states[candidate.unit] = item.checkState()

        self.list_widget.clear()
        existing_units = set(self.unit_to_config.keys())
        show_hidden = self.show_hidden_box.isChecked()

        services = self.tray.backend.list_services(
            include_hidden=True,
            required_units=existing_units,
            force_refresh=force_refresh,
        )
        visible_candidates = [
            c for c in services
            if show_hidden or not c.hidden or c.unit in existing_units or current_states.get(c.unit) == QtCore.Qt.Checked
        ]

        for candidate in sorted(visible_candidates, key=lambda c: (c.description.lower() if c.description else c.unit.lower())):
            item = QtWidgets.QListWidgetItem(self._display_text(candidate))
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            state = current_states.get(candidate.unit)
            if state is None:
                state = QtCore.Qt.Checked if candidate.unit in existing_units else QtCore.Qt.Unchecked
            item.setCheckState(state)
            item.setData(QtCore.Qt.UserRole, candidate)

            tooltip_lines = [candidate.unit]
            if candidate.description:
                tooltip_lines.append(candidate.description)
            if candidate.fragment_path:
                tooltip_lines.append(candidate.fragment_path)
            tooltip_lines.append(f"State: {candidate.state}")
            if candidate.hidden:
                tooltip_lines.append("(filtered by default)")
                item.setForeground(QtGui.QColor("#6c757d"))
            item.setToolTip("\n".join(tooltip_lines))
            self.list_widget.addItem(item)

        if not visible_candidates:
            self.status_label.setText("No manageable services were detected. Toggle ‘Show filtered units’ to include helper/autostart entries.")
        else:
            self.status_label.setText("Select the services you want quick access to. Helper/autostart units stay hidden unless requested.")

        self._apply_filter(self.search_edit.text())

    def _on_show_hidden_toggled(self, _: bool) -> None:
        self._populate_list(force_refresh=True)

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
            extras = {
                k: v
                for k, v in existing.items()
                if k not in {"name", "unit", "logs"}
            }
            selected.append({**extras, "name": name, "unit": candidate.unit, "logs": logs})
        return selected

    def on_reload_daemon(self) -> None:
        self.reload_button.setEnabled(False)
        self.reload_button.setText("Reloading…")
        self.tray.backend.reload_daemon()

    def _reset_reload_button(self) -> None:
        self.reload_button.setEnabled(True)
        self.reload_button.setText("Reload service daemon")
