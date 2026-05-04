from .. import PROJECT_NAME, PROJECT_VERSION

from vindauga.widgets.application import Application
from vindauga.menus.menu_bar import MenuBar
from vindauga.menus.menu_bar import MenuItem
from vindauga.constants.keys import kbNoKey

class KEZFApplication(Application):
    def initMenuBar(self, bounds: Rect) -> MenuBar:
        bounds.bottomRight.y = bounds.topLeft.y + 1
        menuBar = MenuBar(bounds, MenuItem('Exit', 0, kbNoKey))
        return menuBar



class UI:
    def __init__(self):
        self._app=KEZFApplication()


    def launch(self):
        self._app.run()
