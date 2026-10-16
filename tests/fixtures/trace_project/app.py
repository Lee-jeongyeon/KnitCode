import core
from core import lazy_value  # noqa: F401  (core 에 없는 이름: 모듈 __getattr__ 이 불린다)


def main():
    c = core.Child()
    c.run()
    core.via_map([1, 2])
    core.via_sorted([3, 1])
    a = core.Vec(1) + core.Vec(2)
    a.double
    core.dyn_call("target_a")
    core.decorated()
    core.with_lambda(5)
    list(core.gen())


if __name__ == "__main__":
    main()
