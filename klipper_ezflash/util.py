import hashlib
import os
import re
import subprocess
import shutil
from functools import cache
from pathlib import Path

from importlib.resources import files

from typing import Any
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
