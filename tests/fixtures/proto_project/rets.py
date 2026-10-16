class Product:
    def run(self):
        return 1


class Other:
    def run(self):
        return 2


class Factory:
    def make(self):
        return Product()

    def me(self):
        return self

    def maybe(self, f):
        if f:
            return Product()
        return None

    def mixed(self, f):
        if f:
            return Product()
        return Other()

    def local(self):
        p = Product()
        return p

    def gen(self):
        yield Product()
        return Product()

    async def am(self):
        return Product()

    @staticmethod
    def build():
        return Factory()

    @classmethod
    def create(cls):
        return cls()

    @property
    def prop(self):
        return Product()

    def shadowed(self):
        Product = Other
        return Product()

    def literal(self):
        return "x"


class SubFactory(Factory):
    def make(self):
        return Product()


def chained():                          # helper 보다 앞에 있어서 helper 의 타입을 안 뒤에야 알 수 있다 (두 번째 패스)
    return helper().make()


def helper():
    return Factory()


def go(f: Factory):
    f.make().run()
    f.me().make()
    f.maybe(1).run()
    f.mixed(1).run()
    f.local().run()
    f.prop.run()
    Factory.build().make()
    Factory.create().make()
    helper().make().run()
    chained().run()
    f.shadowed().run()
    f.am().run()
    f.gen().run()
