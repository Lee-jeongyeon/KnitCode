import functools


def deco(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


class Base:
    def run(self):
        return self.step()

    def step(self):
        return 1


class Child(Base):
    def __init__(self):
        self.v = helper(1)

    def step(self):
        return 2


def helper(x):
    return inner_calc(x)


def inner_calc(x):
    return x + 1


def via_map(items):
    return list(map(convert, items))


def convert(x):
    return x * 2


def via_sorted(items):
    return sorted(items, key=keyfn)


def keyfn(x):
    return -x


class Vec:
    def __init__(self, n):
        self.n = n

    def __add__(self, other):
        return Vec(self.n + other.n)

    @property
    def double(self):
        return self.n * 2


def dyn_call(name):
    return globals()[name]()


def target_a():
    return "a"


@deco
def decorated():
    return helper(2)


def with_lambda(xs):
    f = lambda v: inner_calc(v)
    return f(xs)


def gen():
    yield helper(3)


def never_run():
    return helper(9)


def __getattr__(name):
    return 0
