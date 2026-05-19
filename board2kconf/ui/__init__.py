from sys import exit, stderr
import traceback

from ..model import BoardDatabase

from dialog import Dialog
import traceback
from sys import exit, stderr

from dialog import Dialog

from ..model import BoardDatabase


class UI(object):

    def __init__(self):
        self._state = 0
        self._dialog = Dialog(dialog='dialog', autowidgetsize=True)


    def main_menu(self):
        self._dialog.set_background_title('Manu Menu')
        return self._dialog.menu(
            "What would you like to do?",
            choices=[
                ('boardinfo', "Lookup information about a board"),
                ('crash', "Intentionally crash"),
                ('exit', "Exit")
            ]
        )


    def select_board(self):
        bdb = BoardDatabase()
        board = bdb.get_all()
        manufacturers = tuple(sorted(set([b.manufacturer for b in board])))
        code, tag = self._dialog.menu(
            "Select manufacturer",
            choices=[(str(i),n) for i,n in enumerate(manufacturers)
            ],
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        if not tag:
            return None
        selected_mfr = manufacturers[int(tag)]

        mfr_models = tuple(sorted(set(b.model for b in board if b.manufacturer == selected_mfr)))
        code, tag = self._dialog.menu(
            "Select model",
            choices=[(str(i),n) for i,n in enumerate(mfr_models)
                     ],
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        if not tag:
            return None
        selected_model = mfr_models[int(tag)]

        board_variants = tuple(sorted(set(b.variant for b in board if b.manufacturer == selected_mfr and b.model == selected_model)))
        if len(board_variants) > 1:
            code, tag = self._dialog.menu(
                "Select variant",
                choices=[(str(i),n) for i,n in enumerate(board_variants)
                         ],
            )
            if code in (Dialog.CANCEL, Dialog.ESC):
                return None
            if not tag:
                return None
            selected_variant = board_variants[int(tag)]
        else:
            selected_variant = board_variants[0]
        return bdb.get(selected_mfr, selected_model, selected_variant)


    def menus(self):
        while True:
            code, tag = self.main_menu()
            if code == Dialog.OK:
                if tag == 'boardinfo':
                    board = self.select_board()
                    self._dialog.msgbox(str(board))
                elif tag == 'exit':
                    return 0
                elif tag == 'crash':
                    raise RuntimeError("Intentional Crash")
                else:
                    raise KeyError("Unexpected response from main menu")
            elif code == Dialog.CANCEL:
                return 0
            else: ...
            # Don't care

    def launch(self):
        try:
            self.menus()
        except KeyboardInterrupt:
            print("\033[H\033[2J")
            exit(0)
        except Exception as e:
            self.handle_doom(e)
        print("\033[H\033[2J")

    @staticmethod
    def describe_error(e):
        if type(e) == NotImplementedError:
            human_text = "The functionality you have tried to use has net yet been implemented\n"
            human_text += f"The responsible part of the codebase said: {e.args}"
        else:
            human_text = f"{type(e)}\n{e.args}\nNo additional details are available"
        return human_text


    def handle_doom(self, e):
        try:
            human_text = self.describe_error(e)
        except Exception:
            human_text = "Unknown error"
        try:
            fail_dialog = Dialog(dialog='dialog')
            fail_dialog.set_background_title('Fatal Error')
            fail_dialog.msgbox(f"A fatal error has occurred in EZFlash\n\n{human_text}\n\nThe program will now exit", width=0, height=0)
            print("\033[H\033[2J")
        except Exception as gui_e:
            print("\033[H\033[2J")
            print("A fatal error has occurred in EZFLASH, and the graphical crash handler also failed",
                  file=stderr)
            print("---Fault---", file=stderr)
            traceback.print_exception(gui_e, file=stderr)
            print("---END---\n", file=stderr)
            print("We are sorry for the inconvenience, the program will now exit", file=stderr)
        exit(1)