from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .fault import EZFException
from .util import find_katapult, find_klipper, find_tool


def _run(cmd: list, cwd: Optional[Path] = None) -> None:
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        raise EZFException(f"Command failed: {' '.join(cmd)}", e)


def _run_with_success_check(
    cmd: list,
    success_patterns: Optional[list[str]] = None,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    """Run a command that may return non-zero exit code but still succeed.

    Streams stdout/stderr live (merged) as the command runs, while also
    buffering it so it can be checked against `success_patterns` even if
    the process exits non-zero.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines = []
    for line in proc.stdout:
        output_lines.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()
    output = "".join(output_lines)

    if proc.returncode == 0:
        return subprocess.CompletedProcess(cmd, proc.returncode, output, "")

    if success_patterns:
        for pattern in success_patterns:
            if pattern in output:
                return subprocess.CompletedProcess(cmd, proc.returncode, output, "")

    raise subprocess.CalledProcessError(proc.returncode, cmd, output, "")


def compile_katapult(katapult_path: Optional[Path] = None) -> Path:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for compilation")
    _run(["make", "clean"], cwd=kp)
    _run(["make", "-j"], cwd=kp)
    out = kp / "out" / "katapult.bin"
    if not out.exists():
        raise EZFException(f"Expected katapult binary not found at {out}")
    return out


def compile_firmware(klipper_path: Optional[Path] = None) -> Path:
    kp = Path(klipper_path) if klipper_path else find_klipper()
    _run(["make", "clean"], cwd=kp)
    _run(["make", "-j"], cwd=kp)
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


def flash_rp2040_katapult(katapult_path: Optional[Path] = None, flash_device: str = "2e8a:0003") -> None:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    _run(["make", "-C", str(kp), "flash", f"FLASH_DEVICE={flash_device}"])


def flash_stm32_katapult( bootloader_bin: Path, katapult_path: Optional[Path] = None, device_vidpid: str = "0483:df11") -> None:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    dfu = find_tool("dfu-util")
    if not dfu:
        raise EZFException("dfu-util not found; required to flash STM32 bootloader")
    if not bootloader_bin.exists():
        raise EZFException(f"Katapult binary not found at {bootloader_bin}")
    _run_with_success_check(
        [str(dfu), "-a", "0", "-s", "0x08000000:mass-erase:force:leave", "-D", str(bootloader_bin), "-d", device_vidpid],
        success_patterns=["File downloaded successfully"],
    )


def flash_firmware_via_flashtool(katapult_path: Optional[Path], firmware_bin: Path, interface: str, identifier: str, can_iface: Optional[str] = None) -> None:
    """Flash klipper firmware using katapult's flashtool.py.

    `interface` is either a serial path or a host CAN interface name.
    `identifier` is a UUID or serial argument as required.
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
        _run([str(py), str(flashtool), "-i", chosen, "-u", identifier, "-f", str(firmware_bin)])
    else:
        # For USB/serial: pass the serial device path with -d
        _run([str(py), str(flashtool), "-d", identifier, "-f", str(firmware_bin)])
