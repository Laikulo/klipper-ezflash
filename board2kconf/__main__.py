from __future__ import print_function
## !!! Important
## This class may be parsed by old python versions. All 3.9+ features should not be used here
## but they may be included after the version check.
## This is to ensure that we exit cleanly and inform the user even if we get started by ancient python

import sys
from . import PROJECT_MINIMUM_PYTHON, PROJECT_NAME

if sys.version_info < PROJECT_MINIMUM_PYTHON:
    print("!!! {} requires python {} or better. Exiting...".format(PROJECT_NAME,
                                                                   ".".join((str(i) for i in PROJECT_MINIMUM_PYTHON))),
          file=sys.stderr)
    raise SystemExit(2)

## From this point, modules and libraries that are 3.9+ exclusive may be used.
## However, syntax from 3.9+ may not be used anywhere in this file (no type unions, f-strings, &c)
## As such, this file should be kept as minimal as possible


## We have not yet validated dependencies, so we do that now

from .startup_util import check_dependencies
check_dependencies()

import logging
from .ui import UI

logger = logging.getLogger("klipper-mcu-configs")


def main():
    logging.basicConfig(level=logging.INFO)
    logger.warning("THIS IS WIP TOOLING, IT MAY EAT YOUR CAT. BE WARNED.")

    ui = UI()
    ui.launch()


if __name__ == '__main__':
    main()
