from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from systemd_tray.config import parse_open_actions


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

def themed_icon(names: List[str], fallback: QtGui.QIcon) -> QtGui.QIcon:
    for name in names:
        icon = QtGui.QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    return fallback

def _make_triangle_icon(color: QtGui.QColor) -> QtGui.QIcon:
    pix = QtGui.QPixmap(18, 18)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(color)
    painter.setPen(QtGui.QPen(color, 1))
    points = [
        QtCore.QPointF(5, 4),
        QtCore.QPointF(14, 9),
        QtCore.QPointF(5, 14),
    ]
    painter.drawPolygon(QtGui.QPolygonF(points))
    painter.end()
    return QtGui.QIcon(pix)

def _make_stop_icon(color: QtGui.QColor) -> QtGui.QIcon:
    pix = QtGui.QPixmap(18, 18)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QtCore.QRectF(5, 5, 8, 8), 2, 2)
    painter.end()
    return QtGui.QIcon(pix)

def _make_log_icon(color: QtGui.QColor) -> QtGui.QIcon:
    pix = QtGui.QPixmap(18, 18)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(color, 1.6)
    painter.setPen(pen)
    for i, y in enumerate((6, 9, 12)):
        painter.drawLine(4, y, 14, y)
    painter.end()
    return QtGui.QIcon(pix)

def _make_open_icon(color: QtGui.QColor) -> QtGui.QIcon:
    pix = QtGui.QPixmap(18, 18)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(color, 1.6)
    painter.setPen(pen)
    painter.drawRect(4, 6, 7, 7)
    painter.drawLine(9, 9, 14, 4)
    painter.drawLine(11, 4, 14, 4)
    painter.drawLine(14, 4, 14, 7)
    painter.end()
    return QtGui.QIcon(pix)

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
        self.open_actions: List[Dict[str, str]] = []

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
        log_fallback = self.style().standardIcon(QtWidgets.QStyle.SP_FileDialogInfoView)
        self.log_button.setIcon(themed_icon(["utilities-log-viewer", "view-list-text"], log_fallback))
        self.log_button.setToolTip("Show journal logs")
        self.log_button.clicked.connect(self.on_logs)
        self.log_button.setIconSize(QtCore.QSize(16, 16))
        layout.addWidget(self.log_button)

        self.open_button = QtWidgets.QToolButton()
        self.open_button.setAutoRaise(True)
        open_fallback = self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton)
        self.open_button.setIcon(themed_icon(["document-open", "system-run"], open_fallback))
        self.open_button.setToolTip("Open…")
        self.open_button.setIconSize(QtCore.QSize(16, 16))
        self.open_button.clicked.connect(self.on_open_clicked)
        layout.addWidget(self.open_button)
        self.open_menu = QtWidgets.QMenu(self)
        self.open_button.hide()

        self.update_config(service)
        self.update_status("unknown")
        self._refresh_button_icons()

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

        self.open_actions = parse_open_actions(service)
        self._refresh_open_button()
        self._refresh_button_icons()

    def update_status(self, status: Optional[str]) -> None:
        self.status = (status or "unknown").strip().lower() or "unknown"
        color = status_indicator_color(self.status)
        self.indicator.setPixmap(indicator_pixmap(color))

        self._set_action_button_icon()

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

    def on_open_clicked(self) -> None:
        if not self.open_actions:
            return
        if len(self.open_actions) == 1:
            self.trigger_open(self.open_actions[0])

    def trigger_open(self, action: Dict[str, str]) -> None:
        if "url" in action:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(action["url"]))
        elif "command" in action:
            cmd = action["command"]
            if isinstance(cmd, list):
                QtCore.QProcess.startDetached(cmd[0], cmd[1:])
            else:
                QtCore.QProcess.startDetached(cmd)

    def _refresh_open_button(self) -> None:
        self.open_menu.clear()
        if not self.open_actions:
            self.open_button.hide()
            self.open_button.setMenu(None)
            return
        self.open_button.show()
        if len(self.open_actions) == 1:
            self.open_button.setPopupMode(QtWidgets.QToolButton.DelayedPopup)
            self.open_button.setMenu(None)
        else:
            for action in self.open_actions:
                act = self.open_menu.addAction(action.get("label", "Open"))
                act.triggered.connect(lambda checked=False, a=action: self.trigger_open(a))
            self.open_button.setMenu(self.open_menu)
            self.open_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)

    def _refresh_button_icons(self) -> None:
        palette = self.palette()
        color = palette.color(QtGui.QPalette.ButtonText)
        if color == palette.color(QtGui.QPalette.WindowText):
            # if button text color equals window text, ensure contrast
            color = palette.color(QtGui.QPalette.HighlightedText)
        self._icon_play = _make_triangle_icon(color)
        self._icon_stop = _make_stop_icon(color)
        self._icon_log = _make_log_icon(color)
        self._icon_open = _make_open_icon(color)

        self.log_button.setIcon(self._icon_log)
        if self.open_actions:
            self.open_button.setIcon(self._icon_open)
        self._set_action_button_icon()

    def _set_action_button_icon(self) -> None:
        if self.status == "active":
            icon = self._icon_stop
            tooltip = "Stop service"
        else:
            icon = self._icon_play
            tooltip = "Start service"
        self.action_button.setIcon(icon)
        self.action_button.setToolTip(tooltip)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        if event.type() == QtCore.QEvent.PaletteChange:
            self._refresh_button_icons()
        super().changeEvent(event)

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
            row.update_config(svc)
            row.update_status(self.tray.query_status(unit))
            self.tray.request_status_update(unit)

        self.empty_label.setVisible(not self.rows)

    def schedule_refresh(self, delay_ms: int = 800) -> None:
        QtCore.QTimer.singleShot(delay_ms, self.refresh)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self.poll_timer.start()
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self.poll_timer.stop()
        super().hideEvent(event)

    def update_unit_status(self, unit: str, status: str) -> None:
        row = self.rows.get(unit)
        if row is not None:
            row.update_status(status)
