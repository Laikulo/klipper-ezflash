import os
from os import PathLike
from pathlib import Path
from typing import List, Optional

from kconfiglib import Kconfig as KCLKConfig, Choice as KCLChoice, Symbol as KCLSymbol, BOOL as KCL_BOOL


class KConfig(object):

    def __init__(self, srctree: PathLike):
        self.srctree = Path(srctree)
        self.kcl = self._get_kcl()

    def _get_kcl(self):
        old_env = os.environ.copy()
        os.environ["srctree"] = str(self.srctree.absolute())
        kc = KCLKConfig(filename='src/Kconfig')
        os.environ = old_env
        return kc

    @property
    def choices(self):
        return [KConfigChoice(self, x) for x in self._choices()]

    @property
    def symbols(self):
        return [KConfigSymbol(self, x) for x in self._symbols()]

    def _symbols(self, allow_invisible: bool = False):
        return [x for x in self.kcl.unique_defined_syms if (allow_invisible or x.visibility != 0) and not x.choice]

    def _choices(self, allow_invisible: bool = False):
        return [x for x in self.kcl.unique_choices if (allow_invisible or x.visibility != 0)]

    def choice(self, name: str = None, prompt: str = None, allow_invisible: bool = False) -> Optional['KConfigChoice']:
        if name:
            if matches := [x for x in self._choices(allow_invisible) if x.prompt == name]:
                if len(matches) > 1:
                    raise ValueError(f"More than one choice definition defined for {name}")
                else:
                    return KConfigChoice(self, matches[0])
        elif prompt:
            for choice in self._choices(allow_invisible):
                for node in choice.nodes:
                    if node.prompt[0] == prompt:
                        return KConfigChoice(self, choice)
        else:
            raise ValueError(f"No search for choice specified. This is a bug and should not happen")
        return None

    def symbol(self, name: str = None, prompt: str = None, allow_invisible: bool = False) -> Optional['KConfigSymbol']:
        if name:
            if matches := [x for x in self._symbols(allow_invisible) if x.name == name]:
                if len(matches) > 1:
                    raise ValueError(f"More than one symbol defined for {name}")
                else:
                    return KConfigSymbol(self, matches[0])
        elif prompt:
            for symbol in self._symbols(allow_invisible):
                for node in symbol.nodes:
                    if node.prompt[0] == prompt:
                        return KConfigSymbol(self, symbol)
        else:
            raise ValueError(f"No search for symbol specified. This is a bug and should not happen")
        return None


class KConfigChoice(object):
    def __init__(self, kc: KConfig, choice: KCLChoice):
        self._kc = kc
        self._choice = choice

    @property
    def prompt(self):
        try:
            return self._choice.nodes[0].prompt[0]
        except IndexError:
            return "Unknown Choice"

    def values(self) -> List[str]:
        return [x.name for x in self._choice.syms]

    def choices(self) -> List[KCLSymbol]:
        return self._choice.syms

    def select(self, name: str = None, prompt: str = None):
        if name:
            matches = [x for x in self._choice.syms if x.name == name]
            if len(matches):
                matches[0].set_value(2)
            else:
                raise ValueError(f"No option {name} found for {self.prompt}")
        elif prompt:
            for choice in self._choice.syms:
                for node in choice.nodes:
                    if node.prompt[0] == prompt:
                        choice.set_value(2)
                        return
            raise ValueError(f"No option {prompt} found for {self.prompt}")
        else:
            raise ValueError(f"No selection for {self.prompt}")

    def __repr__(self):
        return self._choice.__repr__()


class KConfigSymbol(object):
    def __init__(self, kc: KConfig, symbol: KCLSymbol):
        self._kc = kc
        self._symbol = symbol

    def set(self, val):
        if self._symbol.type == KCL_BOOL:
            if type(val) is bool:
                if val:
                    self._symbol.set_value(2)
                else:
                    self._symbol.set_value(0)
                # Klipper doesn't use "m"
            else:
                raise ValueError(f"Not a boolean {val}")
        else:
            self._symbol.set_value(val)

    def get(self):
        if self._symbol.type == KCL_BOOL:
            if self._symbol.tri_value == 2:
                return True
            elif self._symbol.tri_value == 0:
                return False
            else:
                raise ValueError(f"Not a boolean {self._symbol.tri_value} for {self._symbol.name}")
        else:
            return self._symbol.str_value

    def __repr__(self):
        return self._symbol.__repr__()
