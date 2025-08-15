import logging

from .util import find_klipper

logger = logging.getLogger("klipper-mcu-configs")

def main():
    logging.basicConfig(level=logging.DEBUG)
    logger.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")

    klipper_path = find_klipper()

    #TODO: Check if state file exists






if __name__ == '__main__':
    main()