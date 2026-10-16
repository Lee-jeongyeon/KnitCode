import json
import typing as t


class Provider:
    def dumps(self, obj):
        return json.dumps(obj)

    def to_int(self, v):
        return t.cast(int, v)
