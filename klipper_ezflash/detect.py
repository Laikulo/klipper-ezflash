from __future__ import annotations

import os
import re
import sys
import time
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from .fault import EZFException
from .util import find_tool, find_katapult, run_captured

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


class _DirectoryChangeWaiter(FileSystemEventHandler):
    """Wakes on any create/delete/move event in the watched directory.
    Used purely as a signal to re-poll — the actual answer always comes
    from re-checking the directory directly, never from this handler's
    own state, so a missed inotify event costs one extra wait cycle
    instead of a false "not found" result.
    """
    def __init__(self):
        self.changed = threading.Event()

    def on_created(self, event):
        self.changed.set()

    def on_deleted(self, event):
        self.changed.set()

    def on_moved(self, event):
        self.changed.set()


def _wait_until(condition_fn: Callable[[], bool], timeout: float) -> bool:
    """Block until `condition_fn()` returns True, or `timeout` elapses.
    Re-checks immediately, then again on every /dev/serial/by-id change
    (via inotify) or timeout, always by calling `condition_fn()` directly
    rather than trusting the watcher's own bookkeeping.
    """
    base = Path("/dev/serial/by-id")
    if condition_fn():
        return True

    deadline = time.monotonic() + timeout

    if not base.exists():
        while time.monotonic() < deadline and not base.exists():
            time.sleep(0.05)
        if not base.exists():
            return False

    handler = _DirectoryChangeWaiter()
    observer = Observer()
    observer.schedule(handler, str(base), recursive=False)
    observer.start()
    try:
        while True:
            if condition_fn():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return condition_fn()
            handler.changed.wait(remaining)
            handler.changed.clear()
    finally:
        observer.stop()
        observer.join()


def wait_for_serial_device(predicate: Callable[[Path], bool], timeout: float = 5.0) -> Optional[Path]:
    """Block until exactly one /dev/serial/by-id entry matching
    `predicate` appears (or already exists), or until `timeout` elapses.
    Returns the matching Path, or None on timeout.

    Raises EZFException if more than one matching device is present at
    once — the same "can't safely pick one" reasoning as device.py's
    bootloader-mode detection (verify_bootloader_connected): silently
    taking the first match risked acting on the wrong board whenever two
    matching devices were connected simultaneously.
    """
    result: dict = {}

    def condition():
        match = detect_usb_serial(predicate)
        if len(match) > 1:
            raw_list = "\n".join(f"  - {p}" for p in match)
            raise EZFException(
                f"Multiple matching serial devices detected at once:\n{raw_list}\n\n"
                "Remove the extra device(s) so only the board you want "
                "remains connected, then retry."
            )
        if match:
            result["value"] = match[0]
            return True
        return False

    return result.get("value") if _wait_until(condition, timeout=timeout) else None


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


def wait_for_katapult_device(
    known: Optional[Iterable[Path]] = None,
    timeout: float = 5.0,
) -> Optional[Path]:
    """Wait for the board to re-enumerate into katapult mode after
    flashing the bootloader.

    If `known` is given (a snapshot of katapult_ devices taken before the
    bootloader flash), waits for a device that wasn't in that snapshot —
    this is what actually matters when another board's katapult device
    is already sitting connected from an earlier session: without this,
    that leftover device and the newly-flashed one both match the same
    predicate and get reported as an ambiguous "multiple devices"
    situation, even though only the new one is relevant to this flash. If
    omitted, matches any katapult_ device (only safe when at most one
    could plausibly be connected at a time).
    """
    predicate = lambda p: "katapult_" in p.name
    if known is not None:
        return wait_for_new_serial_device(predicate, known, timeout=timeout)
    return wait_for_serial_device(predicate, timeout=timeout)


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

