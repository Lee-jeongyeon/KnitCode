import typing as t
from typing import Any

ResponseType = t.Union[str, bytes]


def helper(x: Any) -> str:
    return t.cast(str, x)
