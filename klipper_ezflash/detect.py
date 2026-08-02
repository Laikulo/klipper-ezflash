from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from .fault import EZFException
from .util import find_tool, find_katapult

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


def detect_usb_serial(predicate: Callable[[Path], bool]) -> List[Path]:
    """Scan /dev/serial/by-id for entries matching `predicate`."""
    base = Path("/dev/serial/by-id")
    if not base.exists():
        return []
    return [p for p in base.iterdir() if predicate(p)]


def extract_serial(device_path: Path) -> Optional[str]:
    """Extract the unique device serial from a /dev/serial/by-id entry.

    e.g. usb-katapult_rp2040_E6611CB697123A29-if00 -> "E6611CB697123A29"
    Works the same for Klipper_ entries, since the serial sits in the same
    position regardless of the katapult_/Klipper_ prefix or chip name.

    Not used to *match* devices across the katapult -> klipper transition
    (a board's klipper config may set a custom USB serial number that
    differs from what katapult reports) — kept around for display/logging
    purposes, e.g. showing the user which board's serial was detected.
    """
    match = re.search(r"_([0-9A-Fa-f]+)-if\d+$", device_path.name)
    return match.group(1) if match else None


class _DeviceWaiter(FileSystemEventHandler):
    def __init__(self, predicate: Callable[[Path], bool]):
        self.predicate = predicate
        self.found = threading.Event()
        self.result: Optional[Path] = None

    def _check(self, path: str):
        p = Path(path)
        if self.predicate(p):
            self.result = p
            self.found.set()

    def on_created(self, event):
        self._check(event.src_path)

    def on_moved(self, event):
        self._check(event.dest_path)


def wait_for_serial_device(predicate: Callable[[Path], bool], timeout: float = 5.0) -> Optional[Path]:
    """Block until a /dev/serial/by-id entry matching `predicate` appears
    (or already exists), or until `timeout` elapses. Returns the matching
    Path, or None on timeout.

    Uses inotify (via watchdog) to react the instant udev creates the entry,
    rather than a settle-delay followed by fixed-interval polling.
    """
    base = Path("/dev/serial/by-id")

    existing = detect_usb_serial(predicate)
    if existing:
        return existing[0]

    deadline = time.monotonic() + timeout

    if not base.exists():
        # inotify can't watch a path that doesn't exist yet. This is the one
        # spot that still polls, but only for the directory's own creation,
        # bounded by the caller's timeout.
        while time.monotonic() < deadline and not base.exists():
            time.sleep(0.05)
        if not base.exists():
            return None

    handler = _DeviceWaiter(predicate)
    observer = Observer()
    observer.schedule(handler, str(base), recursive=False)
    observer.start()
    try:
        # Catch anything that appeared in the gap between the first check
        # above and the watch actually being registered.
        existing = detect_usb_serial(predicate)
        if existing:
            return existing[0]
        remaining = deadline - time.monotonic()
        if remaining > 0:
            handler.found.wait(remaining)
    finally:
        observer.stop()
        observer.join()

    return handler.result


def wait_for_new_serial_device(
    predicate: Callable[[Path], bool],
    known: Iterable[Path],
    timeout: float = 5.0,
) -> Optional[Path]:
    """Wait for a device matching `predicate` that is NOT already present
    in `known` (a snapshot taken before the flash was triggered).

    Use this instead of matching by serial number whenever the firmware may
    assign the device a different USB serial than the bootloader did (e.g.
    a board with a custom CONFIG_USB_SERIAL_NUMBER set in its klipper
    config) — only cares that the device is newly-appeared, not what its
    identity turns out to be.
    """
    known_set = set(known)
    return wait_for_serial_device(
        lambda p: predicate(p) and p not in known_set,
        timeout=timeout,
    )


def wait_for_katapult_device(timeout: float = 5.0) -> Optional[Path]:
    """Wait for the board to re-enumerate into katapult mode after flashing."""
    return wait_for_serial_device(lambda p: "katapult_" in p.name, timeout=timeout)


def wait_for_klipper_device(
    known: Optional[Iterable[Path]] = None,
    timeout: float = 5.0,
) -> Optional[Path]:
    """Wait for a Klipper USB serial device.

    If `known` is given (a snapshot of Klipper_ devices taken before the
    flash), waits for a device that wasn't in that snapshot — safe even if
    the firmware assigns a different serial than the bootloader had. If
    omitted, matches any Klipper_ device (only safe when at most one
    Klipper device could plausibly be connected at a time).
    """
    predicate = lambda p: "Klipper_" in p.name
    if known is not None:
        return wait_for_new_serial_device(predicate, known, timeout=timeout)
    return wait_for_serial_device(predicate, timeout=timeout)

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
