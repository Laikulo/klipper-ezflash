import logging
import sys
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
    failed_boards = []
    failed_interfaces = []

    for board in board_defs:
        pp(board, stream=sys.stderr)
        try:
            config = Configurator(
                (Path(__file__).parent.parent.parent / 'test_resources' / 'kconfig' ),
                board
            )

            config.set_arch(board.mcu.arch)
            config.set_mcu(board.mcu.mcu)
            if clock := board.mcu.clock:
                config.set_freq(clock)

            # Note: we don't want to set flash type if we don't have a "stage 2". Which we don't have if there is a bootloader.
            # RP2040 specifically
            if flash := board.mcu.flash:
                config.set_flash(flash)

            for i in config.get_interfaces():
                pp(i, stream=sys.stderr)
                try:
                    config.set_interface(i)
                except Exception as e:
                    logger.exception("Failed to set interface")
                    failed_interfaces.append(f"{board.manufacturer}/{board.model}/{board.variant}/{i.if_type}: {e!r}")
                    continue
        except Exception:
            logging.exception("Failed to load board")
            failed_boards.append(f"{board.manufacturer}/{board.model}/{board.variant}: {e!r}")
            continue

        print("\n\n====SUMMARY====", file=sys.stderr)

        for i in failed_interfaces:
            print(i, file=sys.stderr)

        for i in failed_boards:
            print(i, file=sys.stderr)


#    code.interact(local=locals())



if __name__ == '__main__':
    main()