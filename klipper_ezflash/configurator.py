import re
import logging
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Optional

from .kconfig import KConfig, KConfigChoice
from .model import BoardDefinition, BoardInterfaceDefinition
from .util import table_munge

logger = logging.getLogger(__name__)
FREQ_IN_RE = re.compile("([0-9]+)([MK]hz)", flags=re.IGNORECASE)

_ARCH_MUNGES = (
    ("rp2040", "rp235x", "Raspberry Pi RP2040", "Raspberry Pi RP2040/RP235x"),
)

_PROMPT_MUNGES = (("Communication interface", "Communications interface"),)


class Configurator(object):
    def __init__(self, source_path: PathLike, board: BoardDefinition):
        self.source_path = source_path
        self.kconfig = KConfig(source_path)
        self._board = board
        # We always set the below, because tons of stuff is missing otherwise
        if extra_config := self.kconfig.symbol(
            prompt="Enable extra low-level configuration options"
        ):
            extra_config.set(True)
        self._load_from_board(board)

    @classmethod
    def katapult(cls, source_path: PathLike, board: BoardDefinition) -> "Configurator":
        configurator = cls(source_path, board)
        configurator._configure_bootloader(board)
        return configurator

    @classmethod
    def klipper(cls, source_path: PathLike, board: BoardDefinition) -> "Configurator":
        configurator = cls(source_path, board)
        configurator._configure_firmware(board)
        return configurator

    def _load_from_board(self, board):
        self.set_arch(board.mcu.arch)
        self.set_mcu(board.mcu.mcu)
        if clock := board.mcu.clock:
            self.set_freq(clock)
        # Note: we don't want to set flash type if we don't have a "stage 2". Which we don't have if there is a bootloader.
        # RP2040 specifically
        if flash := board.mcu.flash:
            self.set_flash(flash)

    def set_arch(self, arch):
        # Despite what the below says, we have to deal with differing titles for some arches
        arch = table_munge(arch, _ARCH_MUNGES)
        # Arch is pretty easy to deal with, since it is always the same prompt, but the choice is unnamed
        self.kconfig.choice(prompt="Micro-controller Architecture").select(prompt=arch)

    def set_mcu(self, mcu):
        if proc_choice := self.kconfig.choice(prompt="Processor model"):
            for possible_mcu in proc_choice.choices():
                mcu_prompt = possible_mcu.nodes[0].prompt[0].split(" ")[0]
                if mcu_prompt == mcu:
                    logger.debug(
                        f"Selected {possible_mcu!r} for MCU specification {mcu}"
                    )
                    possible_mcu.set_value(2)
        else:
            # This arch doesn't support setting the MCU
            if not mcu:
                # We don't care
                return
            # If the kconfig sets an 'MCU', and it is already correct, don't mess with it
            if mcu_sym := self.kconfig.symbol(name="MCU", allow_invisible=True):
                if mcu_sym.get() == mcu:
                    logger.debug(f"Static MCU model {mcu} selected")
                    return
            raise RuntimeError(
                f"Could not set MCU type to {mcu}. Is it supported by this version of klipper?"
            )

    def set_freq(self, freq):
        freq_choice: Optional[KConfigChoice] = None
        for choice in self.kconfig.choices:
            if choice.prompt in ["Processor Speed", "Clock Reference"]:
                freq_choice = choice
                break
        if (not freq_choice) and freq:
            raise ValueError("Frequency cannot be set for this MCU")
        # Special case "INTERNAL"
        if freq == "INTERNAL":
            freq_choice.select(prompt="Internal clock")
            logger.debug(f"Internal clock selected {freq_choice}")
            return
        # Frequency should be in the form XXMhz or XXKhz
        if matches := FREQ_IN_RE.match(freq):
            rate, units = matches.groups()
        else:
            raise ValueError(f'Frequency "{freq}" not recognized')
        target_re = re.compile(
            f"^{re.escape(rate)} ?{re.escape(units)}", flags=re.IGNORECASE
        )
        for sym in freq_choice.choices():
            if target_re.match(sym.nodes[0].prompt[0]):
                logger.debug(f"Selected {sym!r} for clock specification {freq}")
                sym.set_value(2)
                return
        raise ValueError(f"Could not set frequency to {freq}")

    def set_flash(self, flash):
        if flash_choice := self.kconfig.choice(prompt="Flash chip"):
            for possible_flash in flash_choice.choices():
                if possible_flash.nodes[0].prompt[0].lower().startswith(flash.lower()):
                    logger.debug(
                        f"Selected {possible_flash!r} for flash specification {flash}"
                    )
                    possible_flash.set_value(2)
                    return
            raise ValueError(
                f"Could not select flash {flash}, is it supported by this version of klipper?"
            )
        raise RuntimeError(f"This MCU does not support setting the flash type")

    def get_interfaces(self):
        return self._board.interfaces

    def _configure_bootloader(self, board):
        status_led = getattr(board, "status_led", None) or getattr(board, "status", None)
        if isinstance(status_led, str):
            status_led = status_led.strip()
        else:
            status_led = ""
        # Only set the symbol if there's an actual value to preserve Kconfig defaults
        if status_led:
            self._set_string_symbol(("ENABLE_LED"), True)
            self._set_string_symbol(("STATUS_LED_PIN", "CONFIG_STATUS_LED_PIN"), status_led)

    def _configure_firmware(self, board):
        startup_pins = getattr(board, "startup_pins", None)
        if startup_pins is None:
            startup_pins = ""
        elif not isinstance(startup_pins, str):
            startup_pins = str(startup_pins)
        self._set_string_symbol(("INITIAL_PINS", "CONFIG_INITIAL_PINS"), startup_pins)

    def _set_string_symbol(self, names, value):
        for name in names:
            if symbol := self.kconfig.symbol(name=name, allow_invisible=True):
                symbol.set(value)
                return True
        logger.debug("No matching kconfig symbol found for %s", names)
        return False

    # TODO: get_interface - return the currently active interface
    # TODO: on init, determine which interface is active (from defaults) and set as active here.

    def _get_comms_choice(self):
        return self.kconfig.choice(
            prompt=(
                "Communication interface",
                "Communications interface",
                "Communication Interface",
                "Communications Interface",
            )
        )

    def supports_canbridge(self):
        # Can bridge requires both USB and CAN
        have_can = False
        have_usb = False
        for iface in self._board.interfaces:
            if iface.if_type == "CAN":
                have_can = True
            elif iface.if_type == "USB":
                have_usb = True
        if not (have_can and have_usb):
            return False
        for choice in self._get_comms_choice().choices():
            if choice.nodes[0].prompt[0].lower().startswith("usb to can bus bridge"):
                return True
        return False

    def set_canbridge(
        self,
        can_interface: BoardInterfaceDefinition,
        usb_interface: BoardInterfaceDefinition,
    ):
        raise NotImplementedError("Canbridge is not yet supported")

    def set_interface(self, interface: BoardInterfaceDefinition):
        if interface.if_type == "USB":
            if not interface.pins:
                self._get_comms_choice().select(prompt=("USB", "USBSERIAL"))
            else:
                self._get_comms_choice().select(
                    prompt=f"USB (on {interface.pins['dm']}/{interface.pins['dp']})"
                )
        elif interface.if_type == "CAN":
            if "CAN bus" in self._get_comms_choice().prompts():
                self._get_comms_choice().select(prompt="CAN bus")
                if (rx_sym := self.kconfig.symbol(prompt="CAN RX gpio number")) and (
                    tx_sym := self.kconfig.symbol(prompt="CAN TX gpio number")
                ):
                    rx_sym.set(interface.pins["rx"].removeprefix("gpio"))
                    tx_sym.set(interface.pins["tx"].removeprefix("gpio"))
                else:
                    raise RuntimeError(
                        "Generic can bus comms specified, but pin configurations could not be found"
                    )
            else:
                self._get_comms_choice().select(
                    prompt=f"CAN bus (on {interface.pins['rx']}/{interface.pins['tx']})"
                )
        elif interface.if_type == "UART":
            pin_spec = (
                f"({interface.pins['rx'].upper()}/{interface.pins['tx'].upper()}|"
                f"{interface.pins['tx'].upper()}/{interface.pins['rx'].upper()})"
            )
            target_re = re.compile(
                f"^(Serial \\(?on )?US?ART[0-9]* (on )?{pin_spec}\\)?"
            )
            for possible_comms in self._get_comms_choice().choices():
                if target_re.match(possible_comms.nodes[0].prompt[0]):
                    possible_comms.set_value(2)
                    return
            raise ValueError(f"Serial not found {interface!r}")
        else:
            raise ValueError(f"Interface type {interface.if_type} is not supported")

    def set_baud(self, baud):
        """
        Set the baud rate of whatever interface is currently configured.
        Errors if USB is selected.
        If canbridge is active, then sets the can downstream baudrate
        :param baud:
        :return:
        """
        raise NotImplementedError("Setting baud is not supported")

    def _header(self):
        return (
            "#\n"
            "#-# This file was generated by TBDNAMEHERE.\n"
            f"#-# This config is for: {self._board.manufacturer}/{self._board.model}/{self._board.variant}\n"
            f"#-# This config was generated at {datetime.now(None).isoformat()}\n"
            "#-# THIS IS WIP SOFTWARE, AND IT MAY EAT YOUR CAT. BE CAREFUL. TRUST (or not) BUT VERIFY\n"
            "#\n"
        )

    def write_bootloader_config(self, board, config_path: PathLike):
        self._configure_bootloader(board)
        return self.save_config(config_path)

    def write_firmware_config(self, board, config_path: PathLike):
        self._configure_firmware(board)
        return self.save_config(config_path)

    def save_config(self, config_path: PathLike) -> Path:
        config_path = Path(config_path).expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        self.kconfig.kcl.write_config(
            str(config_path), header=self._header(), save_old=False
        )
        return config_path
