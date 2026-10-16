import json as _json
from .provider import Provider


def dumps(obj):
    return Provider().dumps(obj)


def loads(s):
    return _json.loads(s)
