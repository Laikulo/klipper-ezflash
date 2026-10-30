import getpass
import hashlib
import os
import re
import subprocess
import shutil
from functools import cache
from pathlib import Path

from importlib.resources import files

from typing import Any, Optional
from collections.abc import Collection

_KATAPULT_CLONE_URL = "https://github.com/Arksine/katapult.git"
_COMMON_KATAPULT_LOCATIONS = ["~/katapult", "~/Katapult", "/usr/src/katapult"]
_COMMON_KLIPPER_LOCATIONS = ["~/klipper", "~/Klipper", "/usr/src/klipper"]
_COMMON_KLIPPY_ENV_LOCATIONS = ["~/klippy-env", "~/Klippy-env"]


@cache
def find_klippy_env_python() -> Path | None:
    """Find klippy-env's own python interpreter (klipper's dedicated
    virtualenv).

    Needed for anything that imports klippy's own host-side modules
    (reactor, serialhdl, msgproto, ...) — those depend on a compiled C
    extension (chelper) built specifically for that environment, and
    klippy's own tools (e.g. console.py) are written/tested against it,
    not this app's own venv.

    Returns a Path to the python binary if found, or None if not present.
    """
    if override_path := os.environ.get("KBOARD_KLIPPY_ENV_PATH"):
        path = Path(override_path)
        if path.exists():
            return path
        else:
            raise ValueError("Specified KBOARD_KLIPPY_ENV_PATH does not exist")
    for location in _COMMON_KLIPPY_ENV_LOCATIONS:
        python_path = Path(location).expanduser() / "bin" / "python"
        if python_path.exists():
            return python_path
    return None


@cache
def find_katapult():
    """Find the katapult checkout on the system.

    Returns a Path to the katapult directory if found, or None if not present.
    Katapult is used for flashing Klipper bootloaders and is required for certain board operations.
    """
    if override_path := os.environ.get("KBOARD_KATAPULT_PATH"):
        path = Path(override_path)
        if path.exists():
            return path
        else:
            raise ValueError("Specified KBOARD_KATAPULT_PATH does not exist")
    for location in _COMMON_KATAPULT_LOCATIONS:
        path = Path(location).expanduser()
        # Check if it's a valid katapult checkout (has the repo's .git directory)
        if path.exists() and (path / ".git").exists():
            return path
    return None


