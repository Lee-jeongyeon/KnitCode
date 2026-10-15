try:
    from .h import hv
except ImportError:
    hv = None

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .h import hv as hv2
else:
    hv2 = None


def fg():
    from .h import hf
    return hf()
