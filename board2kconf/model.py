import dataclasses
import json
import logging
import os
from functools import cached_property, cache
from os import PathLike
from pathlib import Path
from typing import Union, Optional, Dict, TextIO

from .util import get_boards

logger = logging.getLogger(__name__)


class BoardDatabase(object):
    def __init__(self, source: TextIO | PathLike | None = None):
        # Always load default boards first
        self._boards = list(BoardDefinition.read_from_stream(get_boards()))
        logger.info(f"Loaded {len(self._boards)} default board(s)")

        # Overlay user boards from XDG config if present
        user_path = _get_user_boards_path()
        if user_path is not None and user_path.exists():
            try:
                with open(user_path, "r") as f:
                    self._overlay_from_stream(f)
                logger.info(f"Overlayed user boards from {user_path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load user boards from {user_path}: {e}")

        if source is not None:
            # If an explicit source was passed, overlay it on top of defaults + user
            if isinstance(source, PathLike):
                self._overlay_from_file(source)
            elif isinstance(source, TextIO):
                self._overlay_from_stream(source)

    def _overlay_from_stream(self, stream: TextIO):
        """Overlay boards from a text stream on top of existing defaults."""
        json_data = json.load(stream)
        for category, manufacturers in json_data.items():
            for manufacturer, products in manufacturers.items():
                for product, variants in products.items():
                    for variant, json_defn in variants.items():
                        try:
                            board = BoardDefinition.from_data(
                                manufacturer, product, variant, json_defn
                            )
                            self._replace_board(board)
                        except KeyError as e:
                            logger.warning(
                                f"User board definition for {category}/{manufacturer}/{product}/{variant} is missing {e.args[0]}, skipping..."
                            )
                            continue

    def _overlay_from_file(self, file: PathLike):
        with open(file, "r") as f:
            self._overlay_from_stream(f)

    def _replace_board(self, board):
        """Replace a board in the internal list if one with the same key exists."""
        for i, existing in enumerate(self._boards):
            if (
                existing.manufacturer == board.manufacturer
                and existing.model == board.model
                and existing.variant == board.variant
            ):
                self._boards[i] = board
                return
        # Board not found — add it as new
        self._boards.append(board)

    def get_board(self, manufacturer, model, variant):
        if mfr_matches := [x for x in self._boards if x.manufacturer == manufacturer]:
            if model_matches := [x for x in mfr_matches if x.model == model]:
                if variant_matches := [
                    x for x in model_matches if x.variant == variant
                ]:
                    if len(variant_matches) > 1:
                        raise ValueError("Duplicate board definition")
                    elif len(variant_matches) == 1:
                        return variant_matches[0]
        raise ValueError(f"No board data for {manufacturer}/{model}/{variant}")

    def get_all(self):
        return self._boards.copy()


def _get_user_boards_path():
    """Return the path to user_boards.json in XDG config directory, or None."""
    xdg_config_home = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    return Path(xdg_config_home) / "klipper-ezflash" / "user_boards.json"


