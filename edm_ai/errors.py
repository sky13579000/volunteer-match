class DmsError(Exception):
    """User-facing error carrying an HTTP status. Hidden documents are reported as 404, never 403."""

    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code, self.msg = code, msg
