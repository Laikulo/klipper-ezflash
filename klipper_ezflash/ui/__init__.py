"""
Textual port of the klipper-ezflash TUI.

Split by concern:
  - theme.py         color constants + shared dialog stylesheet
  - dialogs.py        generic Textual dialog toolkit (MessageDialog,
                       ConfirmDialog, ChoiceDialog, ButtonChoiceDialog,
                       InputDialog) — reusable, no klipper-ezflash-specific
                       logic
  - flash_screen.py   FlashFlowScreen (step tracker + live log) and its
                       subprocess-streaming helpers
  - flash_flow.py      FlashFlowMixin — start_flash_flow() and its
                       supporting methods; still Textual-coupled (calls
                       self.msgbox/self.yesno/etc. directly) — see
                       TODO's.md for the follow-up plan to make this
                       genuinely UI-independent
  - app.py            EZFlashApp itself (FlashFlowMixin + App), main()

No re-export shim here — __main__.py imports directly from `.ui.app`.
"""
