from mypkg.utils import helper          # 패키지 안에서도 절대 import 를 쓴다 (src 레이아웃)
from .models import Client


def fetch():
    c = Client()
    helper()
    return c.get()
