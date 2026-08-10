"""
Textual port of the klipper-ezflash TUI.

Design notes
------------
- Every former `dialog` call (msgbox/yesno/menu/inputbox) has an equivalent
  ModalScreen below, and a thin `self.msgbox(...)` / `self.yesno(...)` /
  `self.menu(...)` / `self.inputbox(...)` helper on the App that awaits it.
  Because Textual supports `App.push_screen_wait()`, the overall control flow
  (main menu loop, board-selection wizard, settings menu, flash flow) is
  written as a single linear async coroutine per screen/flow, almost line
  for line the same shape as the original dialog version — just with
  `await` in front of each interaction instead of unpacking `(code, tag)`.

- The flash flow gets its own persistent Screen (FlashFlowScreen) with a
  two-pane layout: a step tracker on the left (pending/active/done/error),
  and a live scrolling log on the right showing command output. This
  replaces `_run_with_progressbox` + the `multiprocessing`(fork) trick
  entirely.

- Command streaming uses `asyncio.create_subprocess_exec` and
  `async for line in proc.stdout`. This runs on Textual's own asyncio event
  loop — there is no `threading` import and no manual fork/pipe plumbing.
  `flash.py` now exposes pure argv builders (`build_rp2040_flash_cmd`,
  `build_stm32_flash_cmd`, `build_flashtool_cmd`, plus the `MAKE_CLEAN_ARGS`
  / `MAKE_BUILD_ARGS` constants and `katapult_output_path` /
  `klipper_output_path` helpers) alongside its original blocking functions,
  so every step of the flash flow — compiles and flashes alike — streams
  through the right-hand log via `screen.run_argv` /
  `screen.run_argv_check_patterns`, with no threads and no subprocess
  logic duplicated between flash.py and the UI.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import io
import shlex
import tempfile
import traceback
from enum import Enum
from pathlib import Path
from typing import Optional, Sequence

from textual.app import App, ComposeResult
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, LoadingIndicator, RichLog, Static
from rich.text import Text

from ..model import BoardDatabase
from ..settings import InterfaceSettings
from ..util import (
    find_katapult,
    _KATAPULT_CLONE_URL,
    find_klipper,
    file_sha256,
    check_can_host_state,
    fix_can_host_state,
)  # NOTE: confirm this matches your util.py's actual function name
from ..session import load_session, save_session, clear_session, SessionData
from .. import device as device_mod
from .. import flash as flash_mod
from .. import detect as detect_mod
from ..fault import EZFException, AmbiguousCanUuidException


# --------------------------------------------------------------------------
# Modal dialogs — equivalents of dialog.msgbox / yesno / menu / inputbox
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Theme palette (dark gray + green). Hardcoded literals rather than
# redefining Textual's built-in $accent/$panel/etc. tokens — safer across
# Textual versions than depending on exactly how the active theme's design
# tokens are meant to be overridden.
# --------------------------------------------------------------------------
_BG = "#121212"        # app/screen background
_PANEL = "#1c1c1c"      # dialog box / panel background
_SURFACE = "#1a1a1a"    # nested surface (log, list) background
_ACCENT = "#3ddc84"     # green accent (borders, titles, primary buttons)
_ACCENT_HOVER = "#2fc774"
_ACCENT_TEXT = "#08130c"  # dark text on top of the green accent
_TEXT = "#e6e6e6"       # primary text
_TEXT_DIM = "#8a9199"   # secondary/pending text
_BTN_BG = "#2a2a2a"
_BTN_HOVER = "#353535"
_ERROR_BG = "#5c1f1f"
_ERROR_TEXT = "#f4b4b4"

DIALOG_CSS = f"""
ModalScreen {{
    align: center middle;
    /* Explicit opaque background — Textual's default ModalScreen
       background is a translucent scrim over whatever screen is
       mounted below it, which is how a previous screen's leftover
       content (e.g. amber step-list text from an earlier flash) could
       show through around the edges of a dialog that doesn't fully
       cover the terminal. Making this fully opaque means nothing from
       underneath can bleed through, regardless of dialog width. */
    background: {_BG};
}}
#dialog-box {{
    /* Percentage width (clamped) instead of width:auto sized to content —
       this is what actually fills the terminal instead of leaving a big
       gap on one side; align:center middle above keeps it centered. */
    width: 80%;
    min-width: 50;
    max-width: 120;
    height: auto;
    max-height: 90%;
    border: solid {_ACCENT};
    background: {_PANEL};
    color: {_TEXT};
    padding: 1 2;
}}
#dialog-title {{
    text-style: bold;
    color: {_ACCENT};
    margin-bottom: 1;
}}
#dialog-body {{
    margin-bottom: 1;
}}
#dialog-buttons {{
    height: auto;
    align: right middle;
}}
#dialog-buttons Button {{
    margin-left: 1;
}}
ListView {{
    height: auto;
    max-height: 20;
    background: {_SURFACE};
}}
ListItem {{
    background: {_SURFACE};
}}
/* The selected/highlighted row in a ListView — Textual applies a
   `--highlight` class to it. Without this rule the only cue was the
   row's text going bold, which is easy to miss; this gives it the
   same kind of solid highlight bar the old `dialog` UI had. */
