import logging
from datetime import datetime
from pathlib import Path

from boarddata.tools.board2kconf.configurator import Configurator
from .kconfig import KConfig
from .model import BoardDefinition

logger = logging.getLogger("klipper-mcu-configs")

def main():
    logging.basicConfig(level=logging.INFO)
    logging.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")
    board_defs = BoardDefinition.get_all_from_file(
        Path(__file__).parent.parent.parent / 'board' / 'v1' / 'example.json',
    )
    my_board = [ d for d in board_defs if d.manufacturer == "BTT" and d.model == "Octopus" and d.variant == "Octopus-F446"][0]

    config = Configurator(
        (Path(__file__).parent.parent.parent / 'test_resources' / 'kconfig' ),
        my_board
    )

    config.set_arch(my_board.mcu.arch)
    config.set_mcu(my_board.mcu.mcu)
    if clock := my_board.mcu.clock:
        config.set_freq(clock)

    config.save_config(Path("./test.config"))

#    code.interact(local=locals())



if __name__ == '__main__':
    main()