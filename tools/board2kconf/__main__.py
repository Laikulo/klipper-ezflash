from pathlib import Path
from . import BoardDefinition

from pprint import pprint as pp

def main():
    board_defs = BoardDefinition.get_all_from_file(
        Path(__file__).parent.parent.parent / 'board' / 'v1' / 'example.json',
    )
    #pp(board_defs)


if __name__ == '__main__':
    main()