def update_katapult(katapult_path: Path) -> Optional[str]:
    """Run `git pull` in an existing katapult checkout.

    Returns None on success, or a human-readable error message on
    failure — never raises, matching find_katapult()'s own "return, don't
    raise" convention for this kind of best-effort operation. Extracted
    from app.py's check_katapult() so the actual git operation is pure,
    UI-independent logic that can be called via asyncio.to_thread from
    Textual (or tested/used standalone) without any dialog coupling.
    """
    result = subprocess.run(
        ["git", "-C", str(katapult_path), "pull"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return (result.stderr or f"Pull failed with exit code {result.returncode}").strip()
    return None


def clone_katapult(target: Path) -> Optional[str]:
    """Clone katapult (from _KATAPULT_CLONE_URL) into `target`.

    Returns None on success, or a human-readable error message on
    failure. Same rationale as update_katapult() above.
    """
    result = subprocess.run(
        ["git", "clone", _KATAPULT_CLONE_URL, str(target)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return (result.stderr or f"Clone failed with exit code {result.returncode}").strip()
    return None


@cache
def find_klipper():
    if override_path := os.environ.get("KBOARD_KLIPPER_PATH"):
        path = Path(override_path)
        if path.exists():
            return path
        else:
            raise ValueError("Specified KBOARD_KLIPPER_PATH does not exist")
    for location in _COMMON_KLIPPER_LOCATIONS:
        if (path := Path(location).expanduser()).exists():
            return path
    raise RuntimeError("Could not find the klipper checkout")


def get_boards():
    if override_path := os.environ.get("KBOARD_BOARDS_PATH"):
        path = Path(override_path)
        if path.exists():
            return path.open()
        else:
            raise ValueError("Specified KBOARD_BOARDS_PATH does not exist")
    try:
        return files("klipper_ezflash.boards").joinpath("boards.json").open("r")
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    # Try other options here
    raise RuntimeError("Could not find the board database")


def cajole_collection(in_val: Any):
    if type(in_val) is str:
        return in_val
    elif (in_val is None) or isinstance(in_val, Collection):
        return in_val
    else:
        return (in_val,)


@cache
def find_tool(name: str) -> Path | None:
    """Locate a system tool by name. Returns a Path or None if not found.

    Checks `shutil.which` first, then a few common locations.
    """
    # Quick path via PATH
    if (p := shutil.which(name)):
        return Path(p)

    # Common locations to try
    common = [f"/usr/bin/{name}", f"/usr/local/bin/{name}", f"/opt/homebrew/bin/{name}"]
    for c in common:
        p = Path(c)
        if p.exists() and os.access(p, os.X_OK):
            return p

    return None


@cache
def table_munge(key: str, table):
    """
    Given a key, and a tuple[tuple[str]], return the first tuple that contains the string, otherwise return the string
    """
    for entry in table:
        if key in entry:
            return entry
    return key

def file_sha256(path: Path) -> str:
    """Return a hex SHA-256 digest of the file contents at *path*.

    Reads the file in 64 KiB chunks to keep memory usage bounded even for large binaries.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run_captured(cmd: list[str], timeout: int = 10) -> str:
    """Run `cmd`, merging stderr into stdout, and return the captured text.

    Shared by device.py (lsusb) and detect.py (flashtool.py -q) so both
    get identical subprocess-invocation behavior instead of two
    near-identical copies drifting apart — which is exactly what had
    happened: detect.py's own copy referenced a bare `STDOUT` name that
    was never imported, a latent NameError waiting to fire the first
    time detect_can_uuid() actually ran.
    """
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True)
        return p.stdout or ""
    except Exception as e:
        # Deferred import: fault.py isn't imported elsewhere in this
        # module, and util.py is imported very early by nearly everything
        # else — keeping this import local avoids adding a new top-level
        # dependency edge just for one error path.
        from .fault import EZFException
        raise EZFException(f"Command failed: {' '.join(cmd)}", e)


# --------------------------------------------------------------------------
# CAN host networking state — see https://canbus.esoterical.online/Getting_Started.html
# --------------------------------------------------------------------------

_CAN_UDEV_RULE_PATH = Path("/etc/udev/rules.d/10-can.rules")
_CAN_UDEV_RULE_CONTENT = 'SUBSYSTEM=="net", ACTION=="change|add", KERNEL=="can*"  ATTR{tx_queue_len}="128"\n'
_CAN_NETWORK_PATH = Path("/etc/systemd/network/25-can.network")


def _can_network_content(can_speed: int) -> str:
    return (
        "[Match]\n"
        "Name=can*\n"
        "\n"
        "[CAN]\n"
        f"BitRate={can_speed}\n"
        "\n"
        "[Link]\n"
        "RequiredForOnline=no\n"
    )


def _run_sudo(args: list[str], description: str) -> None:
    """Run args under sudo. Raises EZFException (labeled with description)
    on failure, so callers get a readable error instead of a bare
    CalledProcessError.
    """
    from .fault import EZFException
    try:
        subprocess.run(["sudo"] + args, capture_output=True, text=True, timeout=30, check=True)
    except subprocess.CalledProcessError as e:
        raise EZFException(f"{description} failed: {e.stderr or e}")
    except subprocess.TimeoutExpired:
        raise EZFException(f"{description} timed out")


def check_can_host_state(can_speed: int) -> list[str]:
    """Read-only check of the host's CAN networking setup against the
    steps at https://canbus.esoterical.online/Getting_Started.html.
    Returns a list of human-readable problems found — an empty list
    means everything already matches, nothing needs fixing.
    """
    problems: list[str] = []

    try:
        enabled = subprocess.run(
            ["systemctl", "is-enabled", "systemd-networkd"], capture_output=True, text=True
        ).stdout.strip()
        if enabled != "enabled":
            problems.append("systemd-networkd service is not enabled")
        active = subprocess.run(
            ["systemctl", "is-active", "systemd-networkd"], capture_output=True, text=True
        ).stdout.strip()
        if active != "active":
            problems.append("systemd-networkd service is not running")
    except Exception as e:
        problems.append(f"Could not check systemd-networkd status: {e}")

    if not _CAN_UDEV_RULE_PATH.exists():
        problems.append(f"{_CAN_UDEV_RULE_PATH} is missing (CAN tx_queue_len won't be set to 128)")
    else:
        try:
            if _CAN_UDEV_RULE_PATH.read_text() != _CAN_UDEV_RULE_CONTENT:
                problems.append(f"{_CAN_UDEV_RULE_PATH} exists but doesn't match the expected rule")
        except OSError as e:
            problems.append(f"Could not read {_CAN_UDEV_RULE_PATH}: {e}")

    if not _CAN_NETWORK_PATH.exists():
        problems.append(f"{_CAN_NETWORK_PATH} is missing (CAN bit rate won't be configured)")
    else:
        try:
            if _CAN_NETWORK_PATH.read_text() != _can_network_content(can_speed):
                problems.append(
                    f"{_CAN_NETWORK_PATH} exists but doesn't match the configured speed ({can_speed} bps)"
                )
        except OSError as e:
            problems.append(f"Could not read {_CAN_NETWORK_PATH}: {e}")

    # Live interfaces already up may be running at a different speed than
    # configured — distinct from the config files being wrong, since an
    # already-running network might belong to a different printer/setup
    # entirely and shouldn't be silently assumed broken.
    try:
        netdir = Path("/sys/class/net")
        if netdir.exists():
            for p in netdir.iterdir():
                if not p.name.startswith("can"):
                    continue
                out = subprocess.run(
                    ["ip", "-details", "link", "show", p.name], capture_output=True, text=True
                ).stdout
                m = re.search(r"bitrate\s+(\d+)", out)
                if m and int(m.group(1)) != can_speed:
                    problems.append(
                        f"Interface {p.name} is currently running at {m.group(1)} bps, "
                        f"not the configured {can_speed} bps"
                    )
    except Exception:
        pass  # best-effort — a failure here shouldn't block the rest of the check

    return problems


def fix_can_host_state(can_speed: int) -> None:
    """Apply fixes for whatever check_can_host_state() found wrong.
    Idempotent — safe to call even when some/all of this is already
    correct. Raises EZFException on the first command that fails.
    """
    if subprocess.run(
        ["systemctl", "is-enabled", "--quiet", "systemd-networkd"]
    ).returncode != 0:
        _run_sudo(["systemctl", "enable", "systemd-networkd"], "Enabling systemd-networkd")
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", "systemd-networkd"]
    ).returncode != 0:
        _run_sudo(["systemctl", "start", "systemd-networkd"], "Starting systemd-networkd")

    if subprocess.run(
        ["systemctl", "is-enabled", "--quiet", "systemd-networkd-wait-online.service"]
    ).returncode == 0:
        _run_sudo(
            ["systemctl", "disable", "systemd-networkd-wait-online.service"],
            "Disabling systemd-networkd-wait-online",
        )

    if not _CAN_UDEV_RULE_PATH.exists() or _CAN_UDEV_RULE_PATH.read_text() != _CAN_UDEV_RULE_CONTENT:
        _run_sudo(
            ["sh", "-c", f"cat > {_CAN_UDEV_RULE_PATH} <<'EOF'\n{_CAN_UDEV_RULE_CONTENT}EOF"],
            "Writing CAN udev rule",
        )

    network_content = _can_network_content(can_speed)
    if not _CAN_NETWORK_PATH.exists() or _CAN_NETWORK_PATH.read_text() != network_content:
        _run_sudo(
            ["sh", "-c", f"cat > {_CAN_NETWORK_PATH} <<'EOF'\n{network_content}EOF"],
            "Writing CAN network config",
        )

    _run_sudo(["udevadm", "control", "--reload-rules"], "Reloading udev rules")
    _run_sudo(["udevadm", "trigger"], "Triggering udev")
    _run_sudo(["networkctl", "reload"], "Reloading systemd-networkd config")


# --------------------------------------------------------------------------
# UART host state — see https://github.com/bigtreetech/CB1/blob/master/readme.md#overlays-settings
# for the armbianEnv.txt console= behavior this is built against.
# --------------------------------------------------------------------------

_ARMBIAN_ENV_PATH = Path("/boot/armbianEnv.txt")
_PI_CONFIG_PATHS = [Path("/boot/firmware/config.txt"), Path("/boot/config.txt")]
# Pi models where the PL011 UART shares pins with Bluetooth by default,
# requiring dtoverlay=disable-bt to free it up for a stable /dev/ttyAMA0.
_PI_BT_SHARING_MARKERS = ("Pi 3", "Pi 4", "Zero W", "Zero 2 W", "Compute Module 3", "Compute Module 4")


def _detect_uart_host_platform() -> tuple[str, Optional[Path]]:
    """Detect which boot-config mechanism applies to this host for UART
    setup. Returns (platform_id, boot_config_path):
      "armbian"       - /boot/armbianEnv.txt present (e.g. BTT CB1/Pi1)
      "pi-bt-shared"  - Raspberry Pi model where the UART shares pins with
                        Bluetooth (3B/3B+/4/Zero W/Zero 2 W/CM3/CM4)
      "pi-standalone" - other Raspberry Pi models (no BT contention)
      "unsupported"   - couldn't confidently classify; boot_config_path
                        will be None and boot-config checks are skipped
                        rather than guessed at
    A direct check against known markers, same interim approach as the
    rp2040 arch check in configurator.py, rather than a general
    JSON-driven platform database.
    """
    if _ARMBIAN_ENV_PATH.exists():
        return "armbian", _ARMBIAN_ENV_PATH

    try:
        model = Path("/proc/device-tree/model").read_text(errors="ignore").strip("\x00").strip()
    except OSError:
        model = ""

    if not model.startswith("Raspberry Pi"):
        return "unsupported", None

    config_path = next((p for p in _PI_CONFIG_PATHS if p.exists()), None)
    if config_path is None:
        return "unsupported", None

    if any(marker in model for marker in _PI_BT_SHARING_MARKERS):
        return "pi-bt-shared", config_path
    return "pi-standalone", config_path


def _uart_getty_device(platform_id: str) -> str:
    """The serial-getty device name to check/disable for a given platform.
    Armbian/CB1 exposes UART0 as /dev/ttyS0 (confirmed against BTT's own
    CB1 docs); Pi's primary PL011 UART is ttyAMA0 once enabled.
    """
    if platform_id == "armbian":
        return "ttyS0"
    return "ttyAMA0"


def _armbian_console_value(content: str) -> Optional[str]:
    """Return the value of the (last) console= line in an armbianEnv.txt,
    or None if no such line is present."""
    value = None
    for line in content.splitlines():
        if line.strip().startswith("console="):
            value = line.strip().split("=", 1)[1].strip()
    return value


def _armbian_set_console_serial(content: str) -> str:
    """Return `content` with its console= line (if any) rewritten to
    console=serial, appending one if it wasn't present. Every other line
    (bootlogo=, fdtfile=, rootdev=, overlays=, ...) is left untouched.
    """
    lines = content.splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith("console="):
            new_lines.append("console=serial")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append("console=serial")
    return "\n".join(new_lines) + "\n"


def _current_user() -> str:
    return getpass.getuser()


def _user_in_dialout_group(user: str) -> bool:
    result = subprocess.run(["id", "-nG", user], capture_output=True, text=True)
    return "dialout" in result.stdout.split()


def check_uart_host_state() -> list[str]:
    """Read-only check of the host's hardware-UART setup. Returns a list
    of human-readable problems found — an empty list means everything
    already matches, nothing needs fixing.

    Boot-config checks (armbianEnv.txt console=, or Pi's enable_uart=1 /
    dtoverlay=disable-bt) are skipped — not failed — when the host
    platform can't be confidently classified, rather than guessing at a
    mechanism we haven't confirmed. The serial-getty and dialout-group
    checks always run, since those don't depend on which boot-config
    mechanism is in play.
    """
    problems: list[str] = []
    platform_id, config_path = _detect_uart_host_platform()

    if platform_id == "unsupported":
        problems.append("Host platform not recognized — boot config not checked (see notes below)")
    elif platform_id == "armbian":
        try:
            value = _armbian_console_value(config_path.read_text())
            if value != "serial":
                problems.append(
                    f"{config_path} has console={value or '(not set)'} — needs console=serial "
                    "to free the UART for Klipper"
                )
        except OSError as e:
            problems.append(f"Could not read {config_path}: {e}")
    else:  # pi-bt-shared or pi-standalone
        try:
            content = config_path.read_text()
            if not re.search(r"^\s*enable_uart=1\s*$", content, re.MULTILINE):
                problems.append(f"enable_uart=1 missing from {config_path}")
            if platform_id == "pi-bt-shared" and not re.search(r"^\s*dtoverlay=disable-bt\s*$", content, re.MULTILINE):
                problems.append(
                    f"dtoverlay=disable-bt missing from {config_path} "
                    "(needed to free the UART from Bluetooth on this board)"
                )
        except OSError as e:
            problems.append(f"Could not read {config_path}: {e}")

    getty_device = _uart_getty_device(platform_id)
    try:
        active = subprocess.run(
            ["systemctl", "is-active", f"serial-getty@{getty_device}.service"], capture_output=True, text=True
        ).stdout.strip()
        if active == "active":
            problems.append(f"serial-getty@{getty_device}.service is active (login console still bound to the UART)")
    except Exception as e:
        problems.append(f"Could not check serial-getty@{getty_device}.service status: {e}")

    user = _current_user()
    if not _user_in_dialout_group(user):
        problems.append(f"user '{user}' is not in the 'dialout' group")

    return problems


def fix_uart_host_state() -> tuple[list[str], bool]:
    """Apply fixes for whatever check_uart_host_state() found wrong.
    Idempotent — safe to call even when some/all of this is already
    correct. Raises EZFException on the first command that fails.

    Returns (skipped, reboot_required):
      skipped: notes about anything left untouched because the host
        platform couldn't be confidently classified.
      reboot_required: True if a boot-config change was made, since
        armbianEnv.txt / config.txt changes only take effect after a
        reboot (unlike the getty/dialout fixes, which are live).
    """
    skipped: list[str] = []
    reboot_required = False
    platform_id, config_path = _detect_uart_host_platform()

    if platform_id == "unsupported":
        skipped.append("Boot config not checked/fixed — host platform not recognized")
    elif platform_id == "armbian":
        content = config_path.read_text()
        if _armbian_console_value(content) != "serial":
            new_content = _armbian_set_console_serial(content)
            _run_sudo(
                ["sh", "-c", f"cat > {config_path} <<'EOF'\n{new_content}EOF"],
                "Updating armbianEnv.txt console setting",
            )
            reboot_required = True
    else:  # pi-bt-shared or pi-standalone
        content = config_path.read_text()
        lines_to_add = []
        if not re.search(r"^\s*enable_uart=1\s*$", content, re.MULTILINE):
            lines_to_add.append("enable_uart=1")
        if platform_id == "pi-bt-shared" and not re.search(r"^\s*dtoverlay=disable-bt\s*$", content, re.MULTILINE):
            lines_to_add.append("dtoverlay=disable-bt")
        if lines_to_add:
            addition = "\n".join(lines_to_add) + "\n"
            _run_sudo(
                ["sh", "-c", f"cat >> {config_path} <<'EOF'\n{addition}EOF"],
                "Updating boot config for UART",
            )
            reboot_required = True

    getty_device = _uart_getty_device(platform_id)
    if subprocess.run(["systemctl", "is-active", "--quiet", f"serial-getty@{getty_device}.service"]).returncode == 0:
        _run_sudo(["systemctl", "stop", f"serial-getty@{getty_device}.service"], f"Stopping serial-getty@{getty_device}")
        _run_sudo(["systemctl", "disable", f"serial-getty@{getty_device}.service"], f"Disabling serial-getty@{getty_device}")

    user = _current_user()
    if not _user_in_dialout_group(user):
        _run_sudo(["usermod", "-aG", "dialout", user], f"Adding {user} to dialout group")

    return skipped, reboot_required


def reboot_host() -> None:
    """Reboot the host via sudo. A successful reboot will typically kill
    this process before _run_sudo returns; on failure to even invoke it,
    raises EZFException like every other _run_sudo call."""
    _run_sudo(["reboot"], "Rebooting")


# --------------------------------------------------------------------------
# Klipper service stop/start around a flash
# --------------------------------------------------------------------------

def stop_services(names: list[str]) -> list[str]:
    """Stop each systemd service in `names` that is currently active.

    Returns the list of names actually stopped (i.e. excluding ones that
    were already inactive), so the caller only restarts what it itself
    touched, rather than starting a service the user had deliberately
    left off. Raises EZFException on the first stop command that fails.
    """
    stopped = []
    for name in names:
        if subprocess.run(["systemctl", "is-active", "--quiet", name]).returncode != 0:
            continue
        _run_sudo(["systemctl", "stop", name], f"Stopping {name}")
        stopped.append(name)
    return stopped


def start_services(names: list[str]) -> None:
    """Best-effort restart of each service in `names`.

    Doesn't raise — this runs during app-exit cleanup after Textual has
    already torn down (see ui/__init__.py's main()), where there's no
    dialog left to show an error in, and a single failed restart
    shouldn't block the others or block the process from exiting cleanly.
    """
    for name in names:
        try:
            _run_sudo(["systemctl", "start", name], f"Starting {name}")
        except Exception:
            pass
