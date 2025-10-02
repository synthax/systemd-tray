# Systemd Tray

A Qt system tray companion for your user-level systemd services. It watches whatever units you select, makes their state visible at a glance, and gives you one-click start/stop/restart plus an inline `journalctl` tail when you need to peek at logs.

## Prerequisites
- Linux desktop running a systemd user session
- Python 3.9+
- Poetry for dependency management/builds

## Setup & Run
```bash
poetry install
poetry run systemd-tray
```

The tray icon appears once Qt starts. Left-click opens the services panel; right-click reveals **Manage services…**, **Reload config**, and **Quit**.

## Configure Services
Systemd Tray looks for `~/.config/systemd-tray/services.yaml`. On first launch it writes a minimal file, but you can also copy the template:

```bash
mkdir -p ~/.config/systemd-tray
cp config/services.yaml.example ~/.config/systemd-tray/services.yaml
```

Use the in-app **Manage services…** dialog to add/remove user units discovered from `systemctl --user`. Each entry stores a friendly name, unit id, and optional log settings.
