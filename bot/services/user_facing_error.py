class UserFacingError(RuntimeError):
    """Provider-tagged error whose string stays backward-compatible."""

    def __init__(self, provider: str, detail: str):
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")
