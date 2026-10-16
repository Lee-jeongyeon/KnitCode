class A:
    def m(self):
        return "A"


class B(A):
    pass


class C(A):
    def m(self):
        return "C"


class D(B, C):
    pass


class E(B, C):
    def m(self):
        return super().m()


class Bad(A, B):                    # 파이썬도 거부하는 모순된 상속 (TypeError) -> 분석기는 죽지 않고 깊이 우선으로 대신한다
    def go(self):
        return self.m()


def use(d: D, e: E):
    d.m()
    e.m()
