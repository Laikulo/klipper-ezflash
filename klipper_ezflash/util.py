import hashlib
import os
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