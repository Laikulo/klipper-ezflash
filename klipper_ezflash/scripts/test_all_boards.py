from ..util import find_klipper, get_boards
from ..model import BoardDefinition
from ..configurator import Configurator
from pathlib import Path

import logging

_DIE_FAST = False


def main():
    boards = BoardDefinition.get_all()

    klipper_path = find_klipper()
    failures = []
    print(f"Checking {len(boards)} boards klipper configuration...")
    for board in boards:
        try:
            config = Configurator.klipper(klipper_path, board=board)

            for i in config.get_interfaces():
                try:
                    config.set_interface(i)
                except Exception as e:
                    if _DIE_FAST:
                        raise e
                    failures.append(f"{board}/{i}: {e!r}")
        except Exception as e:
            if _DIE_FAST:
                raise e
            failures.append(f"{board} klipper: {e!r}")

    print(f"Checking {len(boards)} boards Katapult bootloader configuration...")
    for board in boards:
        try:
            config = Configurator.katapult(klipper_path, board=board)

            for i in config.get_interfaces():
                try:
                    config.set_interface(i)
                except Exception as e:
                    if _DIE_FAST:
                        raise e
                    failures.append(f"{board}/katapult/{i}: {e!r}")
        except Exception as e:
            if _DIE_FAST:
                raise e
            failures.append(f"{board} katapult: {e!r}")

    if failures:
        print("==== FAIL ====")
        for failure in failures:
            print(failure)
        raise SystemExit(1)
    else:
        print("==== PASS ====")


if __name__ == "__main__":
    main()
