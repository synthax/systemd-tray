from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional, Set

from PySide6 import QtCore

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
    "app-",
)


@dataclass
class ServiceCandidate:
    unit: str
    state: str
    description: str
    fragment_path: Optional[str]
    hidden: bool = False

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

class _SystemctlRunnable(QtCore.QRunnable):
    def __init__(self, backend: "SystemdBackend", action: str, unit: str, args: List[str], timeout: int = 10):
        super().__init__()
        self.backend = backend
        self.action = action
        self.unit = unit
        self.args = args
        self.timeout = timeout

    def run(self) -> None:
        cmd = ["systemctl", "--user", *self.args]
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            stdout = (cp.stdout or "").strip()
            stderr = (cp.stderr or "").strip()
        except subprocess.TimeoutExpired:
            self.backend.commandFinished.emit(self.unit, self.action, False, "Command timed out")
            return
        except Exception as exc:  # pragma: no cover
            self.backend.commandFinished.emit(self.unit, self.action, False, str(exc))
            return

        if self.action == "status":
            status = stdout or stderr or "unknown"
            self.backend.statusFetched.emit(self.unit, status)
        else:
            success = cp.returncode == 0
            message = stdout if success else (stderr or stdout)
            self.backend.commandFinished.emit(self.unit, self.action, success, message)

class SystemdBackend(QtCore.QObject):
    statusFetched = QtCore.Signal(str, str)
    commandFinished = QtCore.Signal(str, str, bool, str)

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self.pool = QtCore.QThreadPool(self)
        self._services_cache: Optional[tuple[float, List[ServiceCandidate]]] = None
        self.services_cache_ttl = 3.0

    def request_status(self, unit: str) -> None:
        self._start_task("status", unit, ["is-active", unit], timeout=6)

    def start_unit(self, unit: str) -> None:
        self._start_task("start", unit, ["start", unit])

    def stop_unit(self, unit: str) -> None:
        self._start_task("stop", unit, ["stop", unit])

    def restart_unit(self, unit: str) -> None:
        self._start_task("restart", unit, ["restart", unit])

    def reload_daemon(self) -> None:
        self._start_task("daemon-reload", "daemon", ["daemon-reload"], timeout=15)

    def _start_task(self, action: str, unit: str, args: List[str], timeout: int = 10) -> None:
        runnable = _SystemctlRunnable(self, action, unit, args, timeout)
        self.pool.start(runnable)

    def list_services(
        self,
        include_hidden: bool = False,
        required_units: Optional[Set[str]] = None,
        force_refresh: bool = False,
    ) -> List[ServiceCandidate]:
        now = time.monotonic()
        if not force_refresh and self._services_cache is not None:
            ts, services = self._services_cache
            if now - ts < self.services_cache_ttl:
                return services
        services = self.___list_user_services(include_hidden=include_hidden, required_units=required_units)
        self._services_cache = (now, services)
        return services
    
    def ___list_user_services(self, include_hidden: bool = False, required_units: Optional[Set[str]] = None) -> List[ServiceCandidate]:
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
            expose = self._should_expose_unit(unit, state, frag)
            must_include = required_units and unit in required_units
            if expose or include_hidden or must_include:
                candidates.append(ServiceCandidate(unit=unit, state=state, description=desc, fragment_path=frag, hidden=not expose))
        return candidates

    def _should_expose_unit(self, unit: str, state: str, fragment_path: Optional[str]) -> bool:
        if state not in MANAGEABLE_STATES:
            return False
        if unit.endswith("@.service"):
            return False
        if unit.endswith("@autostart.service"):
            return False
        lowered = unit.lower()
        for prefix in DEFAULT_EXCLUDE_PREFIXES:
            if lowered.startswith(prefix):
                return False
        return True
