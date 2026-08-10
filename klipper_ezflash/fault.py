class EZFException(Exception):
    def __init__(self, human_message, inner_exception=None):
        self.message = human_message
        self.e = inner_exception


class AmbiguousCanUuidException(EZFException):
    """Raised when multiple CAN devices of the same firmware type are
    detected at once and it's not safe to silently pick one.

    Carries the candidate (uuid, firmware_type) list so a caller that
    knows how to disambiguate further (e.g. by querying MCU/CAN pins via
    detect.identify_can_device) can attempt that before falling back to
    asking the user, rather than only getting a human-readable message.
    """
    def __init__(self, human_message, candidates):
        super().__init__(human_message)
        self.candidates = candidates
