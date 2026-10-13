"""Safe messages for users; technical details belong in Python logs."""


class UserError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class StorageError(UserError):
    def __init__(self) -> None:
        super().__init__(
            "Impossibile leggere o salvare i dati del job. Verifica spazio e permessi di DATA_DIR.",
            503,
        )


class PDFInspectionError(Exception):
    """Expected PDF rejection with an explanatory, user-safe message."""