@dataclasses.dataclass
class BoardDefinition(object):
    manufacturer: str
    model: str
    variant: str
    mcu: "BoardMCUDefinition"
    can: Union["BoardCANDefinition", str, None] = None
    usb: Optional[str] = None
    uart: Optional["BoardUARTDefinition"] = None
    status: Optional[str] = None
    klipper_options: Optional[Dict[str, str]] = None

    def __str__(self):
        return f"{self.manufacturer}/{self.model}/{self.variant}"

    @classmethod
    def get_one_from_file(
        cls, file: PathLike, category: str, manufacturer: str, model: str, variant: str
    ) -> "BoardDefinition":
        all_data = json.load(Path(file).open())
        board_def_data = (
            all_data.get(category, {})
            .get(manufacturer, {})
            .get(model, {})
            .get(variant, None)
        )
        if not board_def_data:
            raise ValueError(
                f"No definition for {category}/{manufacturer}/{model}/{variant}"
            )
        return BoardDefinition.from_data(manufacturer, model, variant, board_def_data)

    @classmethod
    def read_from_file(cls, file: PathLike):
        yield cls.read_from_stream(Path(file).open())

    @classmethod
    def read_from_stream(cls, stream: TextIO):
        json_data = json.load(stream)
        for category, manufacturers in json_data.items():
            for manufacturer, products in manufacturers.items():
                for product, variants in products.items():
                    for variant, json_defn in variants.items():
                        try:
                            yield BoardDefinition.from_data(
                                manufacturer, product, variant, json_defn
                            )
                        except KeyError as e:
                            logger.warning(
                                f"Board definition for {category}/{manufacturer}/{product}/{variant} is missing {e.args[0]}, skipping..."
                            )
                            continue

    @classmethod
    def get_all_from_file(cls, file: PathLike):
        boards = list(cls.read_from_file(file))
        logger.info(f"Loaded {len(boards)} boards")
        return boards

    @classmethod
    @cache
    def get_all(cls):
        boards = list(cls.read_from_stream(get_boards()))
        logger.info(f"Loaded {len(boards)} boards")
        return boards

    @classmethod
    def get(cls, manufacturer, model, variant):
        if mfr_matches := [x for x in cls.get_all() if x.manufacturer == manufacturer]:
            if model_matches := [x for x in mfr_matches if x.model == model]:
                if variant_matches := [
                    x for x in model_matches if x.variant == variant
                ]:
                    if len(variant_matches) > 1:
                        raise ValueError("Duplicate board definition")
                    elif len(variant_matches) == 1:
                        return variant_matches[0]
        raise ValueError(f"No board data for {manufacturer}/{model}/{variant}")

    @classmethod
    def from_data(
        cls, manufacturer, model, variant, definition: Dict
    ) -> "BoardDefinition":
        opts = {
            "manufacturer": manufacturer,
            "model": model,
            "variant": variant,
            "mcu": BoardMCUDefinition.from_data(definition["mcu"]),
        }
        if (usb := definition.get("usb")) is not None:
            opts["usb"] = usb
        if uart := definition.get("uart"):
            opts["uart"] = BoardUARTDefinition.from_data(uart)
        elif rs232 := definition.get("rs232"):
            opts["uart"] = BoardUARTDefinition.from_data(
                {"tx_pin": rs232["tx"], "rx_pin": rs232["rx"]}
            )
        if can := definition.get("can"):
            opts["can"] = BoardCANDefinition.from_data(can)
        elif can := definition.get("CAN_Bridge"):
            opts["can"] = BoardCANDefinition.from_data(can)
        return cls(**opts)

    @cached_property
    def interfaces(self):
        interfaces = []
        if self.usb is not None:
            interfaces.append(BoardInterfaceDefinition.usb(self.usb))
        if self.uart:
            interfaces.append(self.uart.as_interface())
        if self.can:
            interfaces.append(self.can.as_interface())
        return interfaces

    def pretty(self, indent=0):
        retstr = f"{self.manufacturer} {self.model}"
        if self.model != self.variant:
            retstr += f" ({self.variant})"
        retstr += "\n"
        retstr += f"  MCU: {self.mcu.pretty()}\n"
        retstr += "  Interfaces:\n"
        for interface in self.interfaces:
            retstr += f"    {interface.pretty()}\n"
        if self.status is not None:
            retstr += f"  Status LED on {self.status}\n"
        else:
            retstr += "  No status LED\n"

        if self.klipper_options:
            retstr += f"  Advanced options:\n"
            for opt, val in self.klipper_options.items():
                retstr += f"    {opt}={val}\n"
        return retstr


@dataclasses.dataclass
class BoardCANDefinition(object):
    tx_pin: str
    rx_pin: str

    @classmethod
    def from_data(cls, data: Dict | str) -> "BoardCANDefinition":
        if type(data) is str:
            tok = data.split("/")
            return cls(rx_pin=tok[0], tx_pin=tok[1])
        else:
            return cls(rx_pin=data["can_rx"], tx_pin=data["can_tx"])

    def as_interface(self):
        return BoardInterfaceDefinition("CAN", {"tx": self.tx_pin, "rx": self.rx_pin})


@dataclasses.dataclass
class BoardUARTDefinition(object):
    tx_pin: str
    rx_pin: str

    @classmethod
    def from_data(cls, data: Dict) -> "BoardUARTDefinition":
        return cls(rx_pin=data["rx_pin"], tx_pin=data["tx_pin"])

    def as_interface(self):
        return BoardInterfaceDefinition("UART", {"tx": self.tx_pin, "rx": self.rx_pin})


@dataclasses.dataclass
class BoardMCUDefinition(object):
    arch: str
    mcu: str
    clock: Optional[str] = (None,)
    flash: Optional[str] = None

    @classmethod
    def from_data(cls, data: Dict) -> "BoardMCUDefinition":
        return cls(
            arch=data["architecture"],
            mcu=data["mcu"],
            clock=data.get("clock"),
            flash=data.get("flash"),
        )

    def pretty(self):
        retstr = f"{self.arch} {self.mcu}"
        if self.clock and self.flash:
            retstr += f" with {self.clock} clock and {self.flash} flash"
        elif self.clock:
            retstr += f" with {self.clock} clock"
        elif self.flash:
            retstr += f" with {self.flash} flash"
        return retstr


@dataclasses.dataclass
class BoardInterfaceDefinition(object):
    if_type: str
    pins: Dict[str, str]

    def __str__(self) -> str:
        return f"{self.if_type.upper()}"

    @classmethod
    def usb(cls, spec):
        if spec:
            tok = spec.split("/")
            pins = {"dm": tok[0], "dp": tok[1]}
        else:
            pins = {}
        return cls("USB", pins)

    def pretty(self):
        retstr = self.if_type
        if self.pins:
            retstr += f" on {'/'.join(self.pins.values())}"
        return retstr