def list_serial_ports() -> List[Path]:
    """Return every serial device currently present, for the user to
    pick from manually.

    Used for UART interfaces: the connection there goes through a
    generic USB-serial adapter (FTDI, CH340, etc.) wired to the board's
    UART pins, which has no board-specific "katapult_"/"Klipper_" name
    to auto-match against the way a board's own native USB port does —
    there's no way to tell which serial adapter is the right one without
    asking.
    """
    base = Path("/dev/serial/by-id")
    if base.exists():
        ports = sorted(base.iterdir())
        if ports:
            return ports
    # Fall back to /dev/ttyUSB*/ttyACM* if by-id is empty — some older
    # adapters/drivers don't populate by-id at all.
    dev = Path("/dev")
    if not dev.exists():
        return []
    return sorted(p for p in dev.glob("tty*") if p.name.startswith(("ttyUSB", "ttyACM")))


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
    out = run_captured(cmd, timeout=timeout)

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


def wait_for_new_can_interface(known: Iterable[str], timeout: float = 15.0) -> Optional[str]:
    """Poll for a CAN network interface that is not already present in
    `known` (a snapshot taken before the flash was triggered).

    Used to identify a CAN-bridge board's own interface once it reboots
    into USB to CAN Bridge-mode firmware and enumerates as a new USB-CAN adapter
    — there's no serial-device equivalent to match against here, since
    the board no longer exposes a USB-serial device at all in this mode.

    Unlike wait_for_serial_device, this polls list_can_interfaces()
    directly on a short interval rather than watching via inotify —
    /sys/class/net doesn't get the same treatment as /dev/serial/by-id
    elsewhere in this file, and a new CAN interface appearing is a
    slower, rarer event where a plain poll loop is simple and sufficient.

    Raises EZFException if more than one new interface appears at once,
    for the same reason as wait_for_serial_device: silently picking one
    risks acting on the wrong device.
    """
    known_set = set(known)
    deadline = time.monotonic() + timeout
    while True:
        current = list_can_interfaces()
        new = [c for c in current if c not in known_set]
        if len(new) > 1:
            raw_list = "\n".join(f"  - {c}" for c in new)
            raise EZFException(
                f"Multiple new CAN interfaces appeared at once:\n{raw_list}\n\n"
                "Remove any other newly-connected CAN-bridge devices so only "
                "the board you're flashing is present, then retry."
            )
        if new:
            return new[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def wait_for_can_uuid(
    interface: str,
    want_type: str = "katapult",
    katapult_path: Optional[Path] = None,
    timeout: float = 30.0,
    query_timeout: float = 8.0,
) -> Optional[Tuple[str, str]]:
    """Poll a CAN interface for a device reporting the given firmware type
    ("katapult" or "klipper"), ignoring any other-typed entries entirely.

    This is what stops a board of the wrong type — an already-in-service
    klipper-mode board still sharing the same physical CAN bus while
    waiting for a bootloader, or vice versa, a device that hasn't
    finished rebooting into its newly-flashed firmware yet while waiting
    to confirm the flash — from ever being mistaken for the device
    actually being waited on right now. detect_can_uuid() itself returns
    every UUID it sees regardless of firmware type, so a caller that just
    took the first result could end up targeting/confirming the wrong
    device or the wrong lifecycle stage of the right device.

    Each individual flashtool.py query gets `query_timeout` seconds to
    actually scan the bus and respond — CAN node discovery isn't
    instant. An earlier version of this function tied the per-query
    timeout to a short poll interval (2s), which meant flashtool.py was
    being killed before it could ever report a UUID even though the
    device was genuinely there and a longer manual query found it fine.
    `timeout` is the overall budget across however many such queries fit
    within it; there's no separate sleep between attempts since each
    attempt's own runtime already paces the loop.

    Returns None on timeout (the caller should report "not found", not
    silently fall back to a wrong-typed entry). Raises EZFException if
    more than one matching-typed device is present at once — the same
    "can't safely pick one" reasoning as wait_for_serial_device.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            found = detect_can_uuid(
                katapult_path=katapult_path, interface=interface,
                timeout=max(1, int(min(query_timeout, remaining))),
            )
        except EZFException:
            found = []
        matching = [(u, fw) for u, fw in found if fw == want_type]
        if len(matching) > 1:
            raw_list = "\n".join(f"  - {u}" for u, _ in matching)
            raise EZFException(
                f"Multiple {want_type}-mode CAN devices detected at once:\n{raw_list}\n\n"
                "Remove the extra device(s) so only the board you want is present, then retry."
            )
        if matching:
            return matching[0]

