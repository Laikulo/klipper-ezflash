import dataclasses
import json
import logging
import sys
from os import PathLike
from pathlib import Path
from typing import Optional, Dict, Union


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

    @classmethod
    def get_one_from_file(cls, file: PathLike, category: str, manufacturer: str, model: str, variant: str) -> 'BoardDefinition':
        all_data = json.load(Path(file).open())
        board_def_data = all_data.get(category, {}).get(manufacturer, {}).get(model, {}).get(variant, None)
        if not board_def_data:
            raise ValueError(f"No definition for {category}/{manufacturer}/{model}/{variant}")
        return BoardDefinition.from_data(manufacturer, model, variant, board_def_data)

    @classmethod
    def read_from_file(cls, file: PathLike):
        json_data = json.load(Path(file).open())
        for category, manufacturers in json_data.items():
            for manufacturer, products in manufacturers.items():
                for product, variants in products.items():
                    for variant, json_defn in variants.items():
                        try:
                            yield BoardDefinition.from_data(manufacturer, product, variant, json_defn)
                        except KeyError as e:
                            logging.warning(f"Board definition for {category}/{manufacturer}/{product}/{variant} is missing {e.args[0]}, skipping...")
                            continue

    @classmethod
    def get_all_from_file(cls, file: PathLike):
        return list(cls.read_from_file(file))

    @classmethod
    def from_data(cls, manufacturer, model, variant, definition: Dict) -> 'BoardDefinition':
        opts = {
            "manufacturer": manufacturer,
            "model": model,
            "variant": variant,
            "mcu": BoardMCUDefinition.from_data(definition['mcu']),
        }
        if usb := definition.get('usb'):
            opts['usb'] = usb
        if uart := definition.get('uart'):
            opts['uart'] = BoardUARTDefinition.from_data(uart)
        if can := definition.get('can'):
            opts['can'] = BoardCANDefinition.from_data(can)
        return cls(**opts)




@dataclasses.dataclass
class BoardCANDefinition(object):
    tx_pin: str
    rx_pin: str

    @classmethod
    def from_data(cls, data: Dict|str) -> 'BoardCANDefinition':
        if type(data) is str:
            tok = data.split("/")
            return cls(
                tx_pin=tok[0],
                rx_pin=tok[1],
            )
        else:
            return cls(
                tx_pin=data['can_tx'],
                rx_pin=data['can_rx']
            )


@dataclasses.dataclass
class BoardUARTDefinition(object):
    tx_pin: str
    rx_pin: str
    
    @classmethod
    def from_data(cls, data: Dict) -> 'BoardUARTDefinition':
        return cls(
            tx_pin=data['tx_pin'],
            rx_pin=data['rx_pin']
        )


@dataclasses.dataclass
class BoardMCUDefinition(object):
    mcu: str
    clock: str
    flash: Optional[str] = None
    
    @classmethod
    def from_data(cls, data: Dict) -> 'BoardMCUDefinition':
        return cls(
            mcu=data['mcu'],
            clock=data['clock'],
            flash=data.get('flash')
        )