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


# ---------------------------------------------------------------------------
# Command builders.
#
# These do the path resolution / precondition checks (raising EZFException
# on anything missing) and hand back a plain argv list plus the cwd it
# should run in — no subprocess is spawned here. This lets any caller drive
# execution however it wants: the blocking `_run` helper below for
# synchronous/CLI use, or an async streaming runner (e.g. Textual's
# `asyncio.create_subprocess_exec`-based `run_command`) for a UI that wants
# to show live output. Keeping path/arg resolution here means the UI layer
# never has to duplicate this logic or guess at flag names.
# ---------------------------------------------------------------------------

MAKE_CLEAN_ARGS = ["make", "clean"]
MAKE_BUILD_ARGS = ["make", "-j", "--output-sync=target"]

STM32_SUCCESS_PATTERNS = ["File downloaded successfully"]


def katapult_output_path(katapult_path: Path) -> Path:
    return Path(katapult_path) / "out" / "katapult.bin"


def klipper_output_candidates(klipper_path: Path) -> list[Path]:
    out_dir = Path(klipper_path) / "out"
    return [out_dir / "klipper.bin", out_dir / "klipper.elf", out_dir / "klipper.elf.hex"]


def klipper_output_path(klipper_path: Path) -> Optional[Path]:
    for candidate in klipper_output_candidates(klipper_path):
        if candidate.exists():
            return candidate
    return None


def build_rp2040_flash_cmd(
    bootloader_bin: Path,
    katapult_path: Optional[Path] = None,
    flash_device: str = "2e8a:0003",
) -> tuple[list[str], Path]:
    """Return (argv, cwd) for flashing an rp2040 katapult bootloader."""
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    if not bootloader_bin.exists():
        raise EZFException(f"Katapult binary not found at {bootloader_bin}")
    argv = ["make", "--output-sync=target", "flash", f"FLASH_DEVICE={flash_device}", "NOSUDO=1"]
    return argv, kp


def build_stm32_flash_cmd(
    bootloader_bin: Path,
    katapult_path: Optional[Path] = None,
    device_vidpid: str = "0483:df11",
) -> list[str]:
    """Return argv for flashing an stm32 katapult bootloader via dfu-util.

    Note: success for this command is determined by output containing one of
    STM32_SUCCESS_PATTERNS, *not* solely by exit code — dfu-util can exit
    non-zero on a successful flash. Callers must check output against
    STM32_SUCCESS_PATTERNS themselves (see `_run_with_success_check` for the
    synchronous reference implementation).
    """
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for flashing")
    dfu = find_tool("dfu-util")
    if not dfu:
        raise EZFException("dfu-util not found; required to flash STM32 bootloader")
    if not bootloader_bin.exists():
        raise EZFException(f"Katapult binary not found at {bootloader_bin}")
    return [str(dfu), "-a", "0", "-s", "0x08000000:mass-erase:force:leave", "-D", str(bootloader_bin), "-d", device_vidpid]


def build_flashtool_cmd(
    katapult_path: Optional[Path],
    firmware_bin: Path,
    interface: str,
    identifier: str,
    can_iface: Optional[str] = None,
    interface_speed: Optional[int] = None,
) -> list[str]:
    """Return argv for flashing klipper firmware via katapult's flashtool.py.

    `interface` is one of "can", "uart", or "serial" (a bare USB katapult
    connection, which auto-negotiates and needs no speed argument).
    `identifier` is a UUID (CAN) or serial device path (UART/serial).

    `interface_speed` is the configured CAN bitrate or UART baud rate.
    It's passed to flashtool.py as `-b` for UART.

    NOTE: it is NOT currently passed through for CAN — flashtool.py talks
    to an already-up CAN network interface, and as far as we've
    confirmed, the bitrate there is a property of the SocketCAN interface
    itself (`ip link set canX type can bitrate <rate>`) rather than a
    flashtool.py flag. If flashtool.py does accept its own CAN bitrate
    override, wire it in here once that's confirmed.
    """
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for firmware flash")
    flashtool = kp / "scripts" / "flashtool.py"
    if not flashtool.exists():
        raise EZFException(f"flashtool.py not found at {flashtool}")
    py = Path(os.environ.get("KBOARD_KLIPPY_PYTHON", "/usr/bin/python3"))
    if interface == "can":
        chosen = can_iface or os.environ.get("KBOARD_CAN_INTERFACE") or "can0"
        return [str(py), str(flashtool), "-i", chosen, "-u", identifier, "-f", str(firmware_bin)]
    elif interface == "uart":
        if interface_speed is None:
            raise EZFException("UART firmware flash requires an interface_speed (baud rate)")
        return [str(py), str(flashtool), "-d", identifier, "-b", str(interface_speed), "-f", str(firmware_bin)]
    else:
        return [str(py), str(flashtool), "-d", identifier, "-f", str(firmware_bin)]


# ---------------------------------------------------------------------------
# Blocking entry points (CLI / non-UI callers, existing tests). Each is now
# just: resolve argv via the builder above, then run it synchronously.
# ---------------------------------------------------------------------------

def compile_katapult(katapult_path: Optional[Path] = None) -> Path:
    kp = Path(katapult_path) if katapult_path else find_katapult()
    if not kp:
        raise EZFException("Katapult checkout not found for compilation")
    _run(MAKE_CLEAN_ARGS, cwd=kp)
    _run(MAKE_BUILD_ARGS, cwd=kp)
    out = katapult_output_path(kp)
    if not out.exists():
        raise EZFException(f"Expected katapult binary not found at {out}")
    return out


def compile_firmware(klipper_path: Optional[Path] = None) -> Path:
    kp = Path(klipper_path) if klipper_path else find_klipper()
    _run(MAKE_CLEAN_ARGS, cwd=kp)
    _run(MAKE_BUILD_ARGS, cwd=kp)
    output = klipper_output_path(kp)
    if output is None:
        candidates = ", ".join(str(p) for p in klipper_output_candidates(kp))
        raise EZFException(f"Expected klipper firmware at one of {candidates}")
    return output


def flash_rp2040_katapult(bootloader_bin: Path, katapult_path: Optional[Path] = None, flash_device: str = "2e8a:0003") -> None:
    argv, cwd = build_rp2040_flash_cmd(bootloader_bin, katapult_path, flash_device)
    _run(argv, cwd=cwd)


def flash_stm32_katapult(bootloader_bin: Path, katapult_path: Optional[Path] = None, device_vidpid: str = "0483:df11") -> None:
    argv = build_stm32_flash_cmd(bootloader_bin, katapult_path, device_vidpid)
    _run_with_success_check(argv, success_patterns=STM32_SUCCESS_PATTERNS)


def flash_firmware_via_flashtool(katapult_path: Optional[Path], firmware_bin: Path, interface: str, identifier: str, can_iface: Optional[str] = None, interface_speed: Optional[int] = None) -> None:
    """Flash klipper firmware using katapult's flashtool.py.

    `interface` is one of "can", "uart", or "serial". `identifier` is a
    UUID or serial device path as required. `interface_speed` is the CAN
    bitrate or UART baud rate — see build_flashtool_cmd for details.
    """
    argv = build_flashtool_cmd(katapult_path, firmware_bin, interface, identifier, can_iface, interface_speed)
    _run(argv)
