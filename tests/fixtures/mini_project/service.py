import argparse
import os
import threading
from typing import Optional, Union

from base import Base
from compat import builtin_str, urlparse
from models import Child, Other


def helper_fn():
    return 0


class Service:
    def __init__(self):
        self.child = Child()                # self.child 는 Child
        self.lock = threading.Lock()        # self.lock 은 표준 라이브러리 객체
        self.items = []                     # self.items 는 list

    def work(self, c: Child, name: str, *args, **kwargs):
        c.ping()                            # 매개변수 타입힌트 -> Child.ping
        self.child.ping()                   # self.속성 -> Child.ping
        self.lock.acquire()                 # 표준 라이브러리(타입 추적)
        self.items.append(name)             # 내장(타입 추적)
        name.strip()                        # 내장(타입힌트 str)
        kwargs.get("x")                     # 내장(**kwargs 는 dict)
        parser = argparse.ArgumentParser()
        parser.add_argument("-x")           # 외부 호출 결과를 담은 변수
        urlparse("http://x")                # compat 재수출 -> 표준 라이브러리
        builtin_str(3)                      # compat 별칭 -> 내장
        os.path.join("a", "b")
        len(args)

    def dispatch(self, b: Base):
        b.run()                             # Base.run + 자식들의 오버라이드

    def optional_hint(self, c: Optional[Child]):
        c.ping()                            # Optional[X] 는 X

    # ---- 아래는 '연결되면 안 되는' 함정들 (잘못된 연결 방지 확인용) ----
    def reassigned(self, c: Child):
        c = make_something()                # 재대입 -> 타입 포기
        c.ping()

    def shadowed(self, c: Child):
        def inner(c):                       # 안쪽 c 는 다른 변수
            return c.ping()
        return inner(c)

    def tuple_unpack(self, c: Child):
        c, rest = make_pair()               # 튜플 풀기는 단순 대입이 아니다
        c.ping()

    def loop_var(self):
        for c in make_many():               # 루프 변수는 타입을 모른다
            c.ping()

    def ambiguous_union(self, c: Union[Child, Other]):
        c.ping()                            # 후보가 둘 이상이면 포기
