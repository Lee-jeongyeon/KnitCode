"""컨테이너 원소 타입 · with-as · 프로퍼티 · 삼항식 · await · 진리값 · @overload · 외부 클래스 값"""
from __future__ import annotations

import typing as t
from contextvars import ContextVar

from fakelib import FakeBase, Map, Namespace


class Tag:
    def check(self, v):
        return True

    def tag(self, v):
        return v


class DictTag(Tag):
    def check(self, v):
        return False


class Item:
    def run(self):
        return 1


class Conn:
    def __enter__(self):
        return self                        # with Conn() as c : c 는 Conn

    def __exit__(self, *exc):
        return None

    def send(self):
        return 1


class Weird:
    def __enter__(self):
        return 5                           # as 변수는 int: 프로젝트 클래스가 아니다

    def __exit__(self, *exc):
        return None

    def run(self):
        return 2


class Box:
    def __init__(self):
        self.tags: list[Tag] = []
        self.by_name: dict[str, Item] = {}
        self.pair: tuple[Item, ...] = ()

    def loop_self(self):
        for tg in self.tags:
            tg.tag(1)                      # -> Tag.tag


class Holder:
    @property
    def item(self) -> Item:
        return Item()

    def via_self(self):
        self.item.run()                    # property 의 반환 힌트: Item.run


class Sized:
    def __len__(self):
        return 0


class Flag:
    def __bool__(self):
        return True


class Bag:
    def __getitem__(self, k):
        return k

    def get(self, k):
        return self[k]                     # self[k] -> Bag.__getitem__


class Req(FakeBase):
    pass


class Req2(FakeBase):
    def __init__(self):
        self.args = make_unknown()         # 프로젝트에서 정해지는 속성: 외부 부모가 준 것이 아니다


class Site:
    url_map_class = Map                    # 프로젝트 밖 클래스를 값으로

    def __init__(self):
        self.url_map = self.url_map_class()

    def bind(self):
        self.url_map.bind()                # 외부(fakelib)


_ns = Namespace()
signal = _ns.signal("x")                   # 외부 패키지 객체의 메서드가 돌려준 값
_cv: ContextVar[Item] = ContextVar("cv")


@t.overload
def get_item(silent: t.Literal[False] = False) -> Item: ...


@t.overload
def get_item(silent: bool = ...) -> Item | None: ...


def get_item(silent: bool = False) -> Item | None:
    return None


async def fetch() -> Item:
    return Item()


def make_unknown():
    return something_dynamic()
