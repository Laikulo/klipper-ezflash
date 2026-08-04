import logging
 
from .ui import main as ui_main
 
 
def main():
    logging.basicConfig(level=logging.INFO)
    # EZFlashApp.launch() already checks for a previous session and offers
    # to resume it before showing the main menu, so no pre-launch resume
    # logic is needed here.
    ui_main()
 
 
if __name__ == "__main__":
    main()
