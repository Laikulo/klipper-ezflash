import dataclasses
import json
import logging
from functools import cached_property
from os import PathLike
from pathlib import Path
from typing import Union, Optional, Dict, TextIO

from .util import get_boards

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class BoardDefinition(object):
    manufacturer: str
    model: str
    variant: str
    mcu: 'BoardMCUDefinition'
    can: Union['BoardCANDefinition',str,None] = None
    usb: Optional[str] = None
    uart: Optional['BoardUARTDefinition'] = None
    status: Optional[str] = None
    klipper_options: Optional[Dict[str,str]] = None

    def __str__(self):
        return f"{self.manufacturer}/{self.model}/{self.variant}"

    @classmethod
    def get_one_from_file(cls, file: PathLike, category: str, manufacturer: str, model: str, variant: str) -> 'BoardDefinition':
        all_data = json.load(Path(file).open())
        board_def_data = all_data.get(category, {}).get(manufacturer, {}).get(model, {}).get(variant, None)
        if not board_def_data:
            raise ValueError(f"No definition for {category}/{manufacturer}/{model}/{variant}")
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
                            yield BoardDefinition.from_data(manufacturer, product, variant, json_defn)
                        except KeyError as e:
                            logger.warning(f"Board definition for {category}/{manufacturer}/{product}/{variant} is missing {e.args[0]}, skipping...")
                            continue

    @classmethod
    def get_all_from_file(cls, file: PathLike):
        boards = list(cls.read_from_file(file))
        logger.info(f"Loaded {len(boards)} boards")
        return boards

    @classmethod
    def get_all(cls):
        boards = list(cls.read_from_stream(get_boards()))
        logger.info(f"Loaded {len(boards)} boards")
        return boards

    @classmethod
    def from_data(cls, manufacturer, model, variant, definition: Dict) -> 'BoardDefinition':
        opts = {
            "manufacturer": manufacturer,
            "model": model,
            "variant": variant,
            "mcu": BoardMCUDefinition.from_data(definition['mcu']),
        }
        if (usb := definition.get('usb')) is not None:
            opts['usb'] = usb
        if uart := definition.get('uart'):
            opts['uart'] = BoardUARTDefinition.from_data(uart)
        elif rs232 := definition.get('rs232'):
            opts['uart'] = BoardUARTDefinition.from_data({
                'tx_pin': rs232['tx'],
                'rx_pin': rs232['rx']
            })
        if can := definition.get('can'):
            opts['can'] = BoardCANDefinition.from_data(can)
        elif can := definition.get('CAN_Bridge'):
            opts['can'] = BoardCANDefinition.from_data(can)
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


@dataclasses.dataclass
class BoardCANDefinition(object):
    tx_pin: str
    rx_pin: str

    @classmethod
    def from_data(cls, data: Dict|str) -> 'BoardCANDefinition':
        if type(data) is str:
            tok = data.split("/")
            return cls(
                rx_pin=tok[0],
                tx_pin=tok[1]
            )
        else:
            return cls(
                rx_pin=data['can_rx'],
                tx_pin=data['can_tx']
            )

    def as_interface(self):
        return BoardInterfaceDefinition("CAN", {'tx': self.tx_pin, 'rx': self.rx_pin})

@dataclasses.dataclass
class BoardUARTDefinition(object):
    tx_pin: str
    rx_pin: str

    @classmethod
    def from_data(cls, data: Dict) -> 'BoardUARTDefinition':
        return cls(
            rx_pin=data['rx_pin'],
            tx_pin=data['tx_pin']
        )

    def as_interface(self):
        return BoardInterfaceDefinition("UART", {'tx': self.tx_pin, 'rx': self.rx_pin})


@dataclasses.dataclass
class BoardMCUDefinition(object):
    arch: str
    mcu: str
    clock: Optional[str] = None,
    flash: Optional[str] = None

    @classmethod
    def from_data(cls, data: Dict) -> 'BoardMCUDefinition':
        return cls(
            arch=data['architecture'],
            mcu=data['mcu'],
            clock=data.get('clock'),
            flash=data.get('flash')
        )

@dataclasses.dataclass
class BoardInterfaceDefinition(object):
    if_type: str
    pins: Dict[str,str]

    def __str__(self) -> str:
        return f"{self.if_type.upper()}"

    @classmethod
    def usb(cls, spec):
        if spec:
            tok = spec.split("/")
            pins = {
                'dm': tok[0],
                'dp': tok[1]
            }
        else:
            pins = {}
        return cls(
            "USB",
            pins
        )

