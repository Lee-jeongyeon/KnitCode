import os
import re
import socket
from typing import Any, Callable, List

import models
from models import (Client, DEFAULTS, PATTERN, SubClient, counter, make_client, make_client_quoted, mystery,
                    rebinding, registry)


def return_type_local(c: Client):
    resp = c.send()                 # Client.send (+ SubClient.send 오버라이드)
    resp.json()                     # Resp.json
    resp.close()                    # Resp.close


def return_type_chain(c: Client):
    c.send().json()                 # Resp.json   (return_type)
    c.chain().ping()                # Client.ping (Self)
    make_client().ping()            # Client.ping (return_type)
    models.make_client().ping()     # Client.ping (모듈 경로 + return_type)
    make_client_quoted().ping()     # 문자열 반환 힌트


def optional_return(c: Client):
    m = c.maybe()
    m.json()                        # Optional[Resp] -> Resp.json


def module_vars():
    registry.get("a")               # Registry.get (module_var)
    models.registry.get("a")        # Registry.get (module_var, 점 표기)


def std_table():
    p = re.compile("x")
    p.match("y")                    # stdlib re (inferred)
    PATTERN.search("z")             # stdlib re (모듈 변수)
    s = socket.socket()
    s.connect(("h", 1))             # stdlib socket (inferred)
    name = os.path.join("a", "b")
    name.upper()                    # builtin (반환표)
    DEFAULTS.get("a")               # builtin (Final = {...})


def traps(c: Client):
    r = c.async_send()
    r.json()                        # async -> 코루틴이라 Resp 아님
    c.prop.json()                   # property
    d = c.decorated()
    d.json()                        # 알 수 없는 데코레이터
    u = c.untyped()
    u.json()                        # 반환 힌트 없음 + 지역 변수 반환
    rebinding.get("a")              # 재대입된 모듈 변수
    counter.get("a")                # global 로 바뀌는 모듈 변수
    mystery.get("a")                # Any


def shadow_param(registry):
    registry.get("a")               # 매개변수가 모듈 변수를 가림


def shadow_local():
    registry = models.Client()      # 지역 변수가 모듈 변수를 가림
    registry.ping()                 # Client.ping (지역 변수의 타입이 우선)
    registry.get("a")               # Client 에는 get 이 없음 -> 미확정


def tuple_rebind(c: Client):
    r, other = c.send(), 1          # 튜플 풀기 -> 타입 포기
    r.json()


def typing_and_builtin_traps(cb: Callable, anyv: Any, items: List[int]):
    items.append(1)                 # typing.List -> list (builtin)
    cb.run()                        # Callable : 타입을 모름
    anyv.get("a")                   # Any : 타입을 모름
    first = next(iter(items))
    first.bit_length()              # next() 는 무엇이든 돌려줄 수 있다 -> 추정 불가
    value = getattr(items, "x")
    value.run()                     # getattr() 도 마찬가지
