from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fault import EZFException


def _default_status_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "klipper-ezflash" / "status.json"


@dataclass
class SessionData:
    step_index: int = 0
    mcu_arch: Optional[str] = None
    interface: Optional[str] = None
    interface_speed: Optional[int] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    detected_devices: List[Dict[str, Any]] = field(default_factory=list)
    katapult_path: Optional[str] = None
    bootloader_bin_hash: Optional[str] = None
    firmware_bin_hash: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionData":
        return cls(
            step_index=int(data.get("step_index", 0)),
            mcu_arch=data.get("mcu_arch"),
            interface=data.get("interface"),
            interface_speed=data.get("interface_speed"),
            manufacturer=data.get("manufacturer"),
            model=data.get("model"),
            variant=data.get("variant"),
            detected_devices=data.get("detected_devices", []),
            katapult_path=data.get("katapult_path"),
            bootloader_bin_hash=data.get("bootloader_bin_hash"),
            firmware_bin_hash=data.get("firmware_bin_hash"),
            extra=data.get("extra", {}),
        )


def save_session(session: SessionData, path: Optional[Path] = None) -> None:
    path = Path(path) if path is not None else _default_status_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = session.to_dict()

    # Atomic write: write to tempfile in same dir then replace
    try:
        fd, tmp = tempfile.mkstemp(prefix="status-", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except Exception as e:
        raise EZFException("Failed to save session status", e)


def load_session(path: Optional[Path] = None) -> Optional[SessionData]:
    path = Path(path) if path is not None else _default_status_path()
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return SessionData.from_dict(data)
    except Exception as e:
        raise EZFException("Failed to load session status", e)


def clear_session(path: Optional[Path] = None) -> None:
    path = Path(path) if path is not None else _default_status_path()
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        raise EZFException("Failed to clear session status", e)
