from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

MAX_LOG_BLOCKS = 2000

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
            args.extend(["-n", str(lines)])
        args.extend(["-o", "short-iso"])
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
        self.act_pause = QtGui.QAction("Pause", self)
        self.act_pause.setCheckable(True)
        self.act_pause.triggered.connect(self.toggle_pause)
        toolbar.addAction(self.act_pause)
        act_clear = QtGui.QAction("Clear", self)
        act_clear.triggered.connect(self.text.clear)
        toolbar.addAction(act_clear)
        act_stop = QtGui.QAction("Stop", self)
        act_stop.triggered.connect(self.stop_stream)
        toolbar.addAction(act_stop)
        self._paused = False
        self._stopped = False

    def on_output(self):
        if self._paused or self._stopped:
            self.proc.readAllStandardOutput()
            return
        data = self.proc.readAllStandardOutput().data().decode(errors="replace")
        if not data:
            return
        self.text.appendPlainText(data)
        self._trim_buffer()
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def on_finished(self):
        if not self._stopped:
            self.text.appendPlainText("\n[log stream ended]")

    def copy_all(self):
        self.text.selectAll()
        self.text.copy()

    def toggle_pause(self, checked: bool):
        self._paused = checked
        if checked:
            self.text.appendPlainText("\n[log stream paused]")
        else:
            self.text.appendPlainText("\n[log stream resumed]")
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def stop_stream(self):
        if self._stopped:
            return
        self._stopped = True
        if self.proc.state() == QtCore.QProcess.Running:
            self.proc.terminate()
            if not self.proc.waitForFinished(2000):
                self.proc.kill()
        self.text.appendPlainText("\n[log stream stopped]")
        self.act_pause.setChecked(False)
        self._paused = False

    def _trim_buffer(self, max_blocks: int = MAX_LOG_BLOCKS) -> None:
        doc = self.text.document()
        if doc.blockCount() <= max_blocks:
            return
        cursor = QtGui.QTextCursor(doc)
        cursor.beginEditBlock()
        while doc.blockCount() > max_blocks:
            cursor.setPosition(0)
            cursor.movePosition(QtGui.QTextCursor.EndOfBlock, QtGui.QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        cursor.endEditBlock()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self.proc.state() == QtCore.QProcess.Running:
                self.proc.kill()
        except Exception:
            pass
        return super().closeEvent(event)
