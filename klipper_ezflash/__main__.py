import logging

from textual.logging import TextualHandler

from .ui import main as ui_main


def main():
    # TextualHandler routes log records to Textual's devtools console
    # (`textual console`, connected separately) instead of writing them
    # straight to stdout. Without this, any logger.info()/warning() call
    # anywhere in the codebase — e.g. model.py's board-loading messages —
    # prints directly onto the raw terminal underneath Textual's
    # rendering, corrupting the screen. force=True ensures this wins even
    # if some other module already called logging.basicConfig() first
    # (basicConfig is a no-op on subsequent calls unless forced).
    logging.basicConfig(
        level=logging.INFO,
        handlers=[TextualHandler()],
        force=True,
    )
    # EZFlashApp.launch() already checks for a previous session and offers
    # to resume it before showing the main menu, so no pre-launch resume
    # logic is needed here.
    ui_main()


if __name__ == "__main__":
    main()