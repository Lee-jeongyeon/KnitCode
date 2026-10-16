import typing as t

from . import typing as ftyping
from .json import dumps


def run():
    ftyping.helper(1)
    return dumps(t.cast(dict, {}))
