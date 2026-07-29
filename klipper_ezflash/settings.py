import json
import logging
import os
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

DEFAULT_CAN_SPEED = 1000000
DEFAULT_UART_BAUDRATE = 250000
DEFAULT_CAN_INTERFACE = "can0"


def _get_settings_path() -> Path:
    """Return the path to interface_settings.json in XDG config directory."""
    xdg_config_home = os.environ.get(
        "XDG_CONFIG_HOME", str(Path.home() / ".config")
    )
    settings_path = Path(xdg_config_home) / "klipper-ezflash" / "interface_settings.json"
    return settings_path


class InterfaceSettings:
    """Manages interface communication settings stored in XDG config."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _get_settings_path()
        # Ensure parent directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing settings or create defaults
        self.can_speed = DEFAULT_CAN_SPEED
        self.uart_baudrate = DEFAULT_UART_BAUDRATE
        self.can_interface = DEFAULT_CAN_INTERFACE
        
        if self._path.exists():
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                if "can_speed" in data:
                    self.can_speed = int(data["can_speed"])
                if "uart_baudrate" in data:
                    self.uart_baudrate = int(data["uart_baudrate"])
                if "can_interface" in data:
                    self.can_interface = str(data["can_interface"])
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to load settings from {self._path}: {e}")
        else:
            # Create the file with default values if it doesn't exist
            self.save()
            logger.info(f"Created new settings file at {self._path} with defaults")

    @property
    def can_speed(self) -> int:
        return self._can_speed

    @can_speed.setter
    def can_speed(self, value: int):
        if value < 0:
            raise ValueError("CAN speed must be non-negative")
        self._can_speed = value

    @property
    def uart_baudrate(self) -> int:
        return self._uart_baudrate

    @uart_baudrate.setter
    def uart_baudrate(self, value: int):
        if value < 0:
            raise ValueError("UART baud rate must be non-negative")
        self._uart_baudrate = value

    def save(self) -> None:
        """Save current settings to disk."""
        data = {
            "can_speed": self.can_speed,
            "uart_baudrate": self.uart_baudrate,
            "can_interface": self.can_interface,
        }
        with open(self._path, "w") as f:
            json.dump(data, f, indent=4)

    def get_dict(self) -> Dict[str, int]:
        """Return settings as a dictionary."""
        return {
            "can_speed": self.can_speed,
            "uart_baudrate": self.uart_baudrate,
            "can_interface": self.can_interface,
        }

    @classmethod
    def from_path(cls, path: Path):
        """Create an InterfaceSettings instance from a specific path without auto-creating."""
        settings = cls.__new__(cls)
        settings._path = path
        settings.can_speed = DEFAULT_CAN_SPEED
        settings.uart_baudrate = DEFAULT_UART_BAUDRATE
        
        if path.exists():
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                if "can_speed" in data:
                    settings.can_speed = int(data["can_speed"])
                if "uart_baudrate" in data:
                    settings.uart_baudrate = int(data["uart_baudrate"])
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Failed to load settings from {path}: {e}")
        
        return settings
