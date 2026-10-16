from models import Config, Session


Alias = Config


def via_module_alias():
    return Alias("x")                  # -> Config


def via_local_alias():
    K = Config
    return K("y")                      # -> Config


def shadowed(Config):
    return Config("z")                 # 매개변수가 가린다: 연결하지 않는다


def reassigned():
    k = Config
    k = print
    return k("w")                      # 두 가지 값이라 믿지 않는다


def two_classes(flag):
    k = Config if flag else Session
    return k()                         # 삼항식은 모른다: 연결하지 않는다


def shadowed_alias(Config):
    k = Config
    return k("v")                      # Config 는 매개변수이므로 클래스가 아니다: 연결하지 않는다
