from ..util import find_klipper, get_boards
from ..model import BoardDefinition
from ..configurator import Configurator
from pathlib import Path

import logging

def main():
    boards = BoardDefinition.get_all()

    klipper = find_klipper()
    failures = []
    print(f"Checking {len(boards)} boards...")
    for board in boards:
        try:
            config = Configurator(klipper, board)

            for i in config.get_interfaces():
                try:
                    config.set_interface(i)
                except Exception as e:
                    failures.append(f"{board}/{i}: {e!r}")
        except Exception as e:
            failures.append(f"{board}: {e!r}")

    if failures:
        print("==== FAIL ====")
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    else:
       print("==== PASS ====")

