import os
from functools import cache
from pathlib import Path

from importlib.resources import files

from typing import Collection, Any

_COMMON_KLIPPER_LOCATIONS = ["~/klipper", "~/Klipper", "/usr/src/klipper"]


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
        return files("board2kconf.data").joinpath("boards.json").open("r")
    except (FileNotFoundError, ModuleNotFoundError):
        pass
    # Try other options here
    raise RuntimeError("Could not find the board database")


def cajole_collection(in_val: Any):
    if type(in_val) is str:
        return in_val
    elif (in_val is None) or (isinstance(in_val, Collection)):
        return in_val
    else:
        return (in_val,)


@cache
def table_munge(key: str, table):
    """
    Given a key, and a tuple[tuple[str]], return the first tuple that contains the string, otherwise return the string
    """
    for entry in table:
        if key in entry:
            return entry
    return key