ListView > ListItem.--highlight {{
    background: {_ACCENT};
    color: {_ACCENT_TEXT};
    text-style: bold;
}}
Button {{
    background: {_BTN_BG};
    color: {_TEXT};
}}
Button:hover {{
    background: {_BTN_HOVER};
}}
Button.-primary {{
    background: {_ACCENT};
    color: {_ACCENT_TEXT};
    text-style: bold;
}}
Button.-primary:hover {{
    background: {_ACCENT_HOVER};
}}
Button.-error {{
    background: {_ERROR_BG};
    color: {_ERROR_TEXT};
}}
"""


def _mnemonic_label(label: str) -> Text:
    """Return `label` as Rich Text with its first alphanumeric character
    underlined — a visual cue for the hotkey/type-ahead character,
    mirroring the classic `dialog` TUI's underlined access-key
    convention. Used for both ListView choice rows and Button labels."""
    text = Text(label)
    for i, ch in enumerate(label):
        if ch.isalnum():
            text.stylize("underline", i, i + 1)
            break
    return text


class _DialogActions:
    """Shared action implementations for dialog screens.

    Deliberately NOT a class that carries its own `BINDINGS` for other
    classes to inherit — every dialog screen below declares its own
    `BINDINGS` list directly (see the note on each class). This mixin only
    supplies the method bodies those bindings call, which is ordinary
    Python method resolution and unaffected by how Textual's binding
    collection walks a class's MRO.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Belt-and-suspenders alongside the `ModalScreen { align: center
        # middle; }` CSS rule: setting it imperatively too guarantees the
        # dialog is actually centered regardless of any ambiguity in how
        # a *scoped* stylesheet's type-selector (`ModalScreen`, the base
        # class) resolves against each subclass's own runtime type.
        self.styles.align_horizontal = "center"
        self.styles.align_vertical = "middle"

    def action_focus_previous(self) -> None:
        self.focus_previous()

    def action_focus_next(self) -> None:
        self.focus_next()

    def action_app_quit(self) -> None:
        # Calling this directly on the screen (rather than relying on the
        # key bubbling up to the App's own `q` binding) means quitting
        # works the same way regardless of whatever screen/modal is on
        # top — no dependency on bubbling semantics we can't fully see
        # from here.
        self.app.exit()

    def _activate_button_by_letter(self, char: str) -> bool:
        """Look for a Button whose label's first alphanumeric character
        matches `char` (case-insensitive); if found, press it directly —
        this is the "hotkey" half of the mnemonic-letter feature (see
        `_mnemonic_label`), so pressing e.g. "Y" activates a "Yes" button
        immediately rather than only moving focus to it."""
        char = char.lower()
        for button in self.query(Button):
            label = button.label
            label_text = getattr(label, "plain", None) or str(label)
            for ch in label_text:
                if ch.isalnum():
                    if ch.lower() == char:
                        button.press()
                        return True
                    break
        return False


# Shared binding set for arrow-key focus navigation and quit keys. Each
# dialog class below assigns its own `BINDINGS = list(_NAV_BINDINGS) +
# [...]` — copied in directly rather than inherited via a separate mixin,
# since that's what actually guaranteed Textual picks them up (whereas
# pulling BINDINGS in only through a bare mixin's inheritance was
# unreliable).
#
# NOTE: vim-style h/j/k/l navigation was dropped here — it collided with
# the ChoiceDialog type-ahead feature below (pressing "h" to jump to a
# choice starting with "h" vs. pressing "h" to move focus left are
# mutually exclusive). Plain arrow keys still work everywhere.
_NAV_BINDINGS = [
    Binding("left,up", "focus_previous", "", show=False),
    Binding("right,down", "focus_next", "", show=False),
    Binding("q", "app_quit", "Quit", show=True),
    Binding("ctrl+c", "app_quit", "Force Quit", show=False),
]


