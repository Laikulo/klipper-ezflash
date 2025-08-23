import logging
import os
from dataclasses import dataclass
from os import PathLike
import subprocess
from functools import cache
from pathlib import Path

from importlib.resources import files
from typing import Optional

logger = logging.getLogger(__name__)

_COMMON_KLIPPER_LOCATIONS = [
    "~/klipper",
    "~/Klipper",
    "/usr/src/klipper"
]

@cache
def find_klipper():
    kl = _find_klipper()
    ver = get_klipper_version(kl)
    if not ver:
        ver = "(unknown version)"
    logger.info(f'Found klipper {ver} at {kl}')
    return kl

@cache
def _find_klipper():
    if override_path := os.environ.get("KBOARD_KLIPPER_PATH"):
        path = Path(override_path)
        if path.exists():
            return path
        else:
            raise ValueError("Specified KBOARD_KLIPPER_PATH does not exist")
    for location in _COMMON_KLIPPER_LOCATIONS:
        if (path := Path(location)).exists():
            return path
    raise RuntimeError("Could not find the klipper checkout")

@dataclass
class KlipperVersion(object):
    pass

def get_klipper_version(klipper_path: Optional[PathLike] =False):
    if not klipper_path:
        klipper_path = find_klipper()
    try:
        desc = subprocess.run(
            [ "git", "describe",
              "--tags", "--always", "--long", "--dirty"],
            cwd=klipper_path,
            check=True,
            stdout=subprocess.PIPE,
            text=True
        )
        return desc.stdout.rstrip()
    except Exception:
        return None


def get_boards():
    if override_path := os.environ.get("KBOARD_BOARDS_PATH"):
        path = Path(override_path)
        if path.exists():
            return path.open()
        else:
            raise ValueError("Specified KBOARD_BOARDS_PATH does not exist")
    try:
        return files('board2kconf.data').joinpath("boards.json").open("r")
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    # Try other options here
    raise RuntimeError("Could not find the board database")


