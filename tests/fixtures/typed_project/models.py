"""v2.3 정답지용: 반환 타입힌트 / 모듈 변수 / 표준 라이브러리 반환표 / 함정"""
from __future__ import annotations

import re
import threading
from typing import Any, Final, Optional

from typing_extensions import Self


def custom_decorator(fn):
    return fn


class Resp:
    def json(self) -> dict:
        return {}

    def close(self):
        pass


class Client:
    def __init__(self):
        self._lock = threading.Lock()

    def send(self) -> Resp:
        return Resp()

    def maybe(self) -> Optional[Resp]:
        return None

    def chain(self) -> Self:
        return self

    def ping(self):
        pass

    async def async_send(self) -> Resp:
        return Resp()

    @property
    def prop(self) -> Resp:
        return Resp()

    @custom_decorator
    def decorated(self) -> Resp:
        return Resp()

    def untyped(self):
        r = Resp()
        return r                            # 반환 힌트가 없고, 지역 변수를 돌려주므로 본문에서도 타입을 확정하지 못한다

    def run(self):
        resp = self.send()                  # self.send() 의 반환 타입 -> Resp
        resp.json()
        self.chain().ping()                 # Self 반환 -> Client.ping


class SubClient(Client):
    def send(self) -> Resp:                 # 오버라이드
        return Resp()

    def ping(self):
        pass


class Registry:
    def get(self, key):
        return key


registry = Registry()                       # 모듈 변수 (한 번만 대입)
rebinding = Registry()
rebinding = Registry()                      # 재대입 -> 믿지 않는다
PATTERN = re.compile("x")                   # 표준 라이브러리 반환표
DEFAULTS: Final = {"a": 1}                  # Final 은 값에서 타입을 알아낸다
mystery: Any = {}                           # Any -> 모른다
counter = Registry()


def bump():
    global counter
    counter = Registry()                    # global 로 바뀌는 변수 -> 믿지 않는다


def make_client() -> Client:
    return Client()


def make_client_quoted() -> "Client":
    return Client()
