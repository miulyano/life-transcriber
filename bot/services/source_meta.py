from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SourceMetadata:
    """Metadata about a downloaded source — used for the result title and caption."""

    title: Optional[str] = None
    uploader: Optional[str] = None
