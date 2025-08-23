import argparse
import logging
from pprint import pprint

from ..configurator import Configurator
from ..model import BoardDefinition
from ..util import find_klipper

from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", action='store_true')
    ap.add_argument("-v", action='store_true')
    ap.add_argument("manufacturer")
    ap.add_argument("model")
    ap.add_argument("variant")
    ap.add_argument("interface")
    ap.add_argument("out", type=Path)

    args = ap.parse_args()

    if args.d:
        loglevel = logging.DEBUG
    elif args.v:
        loglevel = logging.INFO
    else:
        loglevel = logging.WARNING
    logging.basicConfig(level=loglevel)


    board = BoardDefinition.get(
        args.manufacturer,
        args.model,
        args.variant,
    )

    c = Configurator(find_klipper(), board)
    c.select_interface_by_type(
        args.interface.upper()
    )

    c.save_config(args.out)



