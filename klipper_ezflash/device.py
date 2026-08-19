from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .fault import EZFException
from .util import find_tool, run_captured

# Each entry: (device_type, USB VID:PID substrings that identify it,
# architecture keywords a board's mcu_arch must contain a match for to
# be considered "this device is the one we're looking for").
#
# Scanning is intentionally NOT pre-filtered by the caller's expected
# arch (see scan_hardware_bootloader_devices) — we want to see every
# recognized bootloader device that's present, so verify_bootloader_
# connected can tell the difference between "two devices matching what
# I asked for" (ambiguous — can't safely pick one, must block) and "one
# matching device plus some other board's bootloader also happens to be
# plugged in" (not ambiguous, but worth a warning in case the wrong
# board was selected).
_BOOTLOADER_SIGNATURES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("stm32-dfu", ("0483:df11",), ("stm32",)),
    ("rp2040-picoboot", ("2e8a:0003",), ("rp2040", "rp")),
    ("rp2350-picoboot", ("2e8a:000f",), ("rp2350", "rp")),
)


def _arches_for_type(device_type: str) -> Tuple[str, ...]:
    for dtype, _usb_ids, archs in _BOOTLOADER_SIGNATURES:
        if dtype == device_type:
            return archs
    return ()


def _matches_arch(device_type: str, mcu_arch: str) -> bool:
    arch = (mcu_arch or "").lower()
    return any(a in arch for a in _arches_for_type(device_type))


def scan_hardware_bootloader_devices() -> List[Dict[str, Any]]:
    """Return every recognized bootloader-mode USB device currently
    present, regardless of architecture.

    Deliberately not filtered by expected arch here — see the module
    docstring above. Callers that only care about one architecture
    should filter the result themselves (or use
    verify_bootloader_connected, which does this and also handles
    ambiguity).
    """
    tool = find_tool("lsusb")
    if not tool:
        raise EZFException("lsusb not found on PATH; required for bootloader detection")

    out = run_captured([str(tool)])
    found: List[Dict[str, Any]] = []
    for line in out.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        lower_line = line.lower()

        for device_type, usb_ids, _archs in _BOOTLOADER_SIGNATURES:
            if any(usb_id in lower_line for usb_id in usb_ids):
                found.append({"type": device_type, "raw": trimmed})
                break
            # STM32 DFU devices don't always show the VID:PID string in a
            # form that matches cleanly — fall back to the vendor/mode
            # text lsusb prints for them.
            if device_type == "stm32-dfu" and "stmicroelectronics" in lower_line and "dfu" in lower_line:
                found.append({"type": device_type, "raw": trimmed})
                break

    return found


def validate_device(device_info: Dict[str, Any], board_mcu_arch: str) -> bool:
    """Validate that a detected device matches the expected board MCU arch.

    Returns True if ok; raises EZFException on mismatch.
    """
    detected_type = device_info.get("type", "")
    if _matches_arch(detected_type, board_mcu_arch):
        return True
    raise EZFException(
        f"Detected device type '{detected_type}' does not match expected board MCU '{board_mcu_arch}'"
    )


def _matching_and_other(devices: List[Dict[str, Any]], mcu_arch: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    matching = [d for d in devices if _matches_arch(d["type"], mcu_arch)]
    other = [d for d in devices if not _matches_arch(d["type"], mcu_arch)]
    return matching, other


def _ambiguous_error(devices: List[Dict[str, Any]], mcu_arch: str) -> EZFException:
    raw_list = "\n".join(f"  - {d['raw']}" for d in devices)
    return EZFException(
        f"Multiple bootloader devices matching '{mcu_arch}' were detected at once:\n"
        f"{raw_list}\n\n"
        "Remove the extra device(s) so only the board you want to flash "
        "remains connected, then retry."
    )


def check_bootloader_already_present(mcu_arch: str) -> Optional[Dict[str, Any]]:
    """One-shot, non-waiting check for whether a bootloader device
    matching `mcu_arch` is already connected (e.g. a power user who put
    the board into bootloader mode before starting the flash flow).

    Reuses the same matching/ambiguity logic as verify_bootloader_connected
    so "what counts as a matching/ambiguous device" is defined in exactly
    one place. Returns None if nothing matches yet — that's the normal
    case, not an error, and callers should fall through to showing the
    board-prep instructions and waiting via verify_bootloader_connected.

    Raises EZFException if more than one matching device is already
    present, for the same "can't safely pick one" reasoning as
    verify_bootloader_connected — no reason to wait for that to resolve
    itself, it needs the user to unplug the extra device regardless of
    which check catches it.
    """
    devices = scan_hardware_bootloader_devices()
    matching, _other = _matching_and_other(devices, mcu_arch)
    if len(matching) > 1:
        raise _ambiguous_error(matching, mcu_arch)
    return matching[0] if matching else None


def verify_bootloader_connected(mcu_arch: str, timeout: int = 30, settle_time: float = 1.5) -> Dict[str, Any]:
    """Wait for exactly one bootloader device matching `mcu_arch` to
    appear and remain present, returning its info dict.

    Raises EZFException if:
      - no matching device appears within `timeout`
      - more than one device matching `mcu_arch` is present at once —
        this is genuinely ambiguous, since we have no way to tell which
        one the user actually wants flashed, so it's always a hard stop
      - the matching device disappears (or a second one shows up) during
        the brief settle/recheck window

    A bootloader device of a *different* architecture being present at
    the same time is not ambiguous — it doesn't affect which device gets
    flashed — but it's easy to miss having accidentally left another
    board connected, or to have picked the wrong board in the app. That
    case is folded into the returned dict as `other_devices` (a
    possibly-empty list) rather than raised, so the caller can show it
    as a "double check you selected the right board" warning without
    blocking the flash.
    """
    deadline = time.time() + timeout
    last_other: List[Dict[str, Any]] = []
    while time.time() < deadline:
        devices = scan_hardware_bootloader_devices()
        matching, other = _matching_and_other(devices, mcu_arch)
        if other:
            last_other = other

        if len(matching) > 1:
            raise _ambiguous_error(matching, mcu_arch)

        if matching:
            first = matching[0]
            time.sleep(settle_time)
            devices2 = scan_hardware_bootloader_devices()
            matching2, other2 = _matching_and_other(devices2, mcu_arch)

            if len(matching2) > 1:
                raise _ambiguous_error(matching2, mcu_arch)
            if not any(d.get("raw") == first.get("raw") for d in matching2):
                raise EZFException("Bootloader device was detected but did not remain present (cable/click)")

            first["other_devices"] = other2
            return first

        time.sleep(1)

    if last_other:
        raw_list = "\n".join(f"  - {d['raw']}" for d in last_other)
        raise EZFException(
            f"No bootloader device detected for MCU '{mcu_arch}' within {timeout}s, "
            f"but a different-architecture bootloader device was seen:\n{raw_list}\n\n"
            "Double check you selected the right board — this may be the device you meant to flash."
        )
    raise EZFException(f"No bootloader device detected for MCU '{mcu_arch}' within {timeout}s")
