import logging
from pathlib import Path
from pprint import pprint as pp

from board2kconf.configurator import Configurator
from board2kconf.model import BoardDatabase
from .util import find_klipper

import urwid

logger = logging.getLogger("klipper-mcu-configs")

def main():
    logging.basicConfig(level=logging.DEBUG)
    logger.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")

    c = Configurator(
        find_klipper(),
        BoardDatabase().get(
            "Mellow",
            "SB2040",
            "SB2040v3"
        )
    )

    pp(c.get_interfaces())

    c.set_interface([i for i in c.get_interfaces() if i.if_type == "CAN"][0])

    c.save_config(Path("test.config2"))


if __name__ == '__main__':
    main()