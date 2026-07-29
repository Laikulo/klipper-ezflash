from __future__ import annotations

import re
import shlex
import subprocess
import time
from typing import Any, Dict, List, Optional

from .fault import EZFException
from .util import find_tool


def _run_cmd(cmd: List[str], timeout: int = 10) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True)
        return p.stdout
    except Exception as e:
        raise EZFException(f"Command failed: {' '.join(cmd)}", e)


def scan_bootloader_devices(mcu_arch: str) -> List[Dict[str, Any]]:
    """Return a list of detected bootloader devices for the given MCU architecture.

    The returned dicts are pragmatic and may contain raw output and minimal parsed fields.
    """
    arch = (mcu_arch or "").lower()
    found: List[Dict[str, Any]] = []

    if "stm32" in arch:
        tool = find_tool("dfu-util")
        if not tool:
            raise EZFException("dfu-util not found on PATH; required for STM32 DFU detection")
        out = _run_cmd([str(tool), "--list"])  # dfu-util --list
        for line in out.splitlines():
            trimmed = line.strip()
            if not trimmed:
                continue
            # Only treat actual DFU device entries as valid bootloader candidates.
            # Ignore generic status messages such as "dfu-util 0.11" or "No DFU capable USB device available".
            if re.match(r"^Found DFU:", trimmed, re.IGNORECASE):
                found.append({"type": "stm32-dfu", "raw": trimmed})
            elif re.search(r"\[[0-9a-fA-F]{4}:[0-9a-fA-F]{4}\]", trimmed) and "ver=" in trimmed:
                found.append({"type": "stm32-dfu", "raw": trimmed})
            # dfu-util may report access errors while still listing the VID:PID
            # (e.g. "Cannot open DFU device 0483:df11 ... LIBUSB_ERROR_ACCESS").
            # Treat any line containing the VID:PID as a valid DFU candidate.
            elif re.search(r"0483:df11", trimmed, re.IGNORECASE):
                found.append({"type": "stm32-dfu", "raw": trimmed})
        # If dfu-util didn't yield any usable lines (often due to access
        # permission errors), fall back to `lsusb` which still lists VID:PID.
        if not found:
            ls_tool = find_tool("lsusb")
            if ls_tool:
                try:
                    lsout = _run_cmd([str(ls_tool)])
                    for line in lsout.splitlines():
                        if "0483:df11" in line.lower() or (
                            "stmicroelectronics" in line.lower() and "dfu" in line.lower()
                        ):
                            found.append({"type": "stm32-dfu-lsusb", "raw": line.strip()})
                except Exception:
                    # ignore lsusb failures — we still return whatever dfu-util found
                    pass
        return found

    if "rp2040" in arch or "rp2" in arch or "rp" in arch:
        tool = find_tool("lsusb")
        if not tool:
            raise EZFException("lsusb not found on PATH; required for RP2040 detection")
        out = _run_cmd([str(tool)])
        for line in out.splitlines():
            if "2e8a:0003" in line.lower() or "2e8a:0003" in line:
                found.append({"type": "rp2040-katapult", "raw": line.strip()})
        return found

    # Unknown arch: try dfu-util and lsusb as a fallback
    for tool_name in ("dfu-util", "lsusb"):
        tool = find_tool(tool_name)
        if not tool:
            continue
        out = _run_cmd([str(tool)])
        if out:
            found.append({"type": f"{tool_name}-fallback", "raw": out.strip()})
    return found


def validate_device(device_info: Dict[str, Any], board_mcu_arch: str) -> bool:
    """Validate that a detected device matches the expected board MCU arch.

    Returns True if ok; raises EZFException on mismatch.
    """
    detected_type = device_info.get("type", "").lower()
    expected = (board_mcu_arch or "").lower()

    if "stm32" in expected and "stm32" in detected_type:
        return True
    if ("rp2040" in expected or "rp" in expected) and ("rp2040" in detected_type or "rp" in detected_type):
        return True

    raise EZFException(
        f"Detected device type '{detected_type}' does not match expected board MCU '{board_mcu_arch}'"
    )


def verify_bootloader_connected(mcu_arch: str, timeout: int = 30) -> Dict[str, Any]:
    """Poll for a bootloader device to appear and remain present.

    If no device is found immediately, polls up to `timeout` seconds. Once found,
    waits 1-2s and checks it is still present. Returns the first matching device_info.
    """
    deadline = time.time() + timeout
    first = None
    while time.time() < deadline:
        devices = scan_bootloader_devices(mcu_arch)
        if devices:
            first = devices[0]
            # quick re-check after short sleep
            time.sleep(1.5)
            devices2 = scan_bootloader_devices(mcu_arch)
            if any(d.get("raw") == first.get("raw") for d in devices2):
                return first
            else:
                raise EZFException("Bootloader device was detected but did not remain present (cable/click)")
        time.sleep(1)

    raise EZFException(f"No bootloader device detected for MCU '{mcu_arch}' within {timeout}s")
