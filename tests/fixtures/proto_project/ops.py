class Vec:
    def __init__(self, x=0):
        self.x = x

    def __add__(self, o):
        return self

    def __radd__(self, o):
        return self

    def __iadd__(self, o):
        return self

    def __sub__(self, o):
        return self

    def __eq__(self, o):
        return True

    def __lt__(self, o):
        return False

    def __neg__(self):
        return self

    def __getitem__(self, i):
        return i

    def __setitem__(self, i, v):
        pass

    def __len__(self):
        return 1

    def __iter__(self):
        return iter([])

    def __contains__(self, i):
        return True

    def __call__(self, a):
        return a

    def __str__(self):
        return "v"

    def __repr__(self):
        return "v"

    def __rmod__(self, o):
        return o

    def __format__(self, spec):
        return "v"


class Vec2(Vec):
    pass


class Plain:
    pass


class OnlyR:
    def __radd__(self, o):
        return o


def binary(a: Vec, b: Vec):
    return a + b


def reflected(v: Vec):
    return 1 + v


def unknown_left(x, v: Vec):
    return x + v


def same_type_missing(a: Plain, b: Plain):
    return a + b


def inherited(a: Vec2, b: Vec2):
    return a - b


def compare(a: Vec, b: Vec):
    return a == b, a < b, a != b


def reflected_compare(v: Vec):
    return 3 > v


def unary(v: Vec):
    return -v


def aug(v: Vec, w: Vec):
    v += w
    w -= v


def container(v: Vec):
    x = v[0]
    v[1] = 2
    n = len(v)
    for i in v:
        pass
    ok = 3 in v
    s = f"{v}"
    t = str(v)
    print("a", v)
    u = f"{v!r}"
    return x, n, ok, s, t, u


def call_it(v: Vec, p: Plain):
    v(1)
    p(1)


def same_only_r(a: OnlyR, b: OnlyR):
    return a + b


def untyped(q):
    q + q
    q[0]
    len(q)
    q(1)


def percent(v: Vec):
    return "%s" % v
