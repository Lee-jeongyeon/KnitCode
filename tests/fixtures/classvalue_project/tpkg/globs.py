"""current_app: FlaskProxy 처럼 TYPE_CHECKING 안의 타입 표시용 클래스로 선언한 모듈 변수"""
from __future__ import annotations

import typing as t

from werkzeug.local import LocalProxy

if t.TYPE_CHECKING:
    from .app import App, Service

    class AppProxy(t.Protocol, App): ...          # 프로젝트 부모가 App 하나뿐: AppProxy 는 App

    class Both(App, Service): ...                 # 프로젝트 부모가 둘: 어느 쪽인지 모르므로 별칭으로 보지 않는다


current: AppProxy = LocalProxy(lambda: None)
both: Both = LocalProxy(lambda: None)
