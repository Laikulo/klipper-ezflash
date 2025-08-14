import logging
from datetime import datetime
from pathlib import Path

from .configurator import Configurator
from .kconfig import KConfig
from .model import BoardDefinition

from pprint import pprint as pp

logger = logging.getLogger("klipper-mcu-configs")

def main():
    logging.basicConfig(level=logging.DEBUG)
    logger.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")
    board_defs = BoardDefinition.get_all_from_file(
        Path(__file__).parent.parent.parent / 'board' / 'v1' / 'example.json',
    )
    my_board = [ d for d in board_defs if d.manufacturer == "BTT" and d.model == "Octopus" and d.variant == "Octopus-F446"][0]
    # my_board = [ d for d in board_defs if d.manufacturer == "Mellow3D" and d.model == "Fly LIS2DW" and d.variant == "Fly LIS2DW"][0]

    config = Configurator(
        (Path(__file__).parent.parent.parent / 'test_resources' / 'kconfig' ),
        my_board
    )

    config.set_arch(my_board.mcu.arch)
    config.set_mcu(my_board.mcu.mcu)
    if clock := my_board.mcu.clock:
        config.set_freq(clock)

    # Note: we don't want to set flash type if we don't have a "stage 2". Which we don't have if there is a bootloader.
    # RP2040 specifically
    if flash := my_board.mcu.flash:
        config.set_flash(flash)

    pp(my_board)
    pp(config.get_interfaces())
    pp(config.supports_canbridge())

    for i in config.get_interfaces():
        config.set_interface(i)
        pp(config)


#    code.interact(local=locals())



if __name__ == '__main__':
    main()