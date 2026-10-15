"""앱 모듈 : 참조·암묵 호출·중첩 정의 정답지"""
import threading

from deco import log_calls, tagged


def on_done():
    pass


def other():
    pass


class Resource:
    """자원 클래스"""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def label(self):
        return self.name.upper()

    @label.setter
    def label(self, value):
        self.name = value

    def _helper(self):
        pass

    def start(self):
        t = threading.Thread(target=self._helper)
        t.start()
        return self.label


class Child(Resource):
    pass


@log_calls
def decorated():
    pass


@tagged("x")
class Tagged:
    pass


class Outer:
    class Inner:
        def go(self):
            self.stop()

        def stop(self):
            pass


async def fetch():
    pass


def branchy(x, items):
    if x and items:
        for i in items:
            if i > 1:
                pass
    return [y for y in items if y]


def run():
    with Resource("a") as r:
        pass
    c = Child("b")
    callbacks = [on_done, other]
    handlers = {"x": on_done}
    map(other, [1])
    r2 = Resource("z")
    r2.label = "new"

    def helper():
        return on_done()

    sorted([2, 1], key=helper)
    return helper()


def rebound():
    def f():
        pass
    f = other
    f()


def dup(flag):
    if flag:
        def g():
            pass
    else:
        def g():
            pass
    g()


def outer_typed():
    r = Resource("q")

    def inner_use():
        r.start()
    inner_use()


def outer_shadow():
    r = Resource("q")

    def inner(r):
        r.start()
    inner(r)


def outer_hint(res: Resource):
    def inner():
        res.start()
    inner()


def outer_hint_shadow(res: Resource):
    def inner(res):
        res.start()
    inner(res)


def make():
    class Base:
        def ping(self):
            pass

    class Sub(Base):
        def go(self):
            self.ping()
