import re
import logging
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import Optional, Iterable
from enum import Enum

from .kconfig import KConfig, KConfigChoice
from .model import BoardDefinition, BoardInterfaceDefinition
from .util import table_munge

logger = logging.getLogger(__name__)
FREQ_IN_RE = re.compile("([0-9]+)([MK]hz)", flags=re.IGNORECASE)

_ARCH_MUNGES = (
    ("rp2040", "rp235x", "Raspberry Pi RP2040", "Raspberry Pi RP2040/RP235x"),
)


_COMMS_MUNGES = (
    "Communication interface",
    "Communications interface",
    "Communication Interface",
    "Communications Interface",
)


class ConfiguratorTarget(Enum):
    KLIPPER = 1
    KATAPULT = 2


class Configurator(object):
    def __init__(self, source_path: PathLike, board: BoardDefinition, target: Optional[ConfiguratorTarget] = None):
        self._target: Optional[ConfiguratorTarget] = target
        self.source_path = source_path
        self.kconfig = KConfig(source_path)
        self._board = board
        self._active_interface: Optional[BoardInterfaceDefinition] = None
        # Enable extra low-level options (required for many configs to work)
        if extra_config := self.kconfig.symbol(
            prompt="Enable extra low-level configuration options"
        ):
            extra_config.set(True)
        self._load_from_board(board)

    @classmethod
    def katapult(cls, source_path: PathLike, board: BoardDefinition) -> "Configurator":
        return cls(source_path, board, target=ConfiguratorTarget.KATAPULT)

    @classmethod
    def klipper(cls, source_path: PathLike, board: BoardDefinition) -> "Configurator":
        return cls(source_path, board, target=ConfiguratorTarget.KLIPPER)

    def _load_from_board(self, board):
        self.set_arch(board.mcu.arch)
        self.set_mcu(board.mcu.mcu)
        if clock := board.mcu.clock:
            self.set_freq(clock)
        # Don't set flash type when there's a bootloader (no "stage 2" firmware)
        if flash := board.mcu.flash:
            self.set_flash(flash)
        if self._target == ConfiguratorTarget.KATAPULT:
            if board.status is not None:
                self.set_status_leds(board.status)
        if self._target == ConfiguratorTarget.KLIPPER:
            if board.startup_pins is not None:
                self.set_startup_pins(board.startup_pins)
            if "rp2040" in board.mcu.arch.lower():
                self.set_bootloader_offset()

    def set_status_leds(self, gpio_spec: str):
        if self._target != ConfiguratorTarget.KATAPULT:
            raise ValueError("Status LEDs are only supported for Katapult")
        self.kconfig.symbol(prompt="Enable Status LED").set(True)
        self.kconfig.symbol(prompt="Status LED GPIO Pin").set(gpio_spec)

    def set_startup_pins(self, gpio_spec: str):
        if self._target != ConfiguratorTarget.KLIPPER:
            raise ValueError("Startup pins are only supported for Klipper")
        self.kconfig.symbol(prompt="GPIO pins to set at micro-controller startup").set(gpio_spec)

    def set_arch(self, arch):
        # Despite what the below says, we have to deal with differing titles for some arches
        arch = table_munge(arch, _ARCH_MUNGES)
        # Arch is pretty easy to deal with, since it is always the same prompt, but the choice is unnamed
        self.kconfig.choice(prompt="Micro-controller Architecture").select(prompt=arch)

    def set_mcu(self, mcu):
        if proc_choice := self.kconfig.choice(prompt="Processor model"):
            for possible_mcu in proc_choice.choices():
                mcu_prompt = possible_mcu.nodes[0].prompt[0].split(" ")[0]
                if mcu_prompt.upper() == mcu.upper():
                    logger.debug(f"Selected {possible_mcu!r} for MCU specification {mcu}")
                    possible_mcu.set_value(2)
                    return
            raise RuntimeError(f"Unable to find matching MCU {mcu}")
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
        freq_choice = None
        for choice in self.kconfig.choices:
            if choice.prompt in ["Processor Speed", "Clock Reference"]:
                freq_choice = choice
                break
        if not freq_choice and freq:
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
                    logger.debug(f"Selected {possible_flash!r} for flash specification {flash}")
                    possible_flash.set_value(2)
                    return
            raise ValueError(
                f"Could not select flash {flash}, is it supported by this version of klipper?"
            )
        raise RuntimeError("This MCU does not support setting the flash type")

    def set_bootloader_offset(self):
        """Select the flash offset reserved for Katapult on RP2040, so Klipper's
        firmware links after the bootloader rather than at offset 0.
        """
        offset_choice = self.kconfig.choice(
            prompt=("Bootloader offset", "Application start offset")
        )
        if not offset_choice:
            return
        for opt in offset_choice.choices():
            if "no bootloader" not in opt.nodes[0].prompt[0].lower():
                opt.set_value(2)
                return
        raise ValueError("Could not find a bootloader offset option to select")

    def get_interfaces(self):
        return self._board.interfaces

    # TODO: add get_interface() to return the currently active interface

    def _get_comms_choice(self):
        return self.kconfig.choice(prompt=_COMMS_MUNGES)

    def supports_canbridge(self):
        have_can = any(iface.if_type == "CAN" for iface in self._board.interfaces)
        have_usb = any(iface.if_type == "USB" for iface in self._board.interfaces)
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

    def _set_can_pins(self, interface: BoardInterfaceDefinition):
        """Set CAN RX/TX GPIO pins for the given interface."""
        if (rx_sym := self.kconfig.symbol(prompt="CAN RX gpio number")) and (
            tx_sym := self.kconfig.symbol(prompt="CAN TX gpio number")
        ):
            rx_sym.set(interface.pins["rx"].removeprefix("gpio"))
            tx_sym.set(interface.pins["tx"].removeprefix("gpio"))
        else:
            raise RuntimeError(
                f"{interface.if_type} comms specified, but pin configurations could not be found"
            )

    def _select_can_bus(self, interface: BoardInterfaceDefinition):
        """Select CAN bus from the communications choice and set pins if needed."""
        prompts = self._get_comms_choice().prompts()
        if "CAN bus" in prompts:
            self._get_comms_choice().select(prompt="CAN bus")
            self._set_can_pins(interface)
        else:
            self._get_comms_choice().select(
                prompt=f"CAN bus (on {interface.pins['rx']}/{interface.pins['tx']})"
            )

    def set_interface(self, interface: BoardInterfaceDefinition):
        if_type = interface.if_type
        comms = self._get_comms_choice()

        if if_type == "USB":
            if not interface.pins:
                comms.select(prompt=("USB", "USBSERIAL"))
            else:
                comms.select(
                    prompt=f"USB (on {interface.pins['dm']}/{interface.pins['dp']})"
                )
        elif if_type in ("CAN", "CAN Bridge"):
            self._select_can_bus(interface)
        elif if_type == "UART":
            pin_spec = (
                f"({interface.pins['rx'].upper()}/{interface.pins['tx'].upper()}|"
                f"{interface.pins['tx'].upper()}/{interface.pins['rx'].upper()})"
            )
            target_re = re.compile(
                f"^(Serial \\(?on )?US?ART[0-9]* (on )?{pin_spec}\\)?"
            )
            for possible_comms in comms.choices():
                if target_re.match(possible_comms.nodes[0].prompt[0]):
                    possible_comms.set_value(2)
                    return
            raise ValueError(f"Serial not found {interface!r}")
        else:
            raise ValueError(f"Interface type {if_type} is not supported")
        self._active_interface = interface

    def set_baud(self, baud):
        """Set the baud rate of the currently configured interface.
        Raises if USB is selected; sets CAN downstream baudrate for CAN Bridge.
        """
        iface = self._active_interface
        prompt = {"UART": "Baud rate for serial port", "CAN": "CAN bus speed"}.get(
            iface.if_type
        )
        if not prompt:
            raise ValueError("Attempted to set baud on a non-CAN or UART interface")
        self.kconfig.symbol(prompt=prompt).set(baud)

    def _header(self):
        return (
            "#\n"
            "#-# This file was generated by Klipper-EZFlash.\n"
            f"#-# This config is for: {self._board.manufacturer}/{self._board.variant}/{self._active_interface}\n"
            f"#-# This config was generated at {datetime.now(None).isoformat()}\n"
            "#-# THIS IS WIP SOFTWARE, AND IT MAY EAT YOUR CAT. BE CAREFUL. TRUST (or not) BUT VERIFY\n"
            "#\n"
        )

    def save_config(self, config_path: PathLike) -> Path:
        config_path = Path(config_path).expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        self.kconfig.kcl.write_config(
            str(config_path), header=self._header(), save_old=False
        )
        return config_path
