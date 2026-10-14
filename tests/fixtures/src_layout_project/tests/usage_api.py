import mypkg
import mypkg.utils as u
from mypkg import Client, fetch
from mypkg.utils import helper


def test_reexport():
    mypkg.fetch()                        # 패키지가 재수출한 함수
    fetch()                              # from mypkg import fetch
    mypkg.Client()                       # 재수출한 클래스 생성
    c = Client()
    c.close()                            # 재수출한 클래스의 인스턴스
    Client.close(c)                      # 재수출한 클래스 이름으로 직접 호출
    Client().get()                       # 생성자 호출 결과에 바로 붙은 메서드


def test_module_paths():
    helper()                             # from mypkg.utils import helper
    u.helper()                           # import mypkg.utils as u
    mypkg.utils.helper()                 # 점 표기로 모듈 트리를 따라감
    mypkg.models.Client().get()          # 하위 모듈의 클래스 + 생성자 직후 메서드


# ---- 아래는 '연결되면 안 되는' 함정들 ----
def test_shadowed_module():
    mypkg = object()                     # 지역 변수가 import 한 mypkg 를 가린다
    mypkg.fetch()


def test_shadowed_name(fetch):           # 매개변수가 import 한 fetch 를 가린다
    fetch()