class MessageDialog(_DialogActions, ModalScreen[None]):
    """Equivalent of dialog.msgbox / infobox."""

    CSS = DIALOG_CSS
    BINDINGS = list(_NAV_BINDINGS) + [
        Binding("escape,enter", "dismiss_dialog", "OK", show=False),
    ]

    def __init__(self, message: str, title: str = "") -> None:
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            if self._title:
                yield Label(self._title, id="dialog-title")
            yield Static(self._message, id="dialog-body")
            with Horizontal(id="dialog-buttons"):
                yield Button(_mnemonic_label("OK"), id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#ok", Button).focus()

    def on_key(self, event: events.Key) -> None:
        char = event.character
        if char and len(char) == 1 and char.isalnum() and self._activate_button_by_letter(char):
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_dialog(self) -> None:
        self.dismiss(None)


class ConfirmDialog(_DialogActions, ModalScreen[bool]):
    """Equivalent of dialog.yesno."""

    CSS = DIALOG_CSS
    BINDINGS = list(_NAV_BINDINGS)

    def __init__(
        self,
        message: str,
        title: str = "",
        yes_label: str = "Yes",
        no_label: str = "No",
        default_no: bool = False,
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._yes_label = yes_label
        self._no_label = no_label
        self._default_no = default_no

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            if self._title:
                yield Label(self._title, id="dialog-title")
            yield Static(self._message, id="dialog-body")
            with Horizontal(id="dialog-buttons"):
                yield Button(
                    _mnemonic_label(self._no_label),
                    id="no",
                    variant="error" if self._default_no else "default",
                )
                yield Button(
                    _mnemonic_label(self._yes_label),
                    id="yes",
                    variant="default" if self._default_no else "primary",
                )

    def on_mount(self) -> None:
        self.query_one("#no" if self._default_no else "#yes", Button).focus()

    def on_key(self, event: events.Key) -> None:
        char = event.character
        if char and len(char) == 1 and char.isalnum() and self._activate_button_by_letter(char):
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ChoiceDialog(_DialogActions, ModalScreen[Optional[str]]):
    """Equivalent of dialog.menu. `choices` is a list of (tag, label)."""

    CSS = DIALOG_CSS
    BINDINGS = list(_NAV_BINDINGS) + [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, choices: Sequence[tuple[str, str]], message: str = "", title: str = "", cancel_label: str = "Cancel") -> None:
        super().__init__()
        self._choices = list(choices)
        self._message = message
        self._title = title
        self._cancel_label = cancel_label
        # Type-ahead state: pressing a letter jumps to the next choice
        # whose label starts with it; pressing the same letter again
        # cycles to the next match after that, mirroring the classic
        # `dialog` menu's first-letter navigation.
        self._typeahead_char: Optional[str] = None
        self._typeahead_index: int = -1

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            if self._title:
                yield Label(self._title, id="dialog-title")
            if self._message:
                yield Static(self._message, id="dialog-body")
            yield ListView(
                *[ListItem(Label(_mnemonic_label(label)), name=tag) for tag, label in self._choices]
            )
            with Horizontal(id="dialog-buttons"):
                yield Button(_mnemonic_label(self._cancel_label), id="cancel")

    def on_key(self, event: events.Key) -> None:
        char = event.character
        if not char or len(char) != 1 or not char.isalnum():
            return
        if self._jump_to_choice(char):
            event.stop()
            return
        if self._activate_button_by_letter(char):
            event.stop()

    def _jump_to_choice(self, char: str) -> bool:
        char = char.lower()
        labels = [label for _, label in self._choices]
        count = len(labels)
        if count == 0:
            return False
        start = 0
        if char == self._typeahead_char:
            start = (self._typeahead_index + 1) % count
        for offset in range(count):
            idx = (start + offset) % count
            if labels[idx].strip().lower().startswith(char):
                list_view = self.query_one(ListView)
                list_view.index = idx
                list_view.focus()
                self._typeahead_char = char
                self._typeahead_index = idx
                return True
        return False

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.name)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ButtonChoiceDialog(_DialogActions, ModalScreen[Optional[str]]):
    """Like ChoiceDialog, but each choice renders as its own Button
    instead of a ListView row — laid out side by side in the same
    `#dialog-buttons` row style used everywhere else (ConfirmDialog,
    InputDialog, etc.), rather than stacked full-width. Better fit for a
    small, fixed set of equally-weighted choices (e.g. "Begin Flash" /
    "Reselect") where a scrollable list is overkill. The cancel button is
    optional — pass `cancel_label=None` (the default) to omit it
    entirely; Escape still dismisses with None either way, so backing out
    doesn't require a dedicated button."""

    CSS = DIALOG_CSS
    BINDINGS = list(_NAV_BINDINGS) + [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[tuple[str, str]],
        message: str = "",
        title: str = "",
        cancel_label: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._choices = list(choices)
        self._message = message
        self._title = title
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            if self._title:
                yield Label(self._title, id="dialog-title")
            if self._message:
                yield Static(self._message, id="dialog-body")
            with Horizontal(id="dialog-buttons"):
                for i, (tag, label) in enumerate(self._choices):
                    yield Button(_mnemonic_label(label), id=f"choice-{i}", variant="primary" if i == 0 else "default")
                if self._cancel_label:
                    yield Button(_mnemonic_label(self._cancel_label), id="cancel")

    def on_mount(self) -> None:
        first = self.query("#dialog-buttons Button")
        if first:
            first.first().focus()

    def on_key(self, event: events.Key) -> None:
        char = event.character
        if char and len(char) == 1 and char.isalnum() and self._activate_button_by_letter(char):
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        index = int(event.button.id.removeprefix("choice-"))
        self.dismiss(self._choices[index][0])

    def action_cancel(self) -> None:
        self.dismiss(None)


class InputDialog(_DialogActions, ModalScreen[Optional[str]]):
    """Equivalent of dialog.inputbox."""

    CSS = DIALOG_CSS
    BINDINGS = list(_NAV_BINDINGS) + [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, prompt: str, initial: str = "", title: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            if self._title:
                yield Label(self._title, id="dialog-title")
            yield Static(self._prompt, id="dialog-body")
            yield Input(value=self._initial, id="input-field")
            with Horizontal(id="dialog-buttons"):
                yield Button(_mnemonic_label("Cancel"), id="cancel")
                yield Button(_mnemonic_label("OK"), id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_key(self, event: events.Key) -> None:
        # Unlike the other dialogs, this one has a text field — letter
        # keys must type into it, not trigger Cancel/OK by mnemonic.
        if self.query_one(Input).has_focus:
            return
        char = event.character
        if char and len(char) == 1 and char.isalnum() and self._activate_button_by_letter(char):
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one(Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


@contextlib.contextmanager
def _capture_output_to_log(screen: "FlashFlowScreen"):
    """Redirect stdout/stderr into the flash screen's log for the
    duration of the block.

    Some synchronous library code we call into — notably kconfiglib, via
    Configurator/KConfig — prints warnings directly to stdout/stderr
    during parsing rather than raising or logging through the `logging`
    module. Since that's a direct write to the real terminal, it bypasses
    Textual's rendering entirely and corrupts the screen. This can't be
    streamed live (the call is synchronous, not a subprocess we're
    reading from), but capturing and flushing it into the log after the
    fact at least keeps it inside the app instead of on the raw
    terminal, and it's still visible to the user.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            yield
    finally:
        captured = buf.getvalue()
        if captured.strip():
            for line in captured.splitlines():
                screen.write(line)


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


# --------------------------------------------------------------------------
# Flash flow screen — two-pane layout: steps on the left, live output right
# --------------------------------------------------------------------------

class StepStatus(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    ERROR = "error"


_STATUS_GLYPH = {
    StepStatus.PENDING: ("○", f"{_TEXT_DIM}"),
    StepStatus.ACTIVE: ("▶", "bold #f5c518"),
    StepStatus.DONE: ("✔", f"bold {_ACCENT}"),
    StepStatus.ERROR: ("✘", "bold #ef5350"),
}


class StepList(Static):
    """Left-hand pane: ordered list of flash steps with live status."""

    def __init__(self, steps: list[str]) -> None:
        super().__init__()
        self._steps = steps
        self._status = [StepStatus.PENDING] * len(steps)

    def set_status(self, index: int, status: StepStatus) -> None:
        self._status[index] = status
        self.refresh()

    def status(self, index: int) -> StepStatus:
        return self._status[index]

    def __len__(self) -> int:
        return len(self._steps)

    def render(self) -> str:
        lines = []
        for i, name in enumerate(self._steps):
            glyph, style = _STATUS_GLYPH[self._status[i]]
            lines.append(f"[{style}]{glyph}  {name}[/]")
        return "\n".join(lines)


class FlashFlowScreen(Screen):
    """Persistent screen for the whole flash flow: steps left, output right."""

    BINDINGS = [
        Binding("q", "request_quit", "Quit", show=True),
        Binding("ctrl+c", "request_quit", "Force Quit", show=False),
    ]

    CSS = f"""
    FlashFlowScreen {{
        background: {_BG};
    }}
    FlashFlowScreen #body {{
        height: 1fr;
    }}
    FlashFlowScreen #steps {{
        /* percentage width so this pane scales with the terminal instead of
           staying a fixed cell count; clamped so it stays usable on very
           narrow or very wide terminals. */
        width: 30%;
        min-width: 24;
        max-width: 56;
        height: 100%;
        border-right: solid {_ACCENT};
        background: {_PANEL};
        color: {_TEXT};
        padding: 2 3;
    }}
    FlashFlowScreen #output {{
        /* 1fr = "take whatever width is left after #steps" — this is what
           actually makes the right pane grow/shrink with the terminal. */
        width: 1fr;
        height: 100%;
    }}
    FlashFlowScreen RichLog {{
        /* Bounding the height (instead of leaving it "auto", which would
           just grow to fit all content forever) is what makes this an
           actual scrollable region — RichLog's own auto_scroll then keeps
           new lines pinned in view rather than there being nothing to
           scroll within. */
        height: 2fr;
        background: {_SURFACE};
        color: {_TEXT};
    }}
    """

    def __init__(self, step_names: list[str]) -> None:
        super().__init__()
        self.step_list = StepList(step_names)
        # NOTE: can't call this `self.log` — Textual's Widget/DOMNode base
        # class already defines a read-only `log` property (the per-widget
        # logger, e.g. `self.log.info(...)`), so assigning to it raises
        # "property 'log' has no setter".
        self.output_log = RichLog(
            highlight=False, markup=False, wrap=True, max_lines=5000, auto_scroll=True
        )
        # Every line passed to write() is mirrored to this file immediately,
        # regardless of whether the on-screen log gets cleared between
        # steps — this is what makes the full transcript recoverable after
        # the fact (success, error, or abort) even though the visible
        # RichLog only ever shows the current step.
        self.log_path = Path(tempfile.gettempdir()) / f"ezflash-log-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
        try:
            self._log_file = self.log_path.open("a", encoding="utf-8")
        except OSError:
            self._log_file = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with VerticalScroll(id="steps"):
                yield self.step_list
            with Vertical(id="output"):
                yield self.output_log
        yield Footer()

    def action_request_quit(self) -> None:
        # No confirmation here on purpose: push_screen_wait requires an
        # active Textual worker (key-binding actions run directly on the
        # message pump, not inside one — that mismatch is exactly what
        # raised NoActiveWorker and crashed this on a real attempt to
        # abort mid-compile). The flash/compile steps are fast enough
        # that a confirmation isn't worth the risk of crashing the exit
        # path itself — just quit immediately.
        self.app.exit()

    def on_unmount(self) -> None:
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass

    def set_step(self, index: int, status: StepStatus) -> None:
        self.step_list.set_status(index, status)

    def start_step(self, index: int, header: Optional[str] = None) -> None:
        """Begin a new top-level flash-flow step: mark it ACTIVE, clear the
        on-screen log, and write a banner into it.

        The on-screen RichLog only shows the current step — cleared here on
        every transition. Nothing is actually lost: write() mirrors every
        line to self.log_path as it's produced, so the complete transcript
        for the whole flow is always recoverable from disk regardless of
        whether the flow finishes, errors, or is aborted.
        """
        self.set_step(index, StepStatus.ACTIVE)
        self.output_log.clear()
        if 0 <= index < len(self.step_list._steps):
            step_name = self.step_list._steps[index]
        else:
            step_name = f"Step {index}"
        self.write(f"\n{'─' * 8} {step_name} {'─' * 8}")
        if header:
            self.write(header)

    def write(self, line: str) -> None:
        self.output_log.write(line)
        # Belt-and-suspenders: auto_scroll=True already does this, but if
        # the user has manually scrolled up (which pauses auto-scroll)
        # we still want each new line during an active flash to pull the
        # view back down rather than silently piling up out of sight.
        self.output_log.scroll_end(animate=False)
        if self._log_file is not None:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                pass

    async def _stream_subprocess(
        self, argv: list[str], cwd: Optional[Path] = None, capture: Optional[list[str]] = None
    ) -> int:
        """Run argv, streaming stdout+stderr into the log.

        Reads raw chunks (not line-by-line) and splits on both `\\r` and
        `\\n` ourselves. This matters for tools like dfu-util and make
        that redraw an in-place progress line via bare `\\r` with no
        real newline in between ticks — `async for line in proc.stdout`
        only splits on `\\n`, so an entire burst of `\\r`-separated
        percentage updates arrives as one lump the moment a real newline
        finally shows up, which is what rendered as everything jammed
        onto one visual line. Here, intermediate `\\r`-terminated
        updates are held and only the final one before the next real
        line (or end of stream) is actually written — collapsing a
        rapid progress burst into one tidy line instead of dumping every
        tick.

        If `capture` is given, every line actually written is also
        appended to it (e.g. for success-pattern checks that need the
        full text afterward).
        """
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        buf = ""
        pending_progress: Optional[str] = None

        def flush_progress() -> None:
            nonlocal pending_progress
            if pending_progress is not None:
                self.write(pending_progress)
                if capture is not None:
                    capture.append(pending_progress)
                pending_progress = None

        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk.decode(errors="replace")
            while True:
                cut_n = buf.find("\n")
                cut_r = buf.find("\r")
                if cut_n == -1 and cut_r == -1:
                    break
                if cut_r != -1 and (cut_n == -1 or cut_r < cut_n):
                    segment, buf = buf[:cut_r], buf[cut_r + 1:]
                    if segment:
                        pending_progress = segment
                else:
                    segment, buf = buf[:cut_n], buf[cut_n + 1:]
                    if segment.endswith("\r"):
                        segment = segment[:-1]
                    flush_progress()
                    if segment:
                        self.write(segment)
                        if capture is not None:
                            capture.append(segment)
        flush_progress()
        if buf:
            self.write(buf)
            if capture is not None:
                capture.append(buf)
        return await proc.wait()

    async def run_command(self, *args: str, cwd: Optional[Path] = None) -> int:
        """Run a subprocess, streaming stdout/stderr into the log.

        Uses asyncio's native subprocess support, so output is pumped on
        the existing event loop as it's produced — no `threading` import,
        no polling loop, no fork.
        """
        self.write(f"$ {' '.join(shlex.quote(a) for a in args)}")
        return await self._stream_subprocess(list(args), cwd=cwd)

    async def run_make(self, title: str, cwd: Path, *make_args: str) -> None:
        """Run `make` in cwd, raising EZFException on non-zero exit."""
        self.write(f"\n[ {title} ]")
        rc = await self.run_command("make", *make_args, cwd=cwd)
        if rc != 0:
            raise EZFException(f"'make {' '.join(make_args)}' failed (exit {rc}) in {cwd}")

    async def run_argv(self, title: str, argv: list[str], cwd: Optional[Path] = None) -> None:
        """Run an arbitrary argv (e.g. from flash.py's command builders),
        raising EZFException on non-zero exit."""
        self.write(f"\n[ {title} ]")
        rc = await self.run_command(*argv, cwd=cwd)
        if rc != 0:
            raise EZFException(f"'{argv[0]}' failed (exit {rc})")

    async def run_argv_check_patterns(self, title: str, argv: list[str], success_patterns: list[str], cwd: Optional[Path] = None) -> None:
        """Run an argv where a non-zero exit can still mean success (e.g.
        dfu-util), by checking buffered output against `success_patterns`.
        Mirrors flash.py's `_run_with_success_check`, but streaming into the
        log as it runs rather than only at the end.
        """
        self.write(f"\n[ {title} ]")
        self.write(f"$ {' '.join(shlex.quote(a) for a in argv)}")
        output_lines: list[str] = []
        rc = await self._stream_subprocess(argv, cwd=cwd, capture=output_lines)
        if rc == 0:
            return
        output = "\n".join(output_lines)
        if any(pattern in output for pattern in success_patterns):
            return
        raise EZFException(f"'{argv[0]}' failed (exit {rc})")


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class EZFlashApp(App):
    TITLE = "Klipper EZFlash"
    # The command palette (default Ctrl+P) isn't relevant to this app and
    # was cluttering the footer ("^p palette" alongside "q Quit") — off.
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Force Quit", show=False),
    ]
    CSS = f"""
    Screen {{
        background: {_BG};
        color: {_TEXT};
    }}
    Header {{
        background: {_PANEL};
        color: {_ACCENT};
    }}
    Footer {{
        background: {_PANEL};
    }}
    LoadingIndicator {{
        color: {_ACCENT};
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._board_db: Optional[BoardDatabase] = None
        self._katapult_checked = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator(id="startup-loading")
        yield Footer()

    async def on_mount(self) -> None:
        self.run_worker(self.launch(), exclusive=True)

    def _clear_startup_loading(self) -> None:
        """Remove the startup LoadingIndicator, if it's still mounted.
        Called right before the first dialog of launch() appears, so the
        user gets visual feedback during whatever setup work (board
        database parsing, etc.) happens before that point instead of a
        blank-looking screen that reads as "frozen"."""
        try:
            self.query_one("#startup-loading", LoadingIndicator).remove()
        except Exception:
            pass

    # ---- thin awaitable wrappers replacing the old dialog calls ----------

    async def msgbox(self, message: str, title: str = "") -> None:
        await self.push_screen_wait(MessageDialog(message, title))

    async def yesno(self, message: str, title: str = "", yes_label: str = "Yes", no_label: str = "No", default_no: bool = False) -> bool:
        return await self.push_screen_wait(ConfirmDialog(message, title, yes_label, no_label, default_no))

    async def menu(self, choices: Sequence[tuple[str, str]], message: str = "", title: str = "", cancel_label: str = "Cancel") -> Optional[str]:
        return await self.push_screen_wait(ChoiceDialog(choices, message, title, cancel_label))

    async def button_menu(self, choices: Sequence[tuple[str, str]], message: str = "", title: str = "", cancel_label: Optional[str] = None) -> Optional[str]:
        return await self.push_screen_wait(ButtonChoiceDialog(choices, message, title, cancel_label))

    async def inputbox(self, prompt: str, initial: str = "", title: str = "") -> Optional[str]:
        return await self.push_screen_wait(InputDialog(prompt, initial, title))

    # ---- katapult checkout check / clone -----------------------------

    async def check_katapult(self) -> None:
        if self._katapult_checked:
            return

        katapult_path = find_katapult()
        if katapult_path is not None:
            self._katapult_checked = True
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(katapult_path), "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                error_msg = (stderr.decode(errors="replace") or f"Pull failed with exit code {proc.returncode}").strip()
                await self.msgbox(f"Unable to update katapult from git:\n\n{error_msg}", title="Katapult update failed")
            return

        self._katapult_checked = True
        tag = await self.menu(
            choices=[("clone", "Clone katapult now"), ("quit", "Quit")],
            message=(
                "KATAPULT NOT FOUND\n\n"
                "Klipper-ezflash requires 'katapult' (a bootloader flash helper) to be installed.\n\n"
                f"It can be cloned from:\n  {_KATAPULT_CLONE_URL}"
            ),
            title="Katapult not found",
            cancel_label="Quit",
        )
        if tag == "clone":
            await self.clone_katapult()

    async def clone_katapult(self) -> None:
        clone_target = Path.home() / "katapult"
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", _KATAPULT_CLONE_URL, str(clone_target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = (stderr.decode(errors="replace") or f"Clone failed with exit code {proc.returncode}").strip()
            await self.msgbox(f"Failed to clone katapult:\n\n{error_msg}", title="Clone failed")
            return
        await self.msgbox(
            f"Katapult has been cloned successfully!\n\nLocation: {clone_target}\n\nThe tool will now check for it again.",
            title="Katapult cloned",
        )
        self._katapult_checked = False
        await self.check_katapult()

    # ---- board selection wizard ---------------------------------------

    async def select_board(self):
        if self._board_db is None:
            self._board_db = BoardDatabase()
        bdb = self._board_db
        boards = list(bdb.get_all())

        manufacturers = tuple(sorted(set(b.manufacturer for b in boards)))
        tag = await self.menu(
            choices=[(str(i), n) for i, n in enumerate(manufacturers)],
            message="Select manufacturer",
        )
        if tag is None:
            return None
        selected_mfr = manufacturers[int(tag)]

        mfr_models = tuple(sorted(set(b.model for b in boards if b.manufacturer == selected_mfr)))
        tag = await self.menu(
            choices=[(str(i), n) for i, n in enumerate(mfr_models)],
            message="Select model",
        )
        if tag is None:
            return None
        selected_model = mfr_models[int(tag)]

        board_variants = tuple(sorted(set(
            b.variant for b in boards if b.manufacturer == selected_mfr and b.model == selected_model
        )))
        if len(board_variants) > 1:
            tag = await self.menu(
                choices=[(str(i), n) for i, n in enumerate(board_variants)],
                message="Select variant",
            )
            if tag is None:
                return None
            selected_variant = board_variants[int(tag)]
        else:
            selected_variant = board_variants[0]

        return bdb.get_board(selected_mfr, selected_model, selected_variant)

    # ---- settings menu --------------------------------------------------

    async def edit_int_setting(self, title: str, current_value: int) -> int:
        result = await self.inputbox(
            f"Enter a custom value for the {title} (default: {current_value})",
            initial=str(current_value),
        )
        if result is None:
            return current_value
        try:
            new_value = int(result)
            if new_value < 0:
                await self.msgbox(f"Value must be non-negative (got {new_value}).\nUsing previous value.")
                return current_value
            return new_value
        except ValueError:
            await self.msgbox(f"Invalid input '{result}'. Must be a positive integer.\nUsing previous value.")
            return current_value

    async def choose_rate(self, title: str, current_value: int, options: list[tuple[str, int]]) -> int:
        preset_values = [v for _, v in options]
        while True:
            choices = [(str(v), f"{'*' if v == current_value else ' '} {label}") for label, v in options]
            choices.append(("__custom__", f"{'*' if current_value not in preset_values else ' '} Enter a custom value"))
            tag = await self.menu(choices=choices, message=title)
            if tag is None:
                return current_value
            if tag == "__custom__":
                new_value = await self.edit_int_setting(title, current_value)
                if new_value != current_value:
                    return new_value
                continue
            return int(tag)

    async def settings_menu(self) -> None:
        settings = InterfaceSettings()
        while True:
            choices = [
                ("can_speed", f"CAN bit rate: {settings.can_speed} bps"),
                ("uart_baudrate", f"UART baud rate: {settings.uart_baudrate} baud"),
            ]
            try:
                can_list = await asyncio.to_thread(detect_mod.list_can_interfaces)
            except Exception:
                can_list = []
            if len(can_list) > 1:
                choices.append(("can_interface", f"CAN interface: {settings.can_interface}"))
            choices.append(("back", "Back to main menu"))

            tag = await self.menu(choices=choices, title="Communication Settings", cancel_label="Back")

            if tag == "can_speed":
                new_value = await self.choose_rate(
                    "CAN bit rate selector (default 1M bps)",
                    settings.can_speed,
                    [("1M bps", 1000000), ("500K bps", 500000)],
                )
                if new_value != settings.can_speed:
                    try:
                        settings.can_speed = new_value
                        settings.save()
                        await self.msgbox(f"CAN Speed updated to {settings.can_speed} bps.")
                        # Offer to update the host's CAN networking config to match the new speed.
                        sync_now = await self.yesno(
                            f"Update the host's system CAN networking config to match "
                            f"({settings.can_speed} bps)? (sudo password may be required)",
                            yes_label="Update",
                            no_label="Not now",
                            default_no=True,
                        )
                        if sync_now:
                            try:
                                with self.suspend():
                                    print("\nApplying host CAN networking changes — you may be prompted for your sudo password below.\n")
                                    fix_can_host_state(settings.can_speed)
                                await self.msgbox("Host CAN networking has been updated.", title="CAN host state")
                            except Exception as e:
                                await self.msgbox(f"Failed to update host CAN networking:\n\n{e}", title="CAN host state")
                    except ValueError as e:
                        await self.msgbox(str(e))
            elif tag == "uart_baudrate":
                new_value = await self.choose_rate(
                    "UART baud rate selector (default 250K)",
                    settings.uart_baudrate,
                    [("250K baud", 250000), ("230.4K baud", 230400), ("115.2K baud", 115200)],
                )
                if new_value != settings.uart_baudrate:
                    try:
                        settings.uart_baudrate = new_value
                        settings.save()
                        await self.msgbox(f"UART Baud Rate updated to {settings.uart_baudrate} baud.")
                    except ValueError as e:
                        await self.msgbox(str(e))
            elif tag == "can_interface":
                try:
                    can_list = await asyncio.to_thread(detect_mod.list_can_interfaces)
                except Exception:
                    can_list = []
                if not can_list:
                    await self.msgbox("No CAN interfaces detected on host.")
                    continue
                tag2 = await self.menu(
                    choices=[(str(i), n) for i, n in enumerate(can_list)],
                    message="Select CAN interface to use:",
                )
                if tag2 is None:
                    continue
                chosen = can_list[int(tag2)]
                try:
                    settings.can_interface = chosen
                    settings.save()
                    await self.msgbox(f"CAN interface set to {chosen}")
                except Exception as e:
                    await self.msgbox(str(e))
            elif tag in ("back", None):
                return

    # ---- flash flow -------------------------------------------------------

    FLASH_STEP_NAMES = [
        "Compile bootloader",
        "Compile firmware ",
        "Verify device connection",
        "Flash bootloader",
        "Flash firmware",
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
                    )
                    if tag is None:
                        return
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
                    choices=[("begin", "Begin Flash"), ("reselect", "Reselect board/interface")],
                    message=summary,
                    title="Confirm Board & Interface",
                )
                if tag == "begin":
                    break
                elif tag == "reselect":
                    continue
                else:
                    return

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

        if session.step_index >= 4:
            await self.msgbox("A previously completed flash session was found. Starting fresh.")
            try:
                clear_session()
            except Exception:
                pass
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

                        # Board/Configurator setup is only needed if we're
                        # actually recompiling something — a fully-cached
                        # resume (e.g. the user rewired a CAN toolhead
                        # board and restarted the app, with no other build
                        # having touched these outputs since) skips this
                        # entirely.
                        bootloader_config = firmware_config = None
                        if cached_bootloader is None or cached_firmware is None:
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

                        # --- Step 0: compile bootloader (skip if the
                        # cached output from a previous run is still
                        # byte-for-byte unchanged) ---
                        if cached_bootloader is not None:
                            screen.start_step(0, f"Bootloader output unchanged since last run — skipping recompile.\n{cached_bootloader}")
                            bootloader_bin = cached_bootloader
                            screen.set_step(0, StepStatus.DONE)
                        else:
                            screen.start_step(0)
                            with _capture_output_to_log(screen):
                                bootloader_config.save_config(katapult_path / ".config")
                            await screen.run_argv("Cleaning katapult...", flash_mod.MAKE_CLEAN_ARGS, cwd=katapult_path)
                            await screen.run_argv("Compiling katapult...", flash_mod.MAKE_BUILD_ARGS, cwd=katapult_path)
                            bootloader_bin = flash_mod.katapult_output_path(katapult_path)
                            if not bootloader_bin.exists():
                                raise EZFException(f"Expected katapult binary not found at {bootloader_bin}")
                            session.bootloader_bin_hash = file_sha256(bootloader_bin)
                            save_session(session)
                            screen.set_step(0, StepStatus.DONE)

                        # --- Step 1: compile firmware (skip if the
                        # cached output from a previous run is still
                        # byte-for-byte unchanged) ---
                        if cached_firmware is not None:
                            screen.start_step(1, f"Firmware output unchanged since last run — skipping recompile.\n{cached_firmware}")
                            firmware_bin = cached_firmware
                            screen.set_step(1, StepStatus.DONE)
                        else:
                            screen.start_step(1)
                            with _capture_output_to_log(screen):
                                firmware_config.save_config(firmware_path / ".config")
                            await screen.run_argv("Cleaning klipper...", flash_mod.MAKE_CLEAN_ARGS, cwd=firmware_path)
                            await screen.run_argv("Compiling firmware...", flash_mod.MAKE_BUILD_ARGS, cwd=firmware_path)
                            firmware_bin = flash_mod.klipper_output_path(firmware_path)
                            if firmware_bin is None:
                                candidates = ", ".join(str(p) for p in flash_mod.klipper_output_candidates(firmware_path))
                                raise EZFException(f"Expected klipper firmware at one of {candidates}")
                            session.firmware_bin_hash = file_sha256(firmware_bin)
                            save_session(session)
                            screen.set_step(1, StepStatus.DONE)

                    if session.step_index <= 2:
                        arch = (session.mcu_arch or "").lower()

                        # --- Placeholder: tell the user to physically put the
                        # board into bootloader mode before we go looking for
                        # it. This blocks (via msgbox) so they have a chance
                        # to act on it before verification/flashing proceeds.
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

                        screen.start_step(2, "Re-checking bootloader before flashing. Please keep the device connected.")
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

                        screen.set_step(2, StepStatus.DONE)

                        screen.start_step(3)
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
                        screen.set_step(3, StepStatus.DONE)

                        session.step_index = 3
                        save_session(session)

                    if session.step_index <= 3:
                        screen.start_step(4)
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
                            # pick which serial device it actually is.
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
                                tag = await self.menu(
                                    choices=[(str(i), str(p)) for i, p in enumerate(ports)],
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
                                klipper_dev = await asyncio.to_thread(detect_mod.wait_for_klipper_device, known=known_klipper_devs, timeout=10.0)
                                if not klipper_dev:
                                    raise EZFException("No Klipper device detected after flashing.\n\nPlease check the connection.")
                                klipper_path = str(klipper_dev)
                        screen.set_step(4, StepStatus.DONE)
                        session.step_index = 4
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
                    summary_lines = ["Flash flow completed successfully."]
                    if is_canbridge:
                        if katapult_serial_path:
                            summary_lines.append(f"Katapult serial: {katapult_serial_path}")
                        if can_uid:
                            summary_lines.append(f"CAN UUID: {can_uid}")
                    elif is_can_only:
                        if can_uid:
                            summary_lines.append(f"CAN UUID: {can_uid}")
                    else:
                        if katapult_serial_path:
                            summary_lines.append(f"Katapult serial: {katapult_serial_path}")
                        if klipper_path:
                            summary_lines.append(f"Klipper serial: {klipper_path}")
                    summary = "\n".join(summary_lines)

                    screen.write(f"\n{summary}")
                    await self.msgbox(summary, title="Done")
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

    # ---- top-level menu loop ------------------------------------------


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

    async def prepare_can_host_menu(self) -> None:
        settings = InterfaceSettings()
        problems = await asyncio.to_thread(check_can_host_state, settings.can_speed)
        if not problems:
            await self.msgbox(
                f"Host CAN networking looks correctly configured for {settings.can_speed} bps.",
                title="CAN host state",
            )
            return
        problem_list = "\n".join(f"  - {p}" for p in problems)
        proceed = await self.yesno(
            f"Found issues with the host's CAN networking setup:\n\n{problem_list}\n\n"
            "Fix these now? (sudo password may be required)",
            yes_label="Fix",
            no_label="Cancel",
            default_no=True,
        )
        if not proceed:
            return
        try:
            with self.suspend():
                print("\nApplying host CAN networking changes — you may be prompted for your sudo password below.\n")
                fix_can_host_state(settings.can_speed)
            await self.msgbox("Host CAN networking has been updated.", title="CAN host state")
        except Exception as e:
            await self.msgbox(f"Failed to fix host CAN networking:\n\n{e}", title="CAN host state")

    async def menus(self) -> None:
        while True:
            tag = await self.menu(
                choices=[
                    ("boardinfo", "Lookup information about a board"),
                    ("flash", "Flash a board"),
                    ("settings", "Edit the default communication settings"),
                    ("preparecan", "Prepare host OS for CAN"),
                    ("crash", "Intentionally crash"),
                    ("exit", "Exit"),
                ],
                title="Main Menu",
                message="What would you like to do?",
                cancel_label="Quit",
            )
            if tag is None or tag == "exit":
                self.exit()
                return
            if tag == "boardinfo":
                board = await self.select_board()
                if board:
                    await self.msgbox(board.pretty(), title="Board Info")
            elif tag == "settings":
                await self.settings_menu()
            elif tag == "preparecan":
                await self.prepare_can_host_menu()
            elif tag == "flash":
                try:
                    await self.start_flash_flow()
                except Exception as e:
                    await self.msgbox(str(e), title="Error")
            elif tag == "crash":
                raise RuntimeError("Intentional Crash")

    async def launch(self) -> None:
        try:
            # WIP warning and board-database load/warnings both happen
            # BEFORE the resume-session check now — previously they were
            # queued to run only after start_flash_flow(resume_session=...)
            # returned, so choosing "Resume previous flash" meant these
            # warnings didn't show until the resumed flash had already
            # finished (or failed), which looked like they'd appeared at
            # the wrong time entirely.
            self._clear_startup_loading()
            proceed = await self.yesno(
                "This is WIP tooling.\n\nIt may eat your cat and/or firmware\nBe warned!",
                default_no=True,
                yes_label="Risk It",
                no_label="Quit",
            )
            if not proceed:
                self.exit()
                return

            try:
                import importlib
                m = importlib.import_module("klipper_ezflash.model")
                importlib.reload(m)
                self._board_db = m.BoardDatabase()
                warnings = self._board_db.get_load_warnings()
                if warnings:
                    max_show = 25
                    msg = "Issues detected while loading user_boards.json:\n\n"
                    msg += "\n".join(warnings[:max_show])
                    if len(warnings) > max_show:
                        msg += f"\n\n...and {len(warnings) - max_show} more"
                    proceed = await self.yesno(
                        msg + "\n\nDo you want to continue anyway?",
                        yes_label="Continue",
                        no_label="Quit",
                        default_no=False,
                    )
                    if not proceed:
                        self.exit()
                        return
                    try:
                        self._board_db.clear_load_warnings()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                existing = load_session()
            except Exception:
                existing = None
            if existing is not None:
                tag = await self.menu(
                    choices=[("resume", "Resume previous flash"), ("new", "Start a new flash")],
                    message="A previous flash session was detected.\n\nResume or start a new flash?",
                    cancel_label="Start new",
                )
                if tag == "resume":
                    try:
                        await self.start_flash_flow(resume_session=existing)
                    except Exception as e:
                        await self.msgbox(f"Failed to resume session: {e}")

            await self.check_katapult()
            await self.menus()
        except Exception as e:
            await self.handle_doom(e)

    @staticmethod
    def describe_error(e: Exception) -> str:
        if type(e) is NotImplementedError:
            return (
                "The functionality you have tried to use has not yet been implemented\n"
                f"The responsible part of the codebase said: {e.args}"
            )
        return f"{type(e)}\n{e.args}\nNo additional details are available"

    async def handle_doom(self, e: Exception) -> None:
        try:
            human_text = self.describe_error(e)
        except Exception:
            human_text = "Unknown error"
        try:
            await self.msgbox(
                f"A fatal error has occurred in EZFlash\n\n{human_text}\n\nThe program will now exit",
                title="Fatal Error",
            )
        except Exception:
            traceback.print_exc()
        self.exit(return_code=1)


def main() -> None:
    EZFlashApp().run()


if __name__ == "__main__":
    main()