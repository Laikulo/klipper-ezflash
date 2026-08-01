from sys import exit, stderr
import subprocess
import traceback
from pathlib import Path
from typing import Tuple, Optional

from ..model import BoardDatabase
from ..settings import InterfaceSettings, _get_settings_path
from ..util import find_katapult, _KATAPULT_CLONE_URL, find_klipper
from ..session import load_session, save_session, clear_session, SessionData
from .. import device as device_mod
from .. import flash as flash_mod
from .. import detect as detect_mod
from ..fault import EZFException

from dialog import Dialog


class UI(object):
    def __init__(self):
        self._state = 0
        self._dialog = Dialog(dialog="dialog", autowidgetsize=True)
        self._dialog.add_persistent_args(("--no-collapse",))
        self._katapult_checked = False
        # BoardDatabase instantiated at startup so any user-board load warnings can be
        # surfaced immediately to the user via a dialog.
        self._board_db = None

    def _check_katapult(self):
        """Check for katapult and refresh existing local checkouts.

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
            try:
                result = subprocess.run(
                    ["git", "-C", str(katapult_path), "pull"],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    error_msg = (result.stderr or f"Pull failed with exit code {result.returncode}").strip()
                    logger.warning(f"Failed to pull katapult: {error_msg}")
                    self._dialog.msgbox(
                        f"Unable to update katapult from git:\n\n{error_msg}",
                        width=70,
                        height=15,
                    )
            except FileNotFoundError:
                logger.error("git command not found — cannot update katapult")
                self._dialog.msgbox(
                    "git is not installed or not in your PATH.\n\n"
                    "Please install git and try again.",
                    width=70,
                    height=15,
                )
            except Exception as e:
                logger.error(f"Unexpected error updating katapult: {e}")
                self._dialog.msgbox(
                    f"An unexpected error occurred while updating katapult:\n\n{e}",
                    width=70,
                    height=15,
                )
            self._katapult_checked = True
            return

        # Katapult not found — prompt user to clone it
        self._katapult_checked = True
        code, tag = self._dialog.menu(
            "KATAPULT NOT FOUND\n\n"
            "Klipper-ezflash requires 'katapult' (a bootloader flash helper) to be installed.\n\n"
            f"It can be cloned from:\n  {_KATAPULT_CLONE_URL}",
            choices=[
                ("clone", "Clone katapult now"),
                ("quit", "Quit"),
            ],
            cancel_label="Quit",
        )

        if code == Dialog.OK:
            if tag == "clone":
                self._clone_katapult()

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
                ("flash", "Flash a board"),
                ("settings", "Edit the default communication settings"),
                ("crash", "Intentionally crash"),
                ("exit", "Exit"),
            ],
            cancel_label="Quit",
        )

    def select_board(self):
        # Re-use the DB instance created at startup if present, otherwise lazily create it
        if self._board_db is None:
            self._board_db = BoardDatabase()
        bdb = self._board_db
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

    def _edit_int_setting(self, title: str, current_value: int, *, input_title: Optional[str] = None) -> Tuple[str, int]:
        """Show an inputbox for editing a numeric setting. Returns (code, value)."""
        # Use input_title if provided; otherwise derive from title by stripping "Default X" suffixes
        clean_title = ' '.join(input_title.split()) if input_title else ' '.join(title.split()).replace("DEFAULT", "").strip()
        prompt = f"Enter a custom value for the {clean_title} (default: {current_value})"

        code, user_input = self._dialog.inputbox(
            prompt,
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

    def _choose_rate(self, title: str, current_value: int, options: list, *, input_title: Optional[str] = None) -> Tuple[str, int]:
        """Show a menu with predefined rate choices plus 'Custom'. Returns (code, value)."""

        preset_values = [v for _, v in options]
        max_width = max(len(str(v)) for _, v in options)

        def build_choices():
            choices = []
            is_custom = current_value not in preset_values
            custom_marker = "*" if is_custom else " "
            for label, value in options:
                selected_marker = "*" if value == current_value else " "
                # Tag (left column): "[X]value" where X is * or space — always 5 chars prefix
                choices.append((f"[{selected_marker}]  {str(value):>{max_width}}", label))
            choices.append((f"[{custom_marker}]  {'custom':>{max_width}}", "Enter a custom value"))
            return choices

        while True:
            code, tag = self._dialog.menu(
                title,
                choices=build_choices(),
                cancel_label="Cancel",
            )
            if code != Dialog.OK:
                return code, current_value

            # Strip the fixed 5-char prefix "[*]  " or "[ ]  " to recover the value string
            selected_tag = tag[5:]

            if selected_tag.strip() == "custom":
                _, new_value = self._edit_int_setting(title, current_value, input_title=input_title)
                if new_value != current_value:
                    return Dialog.OK, new_value
            else:
                return Dialog.OK, int(selected_tag)

    def settings_menu(self):
        """Display the settings dialog with CAN and UART rate options."""
        settings = InterfaceSettings()

        while True:
            self._dialog.set_background_title("Interface Settings")

            choices = [
                ("can_speed", f"CAN bit rate: {settings.can_speed} bps"),
                ("uart_baudrate", f"UART baud rate: {settings.uart_baudrate} baud"),
            ]

            # Only show CAN interface selection if more than one CAN interface is present
            try:
                can_list = detect_mod.list_can_interfaces()
            except Exception:
                can_list = []
            if len(can_list) > 1:
                choices.append(("can_interface", f"CAN interface: {settings.can_interface}"))

            choices.append(("back", "Exit to main menu"))

            code, tag = self._dialog.menu(
                "Communication Settings",
                choices=choices,
                cancel_label="Exit",
            )

            if code == Dialog.OK:
                if tag == "can_speed":
                    can_options = [
                        ("1M bps", 1000000),
                        ("500K bps", 500000),
                    ]
                    _, new_value = self._choose_rate(
                        "CAN bit rate selector:\nDefault 1M bps",
                        settings.can_speed,
                        can_options,
                        input_title="CAN bit rate",
                    )
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
                    uart_options = [
                        ("250K baud", 250000),
                        ("230.4K baud", 230400),
                        ("115.2K baud", 115200),
                    ]
                    _, new_value = self._choose_rate(
                        "UART baud rate selector:\nDefault 250K",
                        settings.uart_baudrate,
                        uart_options,
                        input_title="UART baud rate",
                    )
                    if new_value != settings.uart_baudrate:
                        try:
                            settings.uart_baudrate = new_value
                            settings.save()
                            self._dialog.msgbox(
                                f"UART Baud Rate updated to {settings.uart_baudrate} baud.\n",
                                width=60,
                                height=20,
                            )
                        except ValueError as e:
                            self._dialog.msgbox(str(e), width=60, height=15)
                elif tag == "can_interface":
                    # Let user pick from detected CAN interfaces
                    try:
                        can_list = detect_mod.list_can_interfaces()
                    except Exception:
                        can_list = []
                    if not can_list:
                        self._dialog.msgbox("No CAN interfaces detected on host.", width=60, height=8)
                        continue
                    choices = [(str(i), n) for i, n in enumerate(can_list)]
                    code, tag = self._dialog.menu(
                        "Select CAN interface to use:",
                        choices=choices,
                        cancel_label="Cancel",
                    )
                    if code != Dialog.OK:
                        continue
                    chosen = can_list[int(tag)]
                    try:
                        settings.can_interface = chosen
                        settings.save()
                        self._dialog.msgbox(f"CAN interface set to {chosen}", width=60, height=8)
                    except Exception as e:
                        self._dialog.msgbox(str(e), width=60, height=12)
                elif tag == "back":
                    return True
            elif code in (Dialog.CANCEL, Dialog.ESC):
                return True

    def start_flash_flow(self, resume_session: Optional[SessionData] = None):
        """Orchestrate the flash flow: compile -> detect bootloader -> flash -> post-detect.

        If `resume_session` is provided, attempt to continue from the saved step index.
        """
        session = resume_session
        board = None
        chosen_if_type = None

        if session is None:
            board = self.select_board()
            if not board:
                return
            # If the board exposes multiple interfaces, ask the user which to use
            ifaces = board.interfaces if hasattr(board, 'interfaces') else []
            if len(ifaces) > 1:
                choices = [(str(i), if_.pretty()) for i, if_ in enumerate(ifaces)]
                code, tag = self._dialog.menu(
                    "This board supports multiple interfaces. Which interface do you want to use?",
                    choices=choices,
                    cancel_label="Cancel",
                )
                if code != Dialog.OK:
                    return
                chosen_if_type = ifaces[int(tag)].if_type
            else:
                chosen_if_type = (ifaces[0].if_type if ifaces else None)

            session = SessionData(
                step_index=0,
                mcu_arch=board.mcu.arch,
                interface=(chosen_if_type.lower() if chosen_if_type else None),
                manufacturer=board.manufacturer,
                model=board.model,
                variant=board.variant,
            )
            save_session(session)
        else:
            chosen_if_type = session.interface

        if session.step_index >= 4:
            self._dialog.infobox(
                "A previously completed flash session was found. Starting fresh.",
                width=60,
                height=8,
            )
            try:
                clear_session()
            except Exception:
                pass
            return

        while True:
            try:
                if session.step_index <= 0: # Compile bootloader and firmware for the selected board and interface.
                    self._dialog.infobox(
                        "Compiling bootloader and firmware. This may take a while.",
                        width=60,
                        height=10,
                    )
                    from ..configurator import Configurator

                    # Look up board if missing (resume from session data)
                    if board is None:
                        try:
                            bdb = self._board_db or BoardDatabase()
                            self._board_db = bdb
                            board = next(
                                (b for b in bdb.get_all()
                                 if b.manufacturer == session.manufacturer
                                 and b.model == session.model
                                 and b.variant == session.variant),
                                None,
                            )
                        except Exception:
                            raise EZFException("Cannot locate board definition to rebuild firmware — start a new flash.")

                    bootloader_config = Configurator.katapult(find_katapult(), board=board)
                    firmware_config = Configurator.klipper(find_klipper(), board=board)
                    chosen_iface = next((i for i in board.interfaces if i.if_type == chosen_if_type), None)
                    if chosen_iface:
                        bootloader_config.set_interface(chosen_iface)
                        firmware_config.set_interface(chosen_iface)

                    bootloader_config_path = _get_settings_path().parent / "bootloader.config"
                    firmware_config_path = _get_settings_path().parent / "firmware.config"

                    bootloader_config.write_bootloader_config(board, bootloader_config_path)
                    bootloader_bin = flash_mod.compile_katapult(kconfig_config=bootloader_config_path)

                    firmware_config.write_firmware_config(board, firmware_config_path)
                    firmware_bin = flash_mod.compile_firmware(kconfig_config=firmware_config_path)

                if session.step_index <= 1: # Verify that the device is connected and in bootloader mode.
                    #TODO: dispay proper instructions for the user to put the device into bootloader mode based on bootloader type.
                    self._dialog.infobox(
                        "Waiting for bootloader to appear. Please put the device into bootloader mode.",
                        width=60,
                        height=8,
                    )
                    dev = device_mod.verify_bootloader_connected(session.mcu_arch)
                    # Validate against board (best-effort — board may be missing in resume)
                    try:
                        b = None
                        try:
                            if self._board_db is None:
                                self._board_db = BoardDatabase()
                            b = next(
                                (x for x in self._board_db.get_all() if x.mcu.arch == session.mcu_arch),
                                None,
                            )
                        except Exception:
                            b = None
                        if b is not None:
                            device_mod.validate_device(dev, b.mcu.arch)
                    except Exception as e:
                        self._dialog.msgbox(f"Device validation failed: {e}", width=80, height=12)
                        raise
                    session.detected_devices.append(dev)
                    session.step_index = 2
                    save_session(session)

                if session.step_index <= 2: # Actually flash the bootloader using katapult.
                    arch = (session.mcu_arch or "").lower()
                    self._dialog.infobox(
                        "Re-checking bootloader before flashing. Please keep the device connected.",
                        width=60,
                        height=10,
                    )
                    stable_dev = device_mod.verify_bootloader_connected(session.mcu_arch)
                    session.detected_devices.append(stable_dev)
                    save_session(session)
                    self._dialog.infobox(
                        f"Bootloader appears stable: {stable_dev.get('raw', '<unknown>')}\n\nStarting bootloader flash now.",
                        width=70,
                        height=12,
                    )
                    if "rp2040" in arch or "rp2" in arch:
                        flash_mod.flash_rp2040_katapult(bootloader_bin)
                    elif "stm32" in arch:
                        flash_mod.flash_stm32_katapult(bootloader_bin)
                    else:
                        raise EZFException("Unsupported MCU architecture for automatic katapult flash")
                    session.step_index = 3
                    save_session(session)

                if session.step_index <= 3: # Flash the firmware to the device (via CAN or USB)
                    iface = (session.interface or "").lower() if session.interface else "usb"
                    if "can" in iface:
                        can_list = detect_mod.list_can_interfaces()
                        if not can_list:
                            raise EZFException("No CAN network interfaces detected on host (e.g. can0).")
                        settings = InterfaceSettings()
                        if settings.can_interface and settings.can_interface in can_list:
                            chosen_can = settings.can_interface
                        elif len(can_list) == 1:
                            chosen_can = can_list[0]
                        else:
                            choices = [(str(i), n) for i, n in enumerate(can_list)]
                            code, tag = self._dialog.menu(
                                "Multiple CAN interfaces detected. Which one should be used?",
                                choices=choices,
                                cancel_label="Cancel",
                            )
                            if code != Dialog.OK:
                                raise EZFException("User cancelled CAN interface selection")
                            chosen_can = can_list[int(tag)]

                        uuids = detect_mod.detect_can_uuid(interface=chosen_can)
                        uid = uuids[0]
                        flash_mod.flash_firmware_via_flashtool(None, firmware_bin, "can", uid, can_iface=chosen_can)
                        self._dialog.infobox(f"Flashed firmware to CAN device UUID {uid}", width=80, height=8)
                    else:
                        devs = detect_mod.detect_usb_serial_by_id()
                        if not devs:
                            raise EZFException("No Klipper USB device found after flashing")
                        serial_path = str(devs[0])
                        flash_mod.flash_firmware_via_flashtool(None, firmware_bin, "serial", serial_path)
                        self._dialog.infobox(f"Flashed firmware to serial device {serial_path}", width=80, height=8)
                    session.step_index = 4
                    save_session(session)

                try:
                    clear_session()
                except Exception:
                    pass
                self._dialog.infobox("Flash flow completed successfully.", width=60, height=8)
                return
            except Exception as e:
                code = self._dialog.yesno(
                    f"An error occurred during the flash flow:\n\n{e}\n\nRetry from the current step?",
                    yes_label="Retry",
                    no_label="Abort",
                    defaultno=True,
                )
                if code == Dialog.OK:
                    continue
                try:
                    clear_session()
                except Exception:
                    pass
                self._dialog.msgbox(
                    "Flash aborted. The current session has been cleared.",
                    width=70,
                    height=12,
                )
                return

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
                elif tag == "flash":
                    # Start flash flow (new session)
                    try:
                        self.start_flash_flow()
                    except Exception as e:
                        # Show error and continue
                        self._dialog.msgbox(str(e), width=80, height=20)
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
            # On startup, check for an existing saved session and offer to resume
            try:
                existing = load_session()
            except Exception:
                existing = None
            if existing is not None:
                code, tag = self._dialog.menu(
                    "A previous flash session was detected.\n\nResume or start a new flash?",
                    choices=[("resume", "Resume previous flash"), ("new", "Start a new flash")],
                    cancel_label="Start new",
                )
                if code == Dialog.OK and tag == "resume":
                    # user chose resume — attempt to continue
                    try:
                        self.start_flash_flow(resume_session=existing)
                    except Exception as e:
                        self._dialog.msgbox(f"Failed to resume session: {e}", width=80, height=20)

            code = self._dialog.yesno(
                "This is WIP tooling.\n\nIt may eat your cat and/or firmware\nBe warned!",
                defaultno=True,
                yes_label="Risk It",
                no_label="Quit",
            )
            if code == Dialog.OK:
                # Instantiate the board database early so any user-board load warnings
                # can be shown to the user before they interact with the menus.
                try:
                    # Import the model module at runtime to ensure we get the latest BoardDatabase
                    import importlib
                    m = importlib.import_module('klipper_ezflash.model')
                    importlib.reload(m)
                    self._board_db = m.BoardDatabase()
                    warnings = self._board_db.get_load_warnings()
                    if warnings:
                        # Show a concise dialog summarizing the problems
                        max_show = 25
                        msg = "Issues detected while loading user_boards.json:\n\n"
                        # Present each issue on its own line and avoid repeating reasons
                        msg += "\n".join(warnings[:max_show])
                        if len(warnings) > max_show:
                            msg += f"\n\n...and {len(warnings)-max_show} more"
                        try:
                            # Ask the user whether to continue despite the warnings.
                            # Use yes=Continue, no=Quit semantics.
                            code = self._dialog.yesno(
                                msg + "\n\nDo you want to continue anyway?",
                                yes_label="Continue",
                                no_label="Quit",
                                defaultno=False,
                            )
                            if code != Dialog.OK:
                                # User chose to quit — exit the program cleanly
                                print("Exiting per user request.")
                                exit(0)
                        except Exception as dialog_exc:
                            # Dialog unavailable or failed; fall back to stderr so the
                            # user (or service manager) can see the warnings in logs.
                            print('\n' + '*** WARNING: Issues detected while loading user_boards.json ***', file=stderr)
                            for line in warnings:
                                print('- ' + line, file=stderr)
                            print('\n', file=stderr)
                            print('Non-interactive session: continuing despite the warnings.', file=stderr)
                        finally:
                            # Clear stored warnings so they are not shown again in-session
                            try:
                                self._board_db.clear_load_warnings()
                            except Exception:
                                pass
                except Exception:
                    # If DB instantiation fails, continue to menus
                    pass

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
