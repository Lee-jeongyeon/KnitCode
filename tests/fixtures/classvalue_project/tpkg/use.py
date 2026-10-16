from .globs import both, current


def use():
    current.run()                      # 모듈 변수 타입 -> App.run
    current.service.go()               # 속성 체인: App.service 는 Service -> Service.go


def local_chain():
    s = current.service
    s.go()                             # 체인으로 정한 지역 변수 타입


def ambiguous():
    both.run()                         # Both 는 부모가 둘이라 타입을 모른다: 연결하지 않는다
