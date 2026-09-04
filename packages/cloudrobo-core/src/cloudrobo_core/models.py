from dataclasses import dataclass


@dataclass
class ErrorResponse:
    error_code: str = ""
    error_msg: str = ""
