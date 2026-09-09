"""Safe, provider-neutral LLM errors."""

from __future__ import annotations

import re
from urllib.parse import urlparse


class LLMError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        base_url: str = "",
        model: str = "",
        request_id: object = None,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.detail = message
        self.status_code = status_code
        self.retryable = retryable
        try:
            self.endpoint = urlparse(base_url).hostname or "default"
        except (TypeError, ValueError):
            self.endpoint = "default"
        self.model = re.sub(r"[\r\n]", "", str(model or ""))[:160] or "unknown"
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "", str(request_id or ""))[:128]
        self.request_id = safe_id or "-"
        status = f" status={status_code}" if status_code is not None else ""
        super().__init__(
            f"{code}: {message}; endpoint={self.endpoint} model={self.model} "
            f"request_id={self.request_id}{status}"
        )


class LLMResponseError(LLMError, ValueError):
    """The upstream responded but its envelope contained no usable text."""

    def __init__(
        self,
        code: str,
        *,
        base_url: str,
        model: str,
        request_id: object = None,
        content_length: int = 0,
    ) -> None:
        self.content_length = max(0, int(content_length or 0))
        super().__init__(
            code,
            f"content_length={self.content_length}",
            base_url=base_url,
            model=model,
            request_id=request_id,
            retryable=True,
        )
