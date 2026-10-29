import logging
from pathlib import Path
from pprint import pprint as pp

from klipper_ezflash.configurator import Configurator
from klipper_ezflash.model import BoardDatabase
from .util import find_klipper
from .ui import UI

logger = logging.getLogger("klipper-mcu-configs")


def main():
    logging.basicConfig(level=logging.INFO)

    ui = UI()
    ui.launch()


if __name__ == "__main__":
    main()
