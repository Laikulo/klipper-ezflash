import urwid
from .. import PROJECT_NAME, PROJECT_VERSION

PALETTE = (
    (None, "black", "light gray"),
    ("select", "light gray", "black")
)

class UI(object):

    def __init__(self):
        self._header_text = urwid.Text(f"{PROJECT_NAME} v{PROJECT_VERSION}")
        self._body = urwid.Filler(
            urwid.Button("Go!")
        )
        self._top_widget = urwid.Frame(header=self._header_text, body=self._body)
        self._loop = urwid.MainLoop(
            widget=self._top_widget,
            palette=PALETTE,
            unhandled_input=self._on_unhandled

        )

    def _on_unhandled(_, in_data) -> bool:
        return False

    def launch(self):
        self._loop.run()


