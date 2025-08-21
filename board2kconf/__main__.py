import logging
from pathlib import Path
from pprint import pprint as pp

from board2kconf.configurator import Configurator
from board2kconf.model import BoardDatabase
from .util import find_klipper
from .ui import UI

import urwid

logger = logging.getLogger("klipper-mcu-configs")

def main():
    logging.basicConfig(level=logging.INFO)
    logger.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")

    ui = UI()
    ui.launch()


if __name__ == '__main__':
    main()