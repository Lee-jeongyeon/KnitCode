import importlib
import operator


def compile(x):                       # 내장 compile 을 가리는 프로젝트 함수 -> 동적 호출이 아니다
    return x


def dyn(obj, name, code, k):
    getattr(obj, name)()
    getattr(obj, "fixed")
    setattr(obj, name, 1)
    delattr(obj, name)
    eval(code)
    exec(code)
    compile(code)
    importlib.import_module(name)
    __import__(name)
    globals()[name]()
    obj.__dict__[k]
    obj.__dict__["fixed"]
    type("X", (), {})
    operator.methodcaller(name)
    operator.methodcaller("fixed")


class Magic:
    def __getattr__(self, n):
        return n


GLOBAL = vars()["x"]
