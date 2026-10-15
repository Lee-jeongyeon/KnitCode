from pkg_a.x import Base, f


class Child(Base):
    def m(self):
        f()
        f()
        return g()


def g():
    f()
    return Child().m()
