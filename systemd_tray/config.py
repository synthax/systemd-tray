from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml optional
    yaml = None

CONFIG_DIR = Path.home() / ".config" / "systemd-tray"
CONFIG_PATH = CONFIG_DIR / "services.yaml"

DEFAULT_CONFIG = {
    "services": [
        {
            "name": "ComfyUI",
            "unit": "comfyui.service",
            "logs": {
                "follow": True,
                "lines": 200,
            },
        }
    ]
}

def ensure_config() -> Dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if yaml is None:
            CONFIG_PATH.write_text("""services:
  - name: ComfyUI
    unit: comfyui.service
    logs:
      follow: true
      lines: 200
""", encoding="utf-8")
        else:
            CONFIG_PATH.write_text(
                yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False),
                encoding="utf-8",
            )
    if yaml is None:
        services: List[Dict] = []
        name = unit = None
        lines = 200
        follow = True
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if s.startswith("- "):
                    if name and unit:
                        services.append({
                            "name": name,
                            "unit": unit,
                            "logs": {"follow": follow, "lines": lines},
                        })
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
            services.append({
                "name": name,
                "unit": unit,
                "logs": {"follow": follow, "lines": lines},
            })
        return {"services": services}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {"services": []}

def save_config(config: Dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {"services": config.get("services", [])}
    if yaml is None:
        lines = ["services:"]
        for svc in data["services"]:
            lines.append(f"  - unit: {svc.get('unit', '')}")
            name = svc.get("name")
            if name:
                lines.append(f"    name: {name}")
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
        CONFIG_PATH.write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )

def parse_open_actions(service: Dict) -> List[Dict[str, str]]:
    raw = service.get("open")
    if not raw:
        return []

    def _normalize(entry: Dict[str, str]) -> Optional[Dict[str, str]]:
        if "url" in entry:
            url = entry["url"].strip()
            if not url:
                return None
            label = entry.get("label") or "Open URL"
            return {"label": label, "url": url}
        if "command" in entry:
            cmd = entry["command"]
            if not cmd:
                return None
            label = entry.get("label") or "Run command"
            return {"label": label, "command": cmd}
        return None

    actions: List[Dict[str, str]] = []
    if isinstance(raw, str):
        url = raw.strip()
        if url:
            actions.append({"label": "Open", "url": url})
    elif isinstance(raw, dict):
        normalized = _normalize(raw)
        if normalized:
            actions.append(normalized)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                url = item.strip()
                if url:
                    actions.append({"label": "Open", "url": url})
            elif isinstance(item, dict):
                normalized = _normalize(item)
                if normalized:
                    actions.append(normalized)
    return actions
