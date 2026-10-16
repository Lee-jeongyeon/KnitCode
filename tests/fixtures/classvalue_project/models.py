"""클래스 자체를 값으로 들고 다니다가 호출하는 경우 (cls(...), self.config_class(...), type[Foo] ...)"""
from typing import Type


class Config:
    def __init__(self, root):
        self.root = root


class Provider:
    def __init__(self, app):
        self.app = app


class FastProvider(Provider):          # __init__ 이 없으므로 Provider.__init__ 이 실행된다
    pass


class Session:
    def __init__(self):
        self.data = {}


class NullSession(Session):
    pass


class Client:
    def __init__(self, app):
        self.app = app


class Base:
    config_class = Config                              # 클래스 변수에 클래스를 담아 둠 (정확히 Config)
    provider_class: Type[Provider] = FastProvider      # 선언은 Provider (자식일 수 있다)
    session_class = Session
    client_class: "type[Client] | None" = None

    def make_config(self):
        return self.config_class("r")                  # -> Config

    def make_provider(self):
        return self.provider_class(self)               # -> Provider (+ 자식 FastProvider)

    def make_session(self):
        return self.session_class()                    # -> Session

    @classmethod
    def create(cls):
        return cls()                                   # -> Base (+ 자식 Child)

    def clone(self):
        return self.__class__()                        # -> Base (+ 자식 Child)

    def clone2(self):
        return type(self)()                            # -> Base (+ 자식 Child)

    def build(self, tag_class: type[Provider]):
        return tag_class(self)                         # -> Provider (+ 자식 FastProvider)

    def pick(self):
        k = self.client_class or Client
        return k(self)                                 # -> Client

    @classmethod
    def helper(cls):
        return 1

    @classmethod
    def uses_cls_method(cls):
        return cls.helper()                            # -> Base.helper


class Child(Base):
    pass
