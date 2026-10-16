from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorResponse:
    error_code: str = ""
    error_msg: str = ""
    error_context: Any = None
