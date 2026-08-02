from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .fault import EZFException
from .util import find_tool, find_katapult


def _wait_for_serial_device(
    filter_keyword: str,
    timeout: float = 5.0,
    poll_interval: float = 0.5,
    initial_delay: float = 0.0,
) -> Optional[Path]:
    """Poll for a USB serial device matching *filter_keyword*.

    Returns the resolved device path or ``None`` on timeout.
    """
    if initial_delay:
        time.sleep(initial_delay)

    deadline = time.time() + timeout
    while time.time() < deadline:
        devs = detect_usb_serial_by_id(filter_keyword=filter_keyword)
        if devs:
            return devs[0]
        time.sleep(poll_interval)
    return None


def wait_for_katapult_device(timeout: float = 5.0, poll_interval: float = 0.5) -> bool:
    """Wait for the board to re-enumerate into katapult mode after flashing."""
    result = _wait_for_serial_device(
        "katapult_", timeout=timeout, poll_interval=poll_interval, initial_delay=1.0,
    )
    return result is not None


def wait_for_klipper_device(timeout: float = 5.0, poll_interval: float = 0.5) -> Optional[Path]:
    """Wait for and return the path to a Klipper USB serial device after flashing."""
    return _wait_for_serial_device(
        "klipper_", timeout=timeout, poll_interval=poll_interval, initial_delay=1.0,
    )


def detect_katapult_serial_path(timeout: float = 5.0, poll_interval: float = 0.5) -> Optional[Path]:
    """Return the path to a Katapult USB serial device (no initial delay)."""
    return _wait_for_serial_device(
        "katapult_", timeout=timeout, poll_interval=poll_interval, initial_delay=0.0,
    )


def detect_klipper_serial_path(timeout: float = 5.0, poll_interval: float = 0.5) -> Optional[Path]:
    """Return the path to a Klipper USB serial device (no initial delay)."""
    return _wait_for_serial_device(
        "klipper_", timeout=timeout, poll_interval=poll_interval, initial_delay=0.0,
    )


def detect_usb_serial_by_id(filter_keyword: str = "katapult_") -> List[Path]:
    """Scan /dev/serial/by-id for entries containing `filter_keyword`.

    Returns a list of Paths to matching device nodes.
    """
    base = Path("/dev/serial/by-id")
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        if filter_keyword in p.name:
            out.append(p)
    return out


def _run_cmd(cmd: List[str], timeout: int = 10) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=STDOUT, timeout=timeout, text=True)
        return p.stdout or ""
    except Exception as e:
        raise EZFException(f"Failed running command: {' '.join(cmd)}", e)


def detect_can_uuid(katapult_path: Optional[Path] = None, interface: str = "can0", timeout: int = 10) -> List[Tuple[str, str]]:
    """Detect device UUIDs on a CAN interface using katapult's `flashtool.py`.

    Parses lines like ``Detected UUID: 66f03f24c715, Application: Katapult``.
    Returns a list of ``(uuid, firmware_type)`` tuples where *firmware_type* is
    one of: ``"katapult"``, ``"klipper"``, or ``"unknown"``.

    Firmware names are matched case-insensitively and ``CanBoot`` is treated as
    equivalent to ``Katapult``.
    """
    kp = Path(katapult_path) if katapult_path else None
    if kp is None:
        try:
            kp = find_katapult()
        except Exception:
            kp = None

    if kp is None:
        raise EZFException("Could not locate katapult checkout for CAN UUID detection")

    flashtool = kp / "scripts" / "flashtool.py"
    if not flashtool.exists():
        raise EZFException(f"flashtool.py not found at {flashtool}")

    py = os.environ.get("KBOARD_KLIPPY_PYTHON") or sys.executable or "/usr/bin/python3"

    # Regex to parse lines like "Detected UUID: 66f03f24c715, Application: Katapult"
    detected_uuid_re = re.compile(
        r"Detected\s+UUID:\s*([0-9a-fA-F]+)(?:,\s*Application:\s*(\w+))?",
        re.IGNORECASE,
    )

    found: List[Tuple[str, str]] = []  # list of (uuid, firmware_type)
    seen_uuids: set[str] = set()
    
    cmd = [py, str(flashtool), "-i", interface, "-q"]
    out = _run_cmd(cmd, timeout=timeout)

    for m in detected_uuid_re.finditer(out):
        uuid_val = m.group(1).lower()
        app_name = (m.group(2) or "").lower() if m.lastindex >= 2 else ""
        fw_type = _resolve_firmware_type(app_name)

        if uuid_val not in seen_uuids:
            seen_uuids.add(uuid_val)
            found.append((uuid_val, fw_type))

    if not found:
        raise EZFException(f"No CAN UUIDs detected with command: {' '.join(cmd)}\nOutput:\n{out}")

    return found


def _resolve_firmware_type(text: str) -> str:
    """Resolve a firmware type from lowercase text.

    Matches *katapult* or *canboot* (case-insensitive) → ``"katapult"``,
    *klipper* → ``"klipper"``, anything else → ``"unknown"``.
    """
    if "katapult" in text or "canboot" in text:
        return "katapult"
    if "klipper" in text:
        return "klipper"
    return "unknown"


def list_can_interfaces() -> List[str]:
    """Return a list of candidate CAN network interface names present on the host.

    This implementation checks /sys/class/net for names starting with 'can' or 'vcan'.
    """
    netdir = Path("/sys/class/net")
    if not netdir.exists():
        return []
    out = []
    for p in netdir.iterdir():
        name = p.name
        if name.startswith("can") or name.startswith("vcan") or name.startswith("slcan"):
            out.append(name)
    return sorted(out)
