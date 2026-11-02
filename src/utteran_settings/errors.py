"""Value-free failures shared by storage, domain and HTTP."""

from utteran_contracts.settings import Issue


class SettingsError(Exception):
    def __init__(self, code: str, status: int = 503, issues: tuple[Issue, ...] = ()) -> None:
        self.code = code
        self.status = status
        self.issues = issues
        super().__init__(code)
