import logging
import os
from dataclasses import dataclass
from os import PathLike
import subprocess
from functools import cache, total_ordering
from pathlib import Path

from importlib.resources import files
from typing import Optional, overload, Tuple, Any

logger = logging.getLogger(__name__)

_COMMON_KLIPPER_LOCATIONS = [
    "~/klipper",
    "~/Klipper",
    "/usr/src/klipper"
]

@cache
def find_klipper():
    return KlipperInstallation.find().path

@dataclass
class KlipperInstallation(object):
    path: Path
    version: 'KlipperVersion'

    @classmethod
    @cache
    def find(cls) -> 'KlipperInstallation':
        inst = cls.__find_installation()
        return cls(
            path=inst,
            version=KlipperVersion.from_klipper(inst)
        )

    @staticmethod
    @cache
    def __find_installation():
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
@total_ordering
class KlipperVersion(object):
    raw: str
    release: tuple
    depth: int
    git: Optional[str]
    dirty: bool

    def as_tuple(self) -> Tuple[int,int]:
        return self.kevin_version, self.depth

    @overload
    def __eq__(self, other: 'KlipperVersion') -> bool:
        pass

    @overload
    def __eq__(self, other: tuple[int,int]) -> bool:
        pass

    @overload
    def __eq__(self, other: int) -> bool:
        pass

    def __eq__(self, other) -> bool:
        if type(other) is self.__class__:
            if self.raw == other.raw:
                return True
            if self.git == other.git:
                return True
            if (
                    not self.is_devel() and
                    not other.is_devel() and
                    self.release == other.release
            ):
                return True
            return (
                    self.release == other.release and
                    self.depth == other.depth and
                    self.git == other.git
            )
        elif type(other) is tuple:
            return (
                    self.kevin_version == other[0] and
                    self.depth == other[1]
            )
        elif type(other) is int:
            return self.release == other and self.depth == 0
        else:
            raise NotImplementedError(f"Comparison between Klipperversion and {type(other)} not supported")

    @overload
    def __gt__(self, other: 'KlipperVersion') -> bool:
        pass

    @overload
    def __gt__(self, other: tuple) -> bool:
        pass

    @overload
    def __gt__(self, other: int) -> bool:
        pass

    def __gt__(self, other: Any):
        if type(other) is self.__class__:
            return self.as_tuple() > other.as_tuple()
        elif type(other) is tuple:
            return self.as_tuple() > other
        elif type(other) is int:
            return self.as_tuple() > (other, 0)
        else:
            raise NotImplementedError(f"Comparison between Klipperversion and {type(other)} not supported")


    @property
    def kevin_version(self) -> int:
        if self.release:
            return int(self.release[1])
        else:
            return 0

    def is_devel(self):
        return self.depth or self.git


    @classmethod
    def from_str(cls, in_str) -> 'KlipperVersion':
        in_str = in_str.rstrip()
        opts = {
            'raw': in_str
        }
        tok = list(in_str.split('-'))
        if 'dirty' in tok:
            opts['dirty'] = True
            tok.remove('dirty')
        else:
            opts['dirty'] = False
        if len(tok) == 1:
            opts['depth'] = 0
            if tok[0].startswith('v'):
                opts['release'] = tuple(tok[0][1:].split('.'))
            else:
                opts['release'] = tuple()
                opts['git'] = tok[0]
        elif len(tok) == 3:
            opts['release'] = tuple(tok[0][1:].split('.'))
            opts['depth'] = int(tok[1])
            opts['git'] = tok[2]
        return cls(**opts)

    @classmethod
    def from_klipper(cls, klipper_path: Optional[PathLike] = False):
        try:
            desc = subprocess.run(
                [ "git", "describe",
                  "--tags", "--always", "--long", "--dirty"],
                cwd=klipper_path,
                check=True,
                stdout=subprocess.PIPE,
                text=True
            )
        except Exception:
            return None
        return cls.from_str(desc.stdout)


def get_klipper_version(klipper_path: Optional[PathLike] = False):
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


