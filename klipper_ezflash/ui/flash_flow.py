"""
Flash flow orchestration — the FlashFlowMixin, meant to be composed into
EZFlashApp (see app.py) via `class EZFlashApp(FlashFlowMixin, App): ...`.

This is still Textual-coupled (calls self.msgbox/self.yesno/self.suspend/
self.push_screen directly throughout) — splitting it into its own file is
Phase 1 of the ui/ package split (see TODO's.md), a mechanical move with
no behavior change. Phase 2 (making the sequencing/retry/session logic
genuinely UI-independent, testable without a real terminal) is a separate,
larger follow-up: it would mean defining an abstract FlowUI protocol and
rewriting every self.msgbox(...)-style call here to go through it instead,
so this logic could live flat in the main package alongside
configurator.py/detect.py/session.py rather than in ui/ at all.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from ..model import BoardDatabase
from ..settings import InterfaceSettings
from ..util import find_katapult, find_klipper, file_sha256, check_can_host_state, fix_can_host_state, stop_services
from ..session import save_session, clear_session, SessionData
from .. import device as device_mod
from .. import flash as flash_mod
from .. import detect as detect_mod
from ..fault import EZFException, AmbiguousCanUuidException
from .flash_screen import FlashFlowScreen, CompletionScreen, StepStatus, _capture_output_to_log


def _build_mcu_snippet(*, klipper_serial: Optional[str] = None, uart_serial: Optional[str] = None, can_uuid: Optional[str] = None, katapult_serial: Optional[str] = None) -> Optional[str]:
    """Build a ready-to-paste printer.cfg [mcu] snippet for this flash session.

    `katapult_serial`, when available, is included as a comment above the
    serial:/canbus_uuid: line — the katapult (bootloader-mode) path is
    what future update_klipper_and_mcus-style re-flashes will need, and
    it's otherwise not shown anywhere in the final printer.cfg.
    """
    comment = f"# katapult path for updating {katapult_serial}\n" if katapult_serial else ""
    if can_uuid:
        return f"[mcu]\n{comment}canbus_uuid: {can_uuid}"
    if uart_serial:
        return f"[mcu]\n{comment}serial: {uart_serial}"
    if klipper_serial:
        return f"[mcu]\n{comment}serial: {klipper_serial}"
    return None


def _valid_cached_binary(path: Optional[Path], hash_str: Optional[str]) -> Optional[Path]:
    """Return `path` if it exists and its current content hash matches
    `hash_str`, else None (meaning: the caller should rebuild it).

    `path` is the deterministic expected output location (e.g. from
    flash.py's katapult_output_path()/klipper_output_path()) — recomputed
    fresh by the caller each time rather than trusted from a stored
    session value, since it's fully derivable from find_katapult()/
    find_klipper() and never needs to be persisted itself.
    """
    if path is None or not hash_str:
        return None
    if not path.exists():
        return None
    try:
        return path if file_sha256(path) == hash_str else None
    except OSError:
        return None


class FlashFlowMixin:
    """Flash-flow methods for EZFlashApp. Relies on the concrete class it's
    mixed into providing: self.msgbox/self.yesno/self.menu/self.button_menu
    (dialog helpers), self.suspend (Textual's terminal-handoff context
    manager), self.push_screen/self.pop_screen, self._board_db, and
    self._stopped_services."""

    FLASH_STEP_NAMES = [
        "Prepare config files",
        "Compile bootloader",
        "Compile firmware ",
        "Detect bootROM device",
        "Flash bootloader",
        "Flash firmware",
        "Finalizing",
    ]

    async def start_flash_flow(self, resume_session: Optional[SessionData] = None) -> None:
        session = resume_session
        board = None
        chosen_if_type = None

        if session is None:
            while True:
                board = await self.select_board()
                if not board:
                    return
                ifaces = board.interfaces if hasattr(board, "interfaces") else []
                ifaces = sorted(ifaces, key=lambda i: i.if_type)
                if len(ifaces) > 1:
                    tag = await self.menu(
                        choices=[(str(i), if_.pretty()) for i, if_ in enumerate(ifaces)],
                        message="This board supports multiple interfaces. Which interface do you want to use?",
                        cancel_label="Back",
                    )
                    if tag is None:
                        # Back to board selection, not out of the flash
                        # flow entirely — mirrors select_board()'s own
                        # step-back behavior instead of dropping the user
                        # back at the main menu.
                        continue
                    chosen_if_type = ifaces[int(tag)].if_type
                else:
                    chosen_if_type = ifaces[0].if_type if ifaces else None

                if chosen_if_type in ("CAN", "USB to CAN Bridge"):
                    can_settings = InterfaceSettings()
                    problems = await asyncio.to_thread(check_can_host_state, can_settings.can_speed)
                    if problems:
                        problem_list = "\n".join(f"  - {p}" for p in problems)
                        fix_now = await self.yesno(
                            f"Found issues with the host's CAN networking setup:\n\n{problem_list}\n\n"
                            "Fix these now? (sudo password may be required)\n\n"
                            "You can still flash without fixing these — useful if this board "
                            "belongs to a different CAN network/printer than the host is "
                            "currently configured for.",
                            yes_label="Fix",
                            no_label="Flash anyway",
                            default_no=True,
                        )
                        if fix_now:
                            try:
                                with self.suspend():
                                    print("\nApplying host CAN networking changes — you may be prompted for your sudo password below.\n")
                                    fix_can_host_state(can_settings.can_speed)
                                await self.msgbox("Host CAN networking has been updated.", title="CAN host state")
                            except Exception as e:
                                await self.msgbox(f"Failed to fix host CAN networking:\n\n{e}", title="CAN host state")

                # Confirmation step: show the full board summary (including
                # its interface list) plus the interface just chosen, and
                # let the user either proceed or go back and pick again
                # before we commit to a session / start compiling anything.
                # Rendered as buttons (not a scrollable list) since it's
                # just two fixed, equally-weighted choices; no separate
                # Cancel button — Escape still backs all the way out.
                iface_label = chosen_if_type or "(none available)"
                speed_line = ""
                if chosen_if_type and chosen_if_type in ("CAN", "USB to CAN Bridge"):
                    speed_line = f"\nCAN bit rate: {InterfaceSettings().can_speed} bps"
                elif chosen_if_type and chosen_if_type == "UART":
                    speed_line = f"\nUART baud rate: {InterfaceSettings().uart_baudrate} baud"
                summary = f"{board.pretty()}\nSelected interface: {iface_label}{speed_line}\n"
                tag = await self.button_menu(
                    choices=[("begin", "Begin Flash")],
                    message=summary,
                    title="Confirm Board & Interface",
                    cancel_label="Back",
                )
                if tag == "begin":
                    break
                else:
                    # "reselect" or Escape/Back (tag is None) both mean the
                    # same thing here: go back to reselecting board/
                    # interface rather than abandoning the flash flow
                    # entirely, mirroring the step-back convention used
                    # everywhere else in this wizard.
                    continue

            # Capture the currently-configured speed for this interface
            # into the session now — so a resumed session (e.g. after a
            # reboot to rewire a CAN toolhead) flashes at the same speed
            # it was set up with, even if the global default in Settings
            # gets changed in the meantime.
            interface_speed = None
            if chosen_if_type:
                _settings_for_speed = InterfaceSettings()
                if chosen_if_type in ("CAN", "USB to CAN Bridge"):
                    interface_speed = _settings_for_speed.can_speed
                elif chosen_if_type == "UART":
                    interface_speed = _settings_for_speed.uart_baudrate

            session = SessionData(
                step_index=0,
                mcu_arch=board.mcu.arch,
                interface=(chosen_if_type if chosen_if_type else None),
                interface_speed=interface_speed,
                manufacturer=board.manufacturer,
                model=board.model,
                variant=board.variant,
            )
            save_session(session)
        else:
            # session.interface is stored lowercased (see below), but
            # every comparison against chosen_if_type elsewhere in this
            # method compares it against board.interfaces' if_type values,
            # which are uppercase ("USB"/"CAN"/"USB to CAN Bridge"/"UART"). Without
            # normalizing case here, a resumed session's chosen_if_type
            # never matches anything, bootloader_iface/firmware_iface both
            # come back None, set_interface() never gets called at all, and
            # the Configurator silently falls back to Klipper's own kconfig
            # default (USB) — exactly the "board gets built for USB mode"
            # symptom this fixes.
            chosen_if_type = session.interface if session.interface else None

        if session.step_index >= 6:
            await self.msgbox("A previously completed flash session was found. Starting fresh.")
            try:
                clear_session()
            except Exception:
                pass
            return

        if not await self.stop_services_for_flash():
            return

        screen = FlashFlowScreen(self.FLASH_STEP_NAMES)
        await self.push_screen(screen)
        # Sub-step-4 progress that must survive a "Retry" (the outer
        # except/continue below) without being redone: once the firmware
        # flash itself has actually succeeded, a retry should only retry
        # whatever DETECTION step failed afterward (like if there are
        # mutiple CAN devices on the bus after flashing USB to CAN Bridge).
        # At that point, the board is no longer reachable in katapult mode
        # at all and a repeat "Waiting for the device to reboot into
        # Katapult..." can only ever time out. Declared once here (not
        # reset per while-loop iteration like the rest of the per-attempt
        # state below) so it persists exactly across in-process retries of
        # THIS flash attempt, and naturally resets for any genuinely new
        # start_flash_flow() call.
        firmware_flashed = False
        known_klipper_devs: Optional[list] = None
        known_can_ifaces: Optional[list] = None
        detected_can_iface: Optional[str] = None
        katapult_serial_path: Optional[str] = None
        uart_port: Optional[str] = None
        try:
            while True:
                try:
                    bootloader_bin = firmware_bin = None
                    klipper_path = can_uid = None
                    known_katapult_devs: Optional[list] = None

                    if session.step_index <= 0:
                        katapult_path = find_katapult()
                        if not katapult_path:
                            raise EZFException("Katapult checkout not found — cannot configure or compile")
                        # Store for resume sessions
                        session.katapult_path = str(katapult_path)
                        firmware_path = find_klipper()
                        if not firmware_path:
                            raise EZFException("Klipper checkout not found — cannot configure or compile")

                        cached_bootloader = _valid_cached_binary(
                            flash_mod.katapult_output_path(katapult_path), session.bootloader_bin_hash
                        )
                        cached_firmware = _valid_cached_binary(
                            flash_mod.klipper_output_path(firmware_path), session.firmware_bin_hash
                        )

                        # Board/Configurator setup + writing the .config
                        # files is only needed if we're actually
                        # recompiling something — a fully-cached resume
                        # (e.g. the user rewired a CAN toolhead board and
                        # restarted the app, with no other build having
                        # touched these outputs since) skips this
                        # entirely.
                        bootloader_config = firmware_config = None
                        if cached_bootloader is None or cached_firmware is None:
                            screen.start_step(0)
                            from ..configurator import Configurator

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

                            with _capture_output_to_log(screen):
                                bootloader_config = Configurator.katapult(katapult_path, board=board)
                                firmware_config = Configurator.klipper(firmware_path, board=board)
                                # Katapult (the bootloader) always builds for plain USB, even
                                # when the overall flash is USB to CAN Bridge — USB to CAN Bridge is a
                                # Klipper-firmware comms mode (the running firmware presents
                                # itself as a USB-CAN adapter), not something the bootloader
                                # itself needs to know about. The device must still be a
                                # normal USB DFU/bootloader target to receive the firmware
                                # flash in the first place.
                                bootloader_if_type = "USB" if chosen_if_type == "USB to CAN Bridge" else chosen_if_type
                                bootloader_iface = next((i for i in board.interfaces if i.if_type == bootloader_if_type), None)
                                firmware_iface = next((i for i in board.interfaces if i.if_type == chosen_if_type), None)
                                if bootloader_iface:
                                    bootloader_config.set_interface(bootloader_iface)
                                    if bootloader_iface.if_type in ("CAN", "UART") and session.interface_speed:
                                        bootloader_config.set_interface_speed(session.interface_speed)
                                elif bootloader_if_type == "USB":
                                    # No USB interface declared in boards.json (e.g. a
                                    # CAN-bridge-only board like the Birds Nest CAN) — Katapult
                                    # still needs a comms mode to build for, so fall back to the
                                    # kconfig's own default/generic USB choice. This only raises
                                    # if the kconfig itself has no USB option at all.
                                    bootloader_config.set_usb_default()
                                if firmware_iface:
                                    firmware_config.set_interface(firmware_iface)
                                    if firmware_iface.if_type in ("CAN", "UART", "USB to CAN Bridge") and session.interface_speed:
                                        firmware_config.set_interface_speed(session.interface_speed)

                                # Prepare ends once the kconfigs are
                                # written to disk — compiling starts
                                # clean from here.
                                if cached_bootloader is None:
                                    bootloader_config.save_config(katapult_path / ".config")
                                if cached_firmware is None:
                                    firmware_config.save_config(firmware_path / ".config")
                            screen.set_step(0, StepStatus.DONE)
                        else:
                            screen.start_step(0, "Bootloader and firmware outputs unchanged since last run — nothing to prepare.")
                            screen.set_step(0, StepStatus.DONE)

                        # --- Step 1: compile bootloader (skip if the
                        # cached output from a previous run is still
                        # byte-for-byte unchanged) ---
                        if cached_bootloader is not None:
                            screen.start_step(1, f"Bootloader output unchanged since last run — skipping recompile.\n{cached_bootloader}")
                            bootloader_bin = cached_bootloader
                            screen.set_step(1, StepStatus.DONE)
                        else:
                            screen.start_step(1)
                            await screen.run_argv("Cleaning katapult...", flash_mod.MAKE_CLEAN_ARGS, cwd=katapult_path)
                            await screen.run_argv("Compiling katapult...", flash_mod.MAKE_BUILD_ARGS, cwd=katapult_path)
                            bootloader_bin = flash_mod.katapult_output_path(katapult_path)
                            if not bootloader_bin.exists():
                                raise EZFException(f"Expected katapult binary not found at {bootloader_bin}")
                            session.bootloader_bin_hash = file_sha256(bootloader_bin)
                            save_session(session)
                            screen.set_step(1, StepStatus.DONE)

                        # --- Step 2: compile firmware (skip if the
                        # cached output from a previous run is still
                        # byte-for-byte unchanged) ---
                        if cached_firmware is not None:
                            screen.start_step(2, f"Firmware output unchanged since last run — skipping recompile.\n{cached_firmware}")
                            firmware_bin = cached_firmware
                            screen.set_step(2, StepStatus.DONE)
                        else:
                            screen.start_step(2)
                            await screen.run_argv("Cleaning klipper...", flash_mod.MAKE_CLEAN_ARGS, cwd=firmware_path)
                            await screen.run_argv("Compiling firmware...", flash_mod.MAKE_BUILD_ARGS, cwd=firmware_path)
                            firmware_bin = flash_mod.klipper_output_path(firmware_path)
                            if firmware_bin is None:
                                candidates = ", ".join(str(p) for p in flash_mod.klipper_output_candidates(firmware_path))
                                raise EZFException(f"Expected klipper firmware at one of {candidates}")
                            session.firmware_bin_hash = file_sha256(firmware_bin)
                            save_session(session)
                            screen.set_step(2, StepStatus.DONE)

                    if session.step_index <= 3:
                        arch = (session.mcu_arch or "").lower()

                        # If a matching bootloader device is already sitting
                        # connected (e.g. a power user who put the board into
                        # bootloader mode before starting this flash), skip
                        # the "go put your board into bootloader mode"
                        # instructions entirely — they'd just be asking the
                        # user to redo something already done. Only shown
                        # when nothing matching is present yet.
                        already_present = await asyncio.to_thread(
                            device_mod.check_bootloader_already_present, session.mcu_arch
                        )
                        if already_present is None:
                            if "rp2040" in arch or "rp2" in arch:
                                bootloader_hint = (
                                    "Hold the BOOTSEL button while plugging the board in "
                                    "(or hold BOOTSEL and tap RESET) to enter RP2 bootloader mode."
                                )
                            elif "stm32" in arch:
                                bootloader_hint = (
                                    "Set the BOOT0 jumper/pin and power-cycle the board "
                                    "to enter STM32 DFU mode."
                                )
                            else:
                                bootloader_hint = "Put the board into its bootloader mode."
                            await self.msgbox(
                                f"Prepare the board for flashing:\n\n{bootloader_hint}\n\n"
                                "Once it's in bootloader mode, press OK to continue.",
                                title="Bootloader Mode Required",
                            )

                        # Either way, still confirm via verify_bootloader_connected
                        # below — its settle/recheck window is what actually
                        # debounces a device that just appeared (or that was
                        # already present but about to disappear/re-enumerate),
                        # rather than trusting either check on its own.
                        screen.start_step(3, "Re-checking bootloader before flashing. Please keep the device connected.")
                        stable_dev = await asyncio.to_thread(device_mod.verify_bootloader_connected, session.mcu_arch)
                        session.detected_devices.append(stable_dev)
                        save_session(session)
                        screen.write(f"Bootloader appears stable: {stable_dev.get('raw', '<unknown>')}")

                        # Not ambiguous (verify_bootloader_connected already
                        # guarantees exactly one device matching this arch),
                        # but a bootloader device of a *different*
                        # architecture also being present is easy to miss
                        # and could mean the wrong board was left connected
                        # or the wrong board was selected in the app.
                        other_devices = stable_dev.get("other_devices") or []
                        if other_devices:
                            other_list = "\n".join(f"  - {d.get('raw', '<unknown>')}" for d in other_devices)
                            screen.write(f"Note: other bootloader device(s) also detected:\n{other_list}")
                            await self.msgbox(
                                "Another bootloader-mode device (different architecture) was "
                                f"also detected:\n\n{other_list}\n\n"
                                "This won't affect which device gets flashed, but double-check "
                                "you selected the right board before continuing.",
                                title="Other bootloader device detected",
                            )

                        screen.set_step(3, StepStatus.DONE)

                        screen.start_step(4)
                        # Snapshot any katapult_ devices already present
                        # BEFORE flashing — this is what lets us wait for
                        # the newly re-enumerated device specifically
                        # afterward, rather than matching any katapult_
                        # device including a leftover one from another
                        # board's earlier session.
                        known_katapult_devs = await asyncio.to_thread(
                            detect_mod.detect_usb_serial, lambda p: "katapult_" in p.name
                        )
                        if "rp2040" in arch:
                            argv, cwd = flash_mod.build_rp2040_flash_cmd(bootloader_bin)
                            await screen.run_argv("Flashing bootloader (rp2040)...", argv, cwd=cwd)
                        elif "stm32" in arch:
                            argv = flash_mod.build_stm32_flash_cmd(bootloader_bin)
                            await screen.run_argv_check_patterns(
                                "Flashing bootloader (stm32)...", argv, flash_mod.STM32_SUCCESS_PATTERNS,
                            )
                        else:
                            raise EZFException("Unsupported MCU architecture for automatic katapult flash")
                        screen.set_step(4, StepStatus.DONE)

                        session.step_index = 4
                        save_session(session)

                    if session.step_index <= 4:
                        screen.start_step(5)
                        iface = (session.interface or "USB")
                        is_canbridge = "USB to CAN Bridge" in iface
                        is_can_only = ("CAN" in iface) and not is_canbridge
                        is_uart = "UART" in iface

                        if is_can_only:
                            can_list = await asyncio.to_thread(detect_mod.list_can_interfaces)
                            if not can_list:
                                raise EZFException("No CAN network interfaces detected on host (e.g. can0).")
                            settings = InterfaceSettings()
                            if settings.can_interface and settings.can_interface in can_list:
                                chosen_can = settings.can_interface
                            elif len(can_list) == 1:
                                chosen_can = can_list[0]
                            else:
                                tag = await self.menu(
                                    choices=[(str(i), n) for i, n in enumerate(can_list)],
                                    message="Multiple CAN interfaces detected. Which one should be used?",
                                )
                                if tag is None:
                                    raise EZFException("User cancelled CAN interface selection")
                                chosen_can = can_list[int(tag)]

                            screen.write(f"Waiting for a katapult-mode device on {chosen_can}....")
                            result = await asyncio.to_thread(
                                detect_mod.wait_for_can_uuid, chosen_can, want_type="katapult", timeout=30.0
                            )
                            if not result:
                                raise EZFException(
                                    f"No katapult-mode CAN device found on {chosen_can} after 30s.\n\n"
                                    "Make sure the board's bootloader actually flashed and that "
                                    "it's on this CAN network — already-running/klipper-mode "
                                    "devices on the bus are ignored on purpose."
                                )
                            can_uid, _fw_type = result
                            screen.write(f"Detected katapult-mode CAN device: UUID {can_uid} on {chosen_can}")
                            argv = flash_mod.build_flashtool_cmd(
                                None, firmware_bin, "can", can_uid, chosen_can,
                                interface_speed=session.interface_speed,
                            )
                            await screen.run_argv("Flashing firmware over CAN...", argv)
                        elif is_uart:
                            # UART goes through a generic USB-to-serial
                            # adapter (FTDI, CH340, etc.) wired to the
                            # board's UART pins — there's no
                            # "katapult_"/"Klipper_" style name to
                            # auto-match against the way a board's own
                            # native USB port has, so the user has to
                            # pick which serial device it actually is
                            # (or let us probe for it).
                            screen.write(
                                "UART interface selected — this board's bootloader can't be "
                                "auto-detected over a generic serial adapter; select the port "
                                "connected to its UART pins below."
                            )
                            ports = await asyncio.to_thread(detect_mod.list_serial_ports)
                            if not ports:
                                raise EZFException(
                                    "No serial devices found. Connect the USB-to-UART adapter "
                                    "wired to this board's UART pins and try again."
                                )
                            if len(ports) == 1:
                                chosen_port = ports[0]
                            else:
                                speed = session.interface_speed or InterfaceSettings().uart_baudrate
                                probe_results: dict = {p: None for p in ports}
                                try_auto = await self.yesno(
                                    f"{len(ports)} serial devices detected.\n\n"
                                    "Try to auto-detect which one is the board by briefly "
                                    "probing each port for a Katapult bootloader response? "
                                    "This only works if the board is currently sitting in "
                                    "bootloader mode.",
                                    yes_label="Auto-detect",
                                    no_label="Select manually",
                                    default_no=True,
                                )
                                chosen_port = None
                                if try_auto:
                                    matches = []
                                    for p in ports:
                                        screen.write(f"Probing {p}...")
                                        ok = await asyncio.to_thread(
                                            detect_mod.probe_katapult_status, p, firmware_bin, speed
                                        )
                                        probe_results[p] = ok
                                        screen.write(f"  {p}: {'Katapult detected' if ok else 'no response'}")
                                        if ok:
                                            matches.append(p)
                                    if len(matches) == 1:
                                        chosen_port = matches[0]
                                    elif not matches:
                                        screen.write("Auto-detect found no matching device — falling back to manual selection.")
                                    else:
                                        screen.write(f"Auto-detect found {len(matches)} matching devices — falling back to manual selection.")

                                if chosen_port is None:
                                    def _label(p):
                                        friendly = detect_mod.describe_serial_port(p)
                                        result = probe_results.get(p)
                                        if result is True:
                                            return f"{friendly} (Katapult detected)"
                                        elif result is False:
                                            return f"{friendly} (no response)"
                                        return friendly
                                    tag = await self.menu(
                                        choices=[(str(i), _label(p)) for i, p in enumerate(ports)],
                                        message="Multiple serial devices detected. Select the one "
                                                "connected to this board's UART pins:",
                                    )
                                    if tag is None:
                                        raise EZFException("User cancelled serial port selection")
                                    chosen_port = ports[int(tag)]

                            speed = session.interface_speed or InterfaceSettings().uart_baudrate
                            argv = flash_mod.build_flashtool_cmd(
                                None, firmware_bin, "uart", str(chosen_port), interface_speed=speed
                            )
                            await screen.run_argv(f"Flashing firmware via UART ({chosen_port})...", argv)
                            screen.write(f"Flashed firmware over UART: {chosen_port}")
                            uart_port = str(chosen_port)
                        else:
                            # Both plain USB and USB to CAN Bridge reach the firmware
                            # flash the same way at this point: the board is
                            # still sitting in Katapult bootloader mode over
                            # USB serial (Katapult itself was always built
                            # for plain USB, per the bootloader/firmware
                            # config split above) — USB to CAN Bridge-mode comms only
                            # exist once the USB to CAN Bridge-configured firmware
                            # itself is running, which hasn't happened yet.
                            if not firmware_flashed:
                                screen.write("Waiting for the device to reboot into Katapult....")
                                katapult_dev = await asyncio.to_thread(
                                    detect_mod.wait_for_katapult_device, known=known_katapult_devs, timeout=10.0
                                )
                                if not katapult_dev:
                                    raise EZFException(
                                        "No Katapult serial device found after flashing!\n\n"
                                        "Please ensure the reset jumpers are removed and that the "
                                        "status LED is flashing if available."
                                    )
                                katapult_serial_path = str(katapult_dev)

                                argv = flash_mod.build_flashtool_cmd(None, firmware_bin, "serial", katapult_serial_path)

                                if is_canbridge:
                                    # Snapshot existing CAN interfaces BEFORE
                                    # flashing, so the newly-appeared one (the
                                    # board itself, once it reboots running
                                    # USB to CAN Bridge-mode firmware and enumerates as
                                    # a USB-CAN adapter) can be told apart from
                                    # any that were already present.
                                    known_can_ifaces = await asyncio.to_thread(detect_mod.list_can_interfaces)
                                else:
                                    known_klipper_devs = await asyncio.to_thread(detect_mod.detect_usb_serial, lambda p: "Klipper_" in p.name)
                                await screen.run_argv("Flashing firmware via Katapult...", argv)
                                firmware_flashed = True
                        screen.set_step(5, StepStatus.DONE)

                        # --- Step 6: Finalizing (wait for the board to
                        # come back up under its new firmware, then build
                        # and show the completion message). CAN-only and
                        # UART flashes have nothing further to wait for —
                        # the flash command itself was the last thing
                        # that could fail — so this step is effectively
                        # instant for those interfaces. ---
                        screen.start_step(6)
                        if not is_can_only and not is_uart:
                            if is_canbridge:
                                if detected_can_iface is None:
                                    screen.write("Waiting for the board to reboot as a CAN-bridge network interface...")
                                    detected_can_iface = await asyncio.to_thread(
                                        detect_mod.wait_for_new_can_interface, known=known_can_ifaces, timeout=15.0
                                    )
                                    if not detected_can_iface:
                                        raise EZFException(
                                            "No new CAN network interface appeared after flashing.\n\n"
                                            "Check the host's CAN networking setup (Main Menu -> "
                                            "Prepare host OS for CAN) and the board's connection."
                                        )
                                screen.write(f"Waiting for the flashed firmware to appear as a klipper-mode CAN device on {detected_can_iface}....")
                                try:
                                    result = await asyncio.to_thread(
                                        detect_mod.wait_for_can_uuid, detected_can_iface, want_type="klipper", timeout=30.0
                                    )
                                except AmbiguousCanUuidException as e:
                                    screen.write(
                                        f"Multiple klipper-mode CAN devices found at once "
                                        f"({len(e.candidates)}) — attempting to identify which one "
                                        "is the board just flashed..."
                                    )
                                    resolved_uuid = await self.resolve_can_ambiguity(
                                        e.candidates, board, detected_can_iface, firmware_path
                                    )
                                    if resolved_uuid is None:
                                        raise EZFException(
                                            "Could not determine which CAN device is the board just "
                                            "flashed, and no selection was made."
                                        )
                                    screen.write(f"Resolved to UUID {resolved_uuid}")
                                    result = (resolved_uuid, "klipper")
                                if not result:
                                    raise EZFException(
                                        f"No klipper-mode CAN device found on the new interface "
                                        f"{detected_can_iface} after flashing.\n\nThe board may not have "
                                        "booted into its newly-flashed firmware correctly."
                                    )
                                can_uid, _fw_type = result
                                screen.write(f"Detected klipper-mode CAN-bridge device: UUID {can_uid} on {detected_can_iface}")
                            else:
                                screen.write("Waiting for the device to reboot and reappear as a Klipper device....")
                                klipper_dev = await asyncio.to_thread(detect_mod.wait_for_klipper_device, known=known_klipper_devs, timeout=10.0)
                                if not klipper_dev:
                                    raise EZFException("No Klipper device detected after flashing.\n\nPlease check the connection.")
                                klipper_path = str(klipper_dev)
                        screen.set_step(6, StepStatus.DONE)
                        session.step_index = 6
                        save_session(session)

                    try:
                        clear_session()
                    except Exception:
                        pass

                    # Build the interface-specific device summary here
                    # (rather than mid-flash, where a screen.write() got
                    # immediately covered by this very dialog and then lost
                    # entirely once the flash screen was popped afterward).
                    # Putting it in the completion dialog's own message
                    # means it's guaranteed visible and requires an
                    # explicit OK, instead of possibly flashing by unread.
                    iface = (session.interface or "USB")
                    is_canbridge = "USB to CAN Bridge" in iface
                    is_can_only = ("CAN" in iface) and not is_canbridge
                    is_uart = "UART" in iface
                    config_snippet = None
                    warning_lines = []
                    if is_canbridge:
                        if can_uid:
                            config_snippet = _build_mcu_snippet(can_uuid=can_uid, katapult_serial=katapult_serial_path)
                    elif is_can_only:
                        if can_uid:
                            config_snippet = _build_mcu_snippet(can_uuid=can_uid)
                    elif is_uart:
                        if uart_port:
                            config_snippet = _build_mcu_snippet(uart_serial=uart_port)
                            # /dev/serial/by-path/ encodes USB topology (which
                            # physical port/hub the adapter is plugged into),
                            # not the adapter's own identity — unlike by-id,
                            # this path can change later if the adapter gets
                            # moved to a different port or a hub is added or
                            # removed, silently breaking printer.cfg's serial:
                            # line at that point.
                            if "/by-path/" in uart_port:
                                warning_lines.append(
                                    "Note: this is a /dev/serial/by-path/ path, which reflects USB "
                                    "topology rather than the adapter itself. If the port ordering "
                                    "changes later (adapter moved to a different port, a hub "
                                    "added/removed), this path can change and printer.cfg's serial: "
                                    "line will need updating to match."
                                )
                    else:
                        if klipper_path:
                            config_snippet = _build_mcu_snippet(klipper_serial=klipper_path, katapult_serial=katapult_serial_path)

                    final_parts = ["Flash flow completed successfully."]
                    if config_snippet:
                        final_parts.append(
                            "To add this MCU into your printer.cfg, copy the following section into "
                            "the config. Note, if this is not the main MCU, you will need to add a "
                            "name for the MCU such as [mcu toolhead]"
                        )
                        final_parts.append("\n" + config_snippet + "\n")
                    final_parts.extend(warning_lines)
                    final_text = "\n\n".join(final_parts)

                    screen.write(final_text)
                    await self.push_screen_wait(CompletionScreen(final_text, config_snippet=config_snippet))
                    return

                except Exception as e:
                    for i in range(len(self.FLASH_STEP_NAMES)):
                        # mark the step that failed (best-effort — whichever is ACTIVE)
                        if screen.step_list.status(i) == StepStatus.ACTIVE:
                            screen.set_step(i, StepStatus.ERROR)
                    retry = await self.yesno(
                        f"An error occurred during the flash flow:\n\n{e}\n\nRetry from the current step?",
                        yes_label="Retry",
                        no_label="Abort",
                        default_no=True,
                    )
                    if retry:
                        continue
                    try:
                        clear_session()
                    except Exception:
                        pass
                    await self.msgbox("Flash aborted. The current session has been cleared.")
                    return
        finally:
            self.pop_screen()

    async def stop_services_for_flash(self) -> bool:
        """Offer to stop Klipper before touching any hardware for this 
        flash — called right before the flash screen is created, the
        common point for both a fresh flash (just confirmed via "Begin
        Flash") and a resumed session, since either path is about to
        start detecting/talking to the device.

        Only checks/prompts once per app run: if we've already stopped
        services earlier this session, later flashes skip straight
        through (no point re-stopping something already stopped, and no
        point re-prompting either). If the user declines, this returns
        False and the caller aborts the flash entirely — Klipper actively
        holding the device, or generating its own CAN traffic, can
        interfere with detection/flashing in ways that are hard to
        diagnose after the fact, so "flash anyway" isn't offered here the
        way it is for the CAN host-state check.

        Returns True if it's fine to proceed (services stopped, none were
        running, or already stopped earlier this run), False if the user
        cancelled or stopping them failed.
        """
        if self._stopped_services:
            return True
        active = [
            name for name in ("klipper",)
            if subprocess.run(["systemctl", "is-active", "--quiet", name]).returncode == 0
        ]
        if not active:
            return True
        proceed = await self.button_menu(
            choices=[
                ("stop", "Stop service(s) and flash"),
                ("skip", "Flash without stopping"),
                ("cancel", "Cancel flash"),
            ],
            message=(
                f"Warning:\n\n"
                f"{' and '.join(s.capitalize() for s in active)} "
                f"{'is' if len(active) == 1 else 'are'} currently running and it is recommended to stop "
                f"{'it' if len(active) == 1 else 'them'} before flashing a board to prevent detection "
                f"conflicts.\n\n"
                "DO NOT STOP or flash while a print is running! Stopping the service(s) "
                "will automatically crash any prints in progress."
            ),
            title="Warning",
        )
        if proceed == "skip":
            return True
        if proceed != "stop":
            return False
        try:
            with self.suspend():
                print("\nStopping service(s) — you may be prompted for your sudo password below.\n")
                stopped = stop_services(active)
            self._stopped_services.extend(stopped)
            return True
        except Exception as e:
            await self.msgbox(f"Failed to stop service(s):\n\n{e}", title="Error")
            return False

    async def resolve_can_ambiguity(
        self, candidates: list[tuple[str, str]], board, interface: str, klipper_path: Path
    ) -> Optional[str]:
        """Given multiple same-typed CAN UUIDs found at once, try to
        auto-resolve which one is actually the board being flashed by
        querying each candidate's MCU config over the real klipper
        protocol (identify_can_device) and checking whether it reports
        CANBUS_BRIDGE (i.e. it's actually running USB-to-CAN-bridge
        firmware — normally only the board just flashed will be) and
        whether its RESERVE_PINS_CAN matches this board's actual CAN
        pins from boards.json exactly (a set comparison — order in the
        Kconfig-reported string isn't guaranteed to match boards.json's
        rx/tx order).

        Falls back to asking the user — showing whatever identify info
        was gathered for each candidate — if auto-resolution doesn't
        land on exactly one match. This includes the case where `board`
        is None (a fully-cached resumed session skips board
        reconstruction entirely) or identify_can_device itself fails for
        some/all candidates: a failed query is treated as "unknown", not
        as a negative match, so it never silently excludes the right
        device — it just can't be auto-confirmed.
        """
        info: dict[str, Optional[dict]] = {}
        matches: list[str] = []
        for uuid, _fw_type in candidates:
            try:
                constants = await asyncio.to_thread(
                    detect_mod.identify_can_device, uuid, interface, klipper_path
                )
            except Exception:
                constants = None
            info[uuid] = constants
            if constants and constants.get("CANBUS_BRIDGE") and board is not None and board.can:
                reserved = {
                    p.strip().upper()
                    for p in str(constants.get("RESERVE_PINS_CAN", "")).split(",")
                    if p.strip()
                }
                board_pins = {board.can.rx_pin.upper(), board.can.tx_pin.upper()}
                if reserved == board_pins:
                    matches.append(uuid)

        if len(matches) == 1:
            return matches[0]

        choices = []
        for i, (uuid, fw_type) in enumerate(candidates):
            constants = info.get(uuid)
            if constants:
                mcu = constants.get("MCU", "?")
                bridge = "bridge" if constants.get("CANBUS_BRIDGE") else "non-bridge"
                reset_status = constants.get("_reset_status", "?")
                label = f"{uuid} ({mcu}, {bridge}, reset: {reset_status})"
            else:
                label = f"{uuid} (could not identify)"
            choices.append((str(i), label))
        tag = await self.menu(
            choices=choices,
            message="Multiple matching CAN devices detected. Which one is the board being flashed?",
        )
        if tag is None:
            return None
        return candidates[int(tag)][0]
