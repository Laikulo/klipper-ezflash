from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from .fault import EZFException
from .util import find_tool, find_katapult


def detect_usb_serial_by_id(filter_keyword: str = "klipper_") -> List[Path]:
    """Scan /dev/serial/by-id for entries containing `filter_keyword`.

    Returns a list of Paths to matching device nodes.
    """
    base = Path("/dev/serial/by-id")
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        if filter_keyword in p.name:
            try:
                resolved = (p.resolve() if p.is_symlink() else p)
            except OSError:
                resolved = p
            out.append(resolved)
    return out


def _run_cmd(cmd: List[str], timeout: int = 10) -> str:
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True)
        return p.stdout or ""
    except Exception as e:
        raise EZFException(f"Failed running command: {' '.join(cmd)}", e)


def detect_can_uuid(katapult_path: Optional[Path] = None, interface: str = "can0", timeout: int = 10) -> List[str]:
    """Detect device UUIDs on a CAN interface using katapult's `flashtool.py`.

    The implementation will try several candidate flashtool invocation styles and
    parse UUIDs from stdout. Returns a list of discovered UUID strings.
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

    # Candidate argument permutations to try; many versions of flashtool use different flags.
    candidate_args = [
        ["-i", interface, "--scan"],
        ["-i", interface, "-r"],
        ["-i", interface, "--list"],
        ["-i", interface, "--discover"],
        ["-i", interface],
    ]

    uuid_re = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    uuid32_re = re.compile(r"\b[0-9a-fA-F]{32}\b")

    combined_output = []
    found = []
    deadline = time.time() + timeout
    # Keep trying until timeout; rotate through candidate commands
    while time.time() < deadline and not found:
        for args in candidate_args:
            cmd = [py, str(flashtool)] + args
            out = _run_cmd(cmd, timeout=5)
            combined_output.append((" ".join(cmd), out))
            for m in uuid_re.findall(out):
                if m not in found:
                    found.append(m)
            for m in uuid32_re.findall(out):
                # convert 32hex -> dashed UUID if needed
                if len(m) == 32:
                    dashed = f"{m[0:8]}-{m[8:12]}-{m[12:16]}-{m[16:20]}-{m[20:32]}"
                    if dashed not in found:
                        found.append(dashed)
            if found:
                break
        if not found:
            time.sleep(1)

    if not found:
        msg = "No CAN UUIDs detected. Tried flashtool invocations:\n"
        for cmd, out in combined_output:
            msg += f"COMMAND: {cmd}\nOUTPUT:\n{out}\n---\n"
        raise EZFException(msg)

    return found


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

