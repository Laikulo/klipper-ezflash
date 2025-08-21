
class EZFException(Exception):
    def __init__(self, human_message, inner_exception=None):
        self.message = human_message
        self.e = inner_exception