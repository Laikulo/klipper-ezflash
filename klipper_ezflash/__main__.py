import logging
from pathlib import Path
from pprint import pprint as pp

from klipper_ezflash.configurator import Configurator
from klipper_ezflash.model import BoardDatabase
from .util import find_klipper
from .ui import UI
from .session import load_session
from dialog import Dialog

logger = logging.getLogger("klipper-mcu-configs")


def main():
    logging.basicConfig(level=logging.INFO)

    ui = UI()
    # If a previous session exists, offer to resume before showing main UI
    try:
        existing = load_session()
    except Exception:
        existing = None

    if existing is not None:
        try:
            # Use the UI's dialog to ask the user whether to resume
            code, tag = ui._dialog.menu(
                "A previous flash session was detected.\n\nResume or start a new flash?",
                choices=[("resume", "Resume previous flash"), ("new", "Start a new flash")],
                cancel_label="Start new",
            )
            if code == Dialog.OK and tag == "resume":
                try:
                    ui.start_flash_flow(resume_session=existing)
                    return
                except Exception:
                    # Fall back to normal launch on failure to resume
                    pass
        except Exception:
            # dialog/menu not available; continue to launch
            pass

    ui.launch()


if __name__ == "__main__":
    main()
