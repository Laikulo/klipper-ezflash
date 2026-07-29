from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional

from .fault import EZFException
from .util import find_katapult, find_klipper, find_tool


def _run(cmd: list, cwd: Optional[Path] = None) -> None:
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        raise EZFException(f"Command failed: {' '.join(cmd)}", e)


def compile_katapult(katapult_path: Optional[Path] = None) -> Path:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for compilation")
    _run(["make", "-C", str(kp), "clean"])
    _run(["make", "-C", str(kp)])
    out = kp / "out" / "katapult.bin"
    if not out.exists():
        raise EZFException(f"Expected katapult binary not found at {out}")
    return out


def compile_firmware(klipper_path: Optional[Path] = None) -> Path:
    kp = Path(klipper_path) if klipper_path else find_klipper()
    # This implementation assumes an existing build target; the real project may require env setup
    _run(["make", "-C", str(kp), "clean"])
    _run(["make", "-C", str(kp)])
    out_dir = kp / "out"
    possible_outputs = [
        out_dir / "klipper.bin",
        out_dir / "klipper.elf",
        out_dir / "klipper.elf.hex",
    ]
    for output in possible_outputs:
        if output.exists():
            return output
    raise EZFException(
        f"Expected klipper firmware at one of {', '.join(str(p) for p in possible_outputs)}"
    )


def locate_firmware(klipper_path: Optional[Path] = None) -> Optional[Path]:
    """Locate an existing compiled Klipper firmware without invoking a build.

    Returns the Path to the artifact if present, otherwise None.
    """
    kp = Path(klipper_path) if klipper_path else find_klipper()
    out_dir = kp / "out"
    possible_outputs = [
        out_dir / "klipper.bin",
        out_dir / "klipper.elf",
        out_dir / "klipper.elf.hex",
    ]
    for output in possible_outputs:
        if output.exists():
            return output
    return None


def flash_rp2040_katapult(katapult_path: Optional[Path] = None, flash_device: str = "2e8a:0003") -> None:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    _run(["make", "-C", str(kp), "flash", f"FLASH_DEVICE={flash_device}"])


def flash_stm32_katapult(katapult_path: Optional[Path] = None, device_vidpid: str = "0483:df11") -> None:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    dfu = find_tool("dfu-util")
    if not dfu:
        raise EZFException("dfu-util not found; required to flash STM32 bootloader")
    binpath = kp / "out" / "katapult.bin"
    if not binpath.exists():
        raise EZFException(f"Katapult binary not found at {binpath}")
    # user may need sudo depending on permissions; we attempt direct call
    _run([str(dfu), "-a", "0", "-s", "0x08000000:mass-erase:force:leave", "-D", str(binpath), "-d", device_vidpid])


def flash_firmware_via_flasktool(katapult_path: Optional[Path], klipper_bin: Path, interface: str, identifier: str, can_iface: Optional[str] = None) -> None:
    """Flash klipper firmware using katapult's flashtool.py.

    `interface` is either a serial path or 'can0'. `identifier` is a UUID or serial argument as required.
    """
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for firmware flash")
    flashtool = kp / "scripts" / "flashtool.py"
    if not flashtool.exists():
        raise EZFException(f"flashtool.py not found at {flashtool}")
    py = Path(os.environ.get("KBOARD_KLIPPY_PYTHON", "/usr/bin/python3"))
    if interface == "can":
        # For CAN: supply the CAN network interface with -i (e.g. can0)
        # and the device UUID with -u.
        chosen = can_iface or os.environ.get("KBOARD_CAN_INTERFACE") or "can0"
        _run([str(py), str(flashtool), "-i", chosen, "-u", identifier, "-f", str(klipper_bin)])
    else:
        # For USB/serial: pass the serial device path with -d
        _run([str(py), str(flashtool), "-d", identifier, "-f", str(klipper_bin)])
