import service
from service import Service


def main():
    s = Service()                           # 지역 변수 -> Service
    s.work(None, "n")
    service.helper_fn()                     # 모듈.함수
    print("done")
