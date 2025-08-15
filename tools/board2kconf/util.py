import os
from functools import cache
from pathlib import Path

from importlib.resources import files

_COMMON_KLIPPER_LOCATIONS = [
    "~/klipper",
    "~/Klipper",
    "/usr/src/klipper"
]

@cache
def find_klipper():
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

def get_boards():
    if override_path := os.environ.get("KBOARD_BOARDS_PATH"):
        path = Path(override_path)
        if path.exists():
            return path.open()
        else:
            raise ValueError("Specified KBOARD_BOARDS_PATH does not exist")
    try:
        return files('board2kconf.data').joinpath("boards.json")
    except FileNotFoundError:
        pass
    # Try other options here
    raise RuntimeError("Could not find the board database")


