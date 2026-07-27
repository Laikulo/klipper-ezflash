from sys import exit, stderr
import subprocess
import traceback
from pathlib import Path
from typing import Tuple

from ..model import BoardDatabase
from ..settings import InterfaceSettings
from ..util import find_katapult, _KATAPULT_CLONE_URL

from dialog import Dialog


class UI(object):
    def __init__(self):
        self._state = 0
        self._dialog = Dialog(dialog="dialog", autowidgetsize=True)
        self._dialog.add_persistent_args(("--no-collapse",))
        self._katapult_checked = False

    def _check_katapult(self):
        """Check if katapult is installed; prompt user to clone it if missing.

        Katapult is required for flashing Klipper bootloaders on supported boards.
        This check only runs once per session to avoid repeated prompts.
        """
        if self._katapult_checked:
            return  # Already checked this session
        
        katapult_path = find_katapult()
        if katapult_path is not None:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Katapult found at {katapult_path}")
            self._katapult_checked = True
            return

        # Katapult not found — prompt user to clone it or skip
        self._katapult_checked = True
        code, tag = self._dialog.menu(
            "KATAPULT NOT FOUND\n\n"
            "Klipper-ezflash requires 'katapult' (a bootloader flash helper) to be installed.\n\n"
            f"It can be cloned from:\n  {_KATAPULT_CLONE_URL}",
            choices=[
                ("clone", "Clone katapult now"),
                ("skip", "Skip (katapult will not be available)"),
                ("quit", "Quit"),
            ],
            cancel_label="Quit",
        )

        if code == Dialog.OK:
            if tag == "clone":
                self._clone_katapult()
            # skip/quit — just continue without katapult

    def _clone_katapult(self):
        """Clone the katapult repository to ~/katapult."""
        import logging
        logger = logging.getLogger(__name__)

        clone_target = Path.home() / "katapult"
        
        try:
            logger.info(f"Cloning katapult from {_KATAPULT_CLONE_URL} to {clone_target}")
            result = subprocess.run(
                ["git", "clone", _KATAPULT_CLONE_URL, str(clone_target)],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                error_msg = (result.stderr or f"Clone failed with exit code {result.returncode}").strip()
                logger.error(f"Failed to clone katapult: {error_msg}")
                self._dialog.msgbox(
                    f"Failed to clone katapult:\n\n{error_msg}",
                    width=70,
                    height=15,
                )
            else:
                logger.info(f"Successfully cloned katapult to {clone_target}")
                self._dialog.msgbox(
                    "Katapult has been cloned successfully!\n\n"
                    f"Location: {clone_target}\n\n"
                    "The tool will now check for it again.",
                    width=70,
                    height=15,
                )
                # Reset the checked flag so the next call re-validates
                self._katapult_checked = False
                self._check_katapult()
        except FileNotFoundError:
            logger.error("git command not found — cannot clone katapult")
            self._dialog.msgbox(
                "git is not installed or not in your PATH.\n\n"
                "Please install git and try again.",
                width=70,
                height=15,
            )
        except Exception as e:
            logger.error(f"Unexpected error cloning katapult: {e}")
            self._dialog.msgbox(
                f"An unexpected error occurred:\n\n{e}",
                width=70,
                height=15,
            )

    def main_menu(self):
        self._dialog.set_background_title("Main Menu")
        return self._dialog.menu(
            "What would you like to do?",
            choices=[
                ("boardinfo", "Lookup information about a board"),
                ("settings", "Edit the default communication settings"),
                ("crash", "Intentionally crash"),
                ("exit", "Exit"),
            ],
            cancel_label="Quit",
        )

    def select_board(self):
        bdb = BoardDatabase()
        board = list(bdb.get_all())
        manufacturers = tuple(sorted(set([b.manufacturer for b in board])))
        code, tag = self._dialog.menu(
            "Select manufacturer",
            choices=[(str(i), n) for i, n in enumerate(manufacturers)],
            no_tags=True,
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        if not tag:
            return None
        selected_mfr = manufacturers[int(tag)]

        mfr_models = tuple(
            sorted(set(b.model for b in board if b.manufacturer == selected_mfr))
        )
        code, tag = self._dialog.menu(
            "Select model",
            choices=[(str(i), n) for i, n in enumerate(mfr_models)],
            no_tags=True,
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        if not tag:
            return None
        selected_model = mfr_models[int(tag)]

        board_variants = tuple(
            sorted(
                set(
                    b.variant
                    for b in board
                    if b.manufacturer == selected_mfr and b.model == selected_model
                )
            )
        )
        if len(board_variants) > 1:
            code, tag = self._dialog.menu(
                "Select variant",
                choices=[(str(i), n) for i, n in enumerate(board_variants)],
                no_tags=True,
            )
            if code in (Dialog.CANCEL, Dialog.ESC):
                return None
            if not tag:
                return None
            selected_variant = board_variants[int(tag)]
        else:
            selected_variant = board_variants[0]
        return bdb.get_board(selected_mfr, selected_model, selected_variant)

    def _edit_int_setting(self, title: str, current_value: int) -> Tuple[str, int]:
        """Show an inputbox for editing a numeric setting. Returns (code, value)."""
        code, user_input = self._dialog.inputbox(
            f"Enter {title}:",
            init=str(current_value),
            width=60,
            height=15,
        )
        if code != Dialog.OK:
            return code, current_value

        try:
            new_value = int(user_input)
            if new_value < 0:
                self._dialog.msgbox(
                    f"Value must be non-negative (got {new_value}).\nUsing previous value.",
                    width=60,
                    height=15,
                )
                return Dialog.CANCEL, current_value
            return code, new_value
        except ValueError:
            self._dialog.msgbox(
                f"Invalid input '{user_input}'. Must be a positive integer.\nUsing previous value.",
                width=60,
                height=15,
            )
            return Dialog.CANCEL, current_value

    def settings_menu(self):
        """Display the settings dialog with CAN and UART rate options."""
        settings = InterfaceSettings()

        while True:
            self._dialog.set_background_title("Interface Settings")

            choices = [
                ("can_speed", f"CAN Bit Rate: {settings.can_speed} bps"),
                ("uart_baudrate", f"UART Baud Rate: {settings.uart_baudrate} bps"),
                ("back", "Exit to main menu"),
            ]

            code, tag = self._dialog.menu(
                "Communication Settings",
                choices=choices,
                cancel_label="Exit",
            )

            if code == Dialog.OK:
                if tag == "can_speed":
                    _, new_value = self._edit_int_setting("CAN speed (bps), default: 1000000", settings.can_speed)
                    if new_value != settings.can_speed:
                        try:
                            settings.can_speed = new_value
                            settings.save()
                            self._dialog.msgbox(
                                f"CAN Speed updated to {settings.can_speed} bps.\n",
                                width=60,
                                height=20,
                            )
                        except ValueError as e:
                            self._dialog.msgbox(str(e), width=60, height=15)
                elif tag == "uart_baudrate":
                    _, new_value = self._edit_int_setting("UART baud rate (bps), default: 250000", settings.uart_baudrate)
                    if new_value != settings.uart_baudrate:
                        try:
                            settings.uart_baudrate = new_value
                            settings.save()
                            self._dialog.msgbox(
                                f"UART Baud Rate updated to {settings.uart_baudrate} bps.\n",
                                width=60,
                                height=20,
                            )
                        except ValueError as e:
                            self._dialog.msgbox(str(e), width=60, height=15)
                elif tag == "back":
                    return True
            elif code in (Dialog.CANCEL, Dialog.ESC):
                return True

    def menus(self):
        while True:
            code, tag = self.main_menu()
            if code == Dialog.OK:
                if tag == "boardinfo":
                    board = self.select_board()
                    if not board:
                        continue
                    self._dialog.msgbox(board.pretty(), width=100, height=20)
                elif tag == "settings":
                    self.settings_menu()
                elif tag == "exit":
                    return 0
                elif tag == "crash":
                    raise RuntimeError("Intentional Crash")
                else:
                    raise KeyError("Unexpected response from main menu")
            elif code == Dialog.CANCEL:
                return 0
            else:
                ...
            # Don't care

    def launch(self):
        try:
            code = self._dialog.yesno(
                "This is WIP tooling.\n\nIt may eat your cat and/or firmware\nBe warned!",
                defaultno=True,
                yes_label="Risk It",
                no_label="Quit",
            )
            if code == Dialog.OK:
                self._check_katapult()
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
            human_text = (
                "The functionality you have tried to use has net yet been implemented\n"
            )
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
            fail_dialog = Dialog(dialog="dialog")
            fail_dialog.set_background_title("Fatal Error")
            fail_dialog.msgbox(
                f"A fatal error has occurred in EZFlash\n\n{human_text}\n\nThe program will now exit",
                width=0,
                height=0,
            )
            print("\033[H\033[2J")
        except Exception as gui_e:
            print("\033[H\033[2J")
            print(
                "A fatal error has occurred in EZFLASH, and the graphical crash handler also failed",
                file=stderr,
            )
            print("---Fault---", file=stderr)
            traceback.print_exception(gui_e, file=stderr)
            print("---END---\n", file=stderr)
            print(
                "We are sorry for the inconvenience, the program will now exit",
                file=stderr,
            )
        exit(1)
