"""
KnitCode 분석기 회귀 테스트

실행:   python tests/test_analyzer.py            (pytest 가 있으면  pytest tests  도 가능)
기준선: python tests/test_analyzer.py --update-baseline   (분석기를 일부러 개선했을 때만)

구성
  1) 미니 프로젝트(tests/fixtures/mini_project)        : 정답을 손으로 설계한 작은 프로젝트
       - 있어야 하는 연결 / 있으면 안 되는 연결(잘못된 연결 방지) / 외부 분류 결과를 확인
  2) src 레이아웃 프로젝트(tests/fixtures/src_layout_project) : 패키지가 src/ 아래에 있고 tests/ 가 절대 import 로 쓰는 구조
       - import 루트 보정, 재수출, 점 표기, 이름 가림(shadow)을 확인
  2-2) v2.3 타입 프로젝트(tests/fixtures/typed_project) : 반환 타입힌트 / 모듈 변수 / 표준 라이브러리 반환표 / 함정
  2-3) 구조 간선(imports / extends / contains)
  2-4) 참조·암묵 호출·중첩 정의·노드 메타데이터(tests/fixtures/refs_project)
  2-5) 그래프용 정보(tests/fixtures/graph_project) : import 종류·순환·star 확장 / 진입점 / 패키지→함수 계층 집계
  2-6) 타입·상속 보강(tests/fixtures/proto_project) : C3 MRO / 연산자·프로토콜·__call__ / 본문 반환 타입 추론 / 동적 호출 표시
  2-7) PyCG 비교 도구(compare_pycg.py) : 이름 맞추기·쌍 비교·원인 분류 (PyCG 를 설치하지 않아도 저장해 둔 PyCG 결과로 검증)
  3) 실제 프로젝트(Sublist3r, requests, requests 저장소 루트) : 기준선(baseline.json)보다 나빠지지 않았는지 확인
       - 프로젝트를 clone 하지 않았거나 버전이 다르면 건너뛴다
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import analyze_project as ap  # noqa: E402

FIXTURE = HERE / "fixtures" / "mini_project"
BASELINE = HERE / "baseline.json"
LAYOUT = HERE / "fixtures" / "src_layout_project"
TYPED = HERE / "fixtures" / "typed_project"
REFS = HERE / "fixtures" / "refs_project"
GRAPH = HERE / "fixtures" / "graph_project"
PROTO = HERE / "fixtures" / "proto_project"
SHADOW = HERE / "fixtures" / "shadow_project"
CLASSVAL = HERE / "fixtures" / "classvalue_project"
GENERIC = HERE / "fixtures" / "generic_project"
ROOTNAME = HERE / "fixtures" / "celery"            # 폴더 이름이 외부 패키지(celery)와 같다 (flask/examples/celery 에서 발견)
REAL_PROJECTS = {"Sublist3r": ROOT / "Sublist3r",
                 "requests": ROOT / "requests" / "src" / "requests",
                 "requests_root": ROOT / "requests",          # 저장소 루트(tests/ 포함)를 통째로 분석
                 "flask": ROOT / "flask" / "src" / "flask",   # 패키지 안에 typing.py, json/ 처럼 표준 라이브러리와 같은 이름이 있다
                 "click": ROOT / "click" / "src" / "click",   # 일반화 검증: 데코레이터·콜백이 많은 CLI 라이브러리
                 "httpx": ROOT / "httpx" / "httpx"}           # 일반화 검증: async 와 타입힌트가 많은 HTTP 클라이언트


MIN_PYTHON = {"click": (3, 10)}                          # click/utils.py 가 3.10 문법을 써서 3.9 에서는 그 파일이 건너뛰어진다 (skipped_files 로 보고됨)


class Skip(Exception):
    pass


def _skip(msg):
    import os
    if "PYTEST_CURRENT_TEST" in os.environ:                   # pytest 로 돌 때만 pytest.skip (직접 실행할 때는 Skip 으로 처리)
        import pytest
        pytest.skip(msg)
    raise Skip(msg)


_cache = {}


def mini():
    if "mini" not in _cache:
        _cache["mini"] = ap.analyze(FIXTURE)[1]
    return _cache["mini"]


def layout(sub=None):
    key = f"layout:{sub}"
    if key not in _cache:
        _cache[key] = ap.analyze(LAYOUT / sub if sub else LAYOUT)[1]
    return _cache[key]


CALL_TYPES = ("calls", "calls_override")


def _edges(result, etype=None):
    """호출 간선만 (구조 간선 imports/extends/contains 는 _structure 로 따로 본다)"""
    return [(e["from"], e["to"], e["type"], e.get("via")) for e in result["edges"]
            if (e["type"] == etype if etype else e["type"] in CALL_TYPES)]


def _structure(result, etype):
    return {(e["from"], e["to"]) for e in result["edges"] if e["type"] == etype}


def typed():
    if "typed" not in _cache:
        _cache["typed"] = ap.analyze(TYPED)[1]
    return _cache["typed"]


def graph():
    if "graph" not in _cache:
        _cache["graph"] = ap.analyze(GRAPH)[1]
    return _cache["graph"]


def proto():
    if "proto" not in _cache:
        _cache["proto"] = ap.analyze(PROTO)[1]
    return _cache["proto"]


def refs():
    if "refs" not in _cache:
        _cache["refs"] = ap.analyze(REFS)[1]
    return _cache["refs"]


# =====================================================================
# 1) 미니 프로젝트: 있어야 하는 연결
# =====================================================================
EXPECTED_EDGES = [
    # (from, to, type, via)
    ("base.py::Base.execute", "base.py::Base.run", "calls", None),
    ("base.py::Base.execute", "models.py::Child.run", "calls_override", None),     # 자식 오버라이드
    ("base.py::Base.execute", "models.py::Other.run", "calls_override", None),
    ("models.py::Child.__init__", "base.py::Base.__init__", "calls", None),        # super() + as 별칭 부모
    ("models.py::Child.run", "models.py::helper", "calls", None),
    ("service.py::Service.__init__", "models.py::Child", "calls", None),
    ("service.py::Service.work", "models.py::Child.ping", "calls", "type_hint"),    # c: Child
    ("service.py::Service.work", "models.py::Child.ping", "calls", "self_attr"),    # self.child = Child()
    ("service.py::Service.dispatch", "base.py::Base.run", "calls", "type_hint"),    # b: Base
    ("service.py::Service.dispatch", "models.py::Child.run", "calls_override", "type_hint"),
    ("service.py::Service.dispatch", "models.py::Other.run", "calls_override", "type_hint"),
    ("service.py::Service.optional_hint", "models.py::Child.ping", "calls", "type_hint"),   # Optional[Child]
    ("api.py::main", "service.py::Service", "calls", None),
    ("api.py::main", "service.py::Service.work", "calls", None),                    # s = Service()
    ("api.py::main", "service.py::helper_fn", "calls", None),                       # service.helper_fn()
    ("service.py::Service.shadowed", "service.py::Service.shadowed.<locals>.inner", "calls", "closure"),   # 중첩 함수 호출
]

# 있으면 안 되는 연결: 타입을 증명할 수 없는데 연결해 버리는 실수를 잡는다
FORBIDDEN_CALLERS = [
    ("service.py::Service.reassigned", "models.py::Child.ping"),          # 재대입
    ("service.py::Service.shadowed", "models.py::Child.ping"),            # 중첩 함수 인자와 이름이 겹침
    ("service.py::Service.shadowed.<locals>.inner", "models.py::Child.ping"),
    ("service.py::Service.tuple_unpack", "models.py::Child.ping"),        # 튜플 풀기로 재대입
    ("service.py::Service.loop_var", "models.py::Child.ping"),            # 루프 변수
    ("service.py::Service.ambiguous_union", "models.py::Child.ping"),     # Union[A, B]
]

# (호출한 함수, 호출 표현식) -> (category, package, confidence)
EXPECTED_EXTERNAL = {
    ("service.py::Service.__init__", "threading.Lock"): ("stdlib", "threading", "certain"),
    ("service.py::Service.work", "argparse.ArgumentParser"): ("stdlib", "argparse", "certain"),
    ("service.py::Service.work", "os.path.join"): ("stdlib", "os", "certain"),
    ("service.py::Service.work", "len"): ("builtin", "builtins", "certain"),
    ("service.py::Service.work", "urlparse"): ("stdlib", "urllib", "certain"),          # compat 재수출을 따라감
    ("service.py::Service.work", "builtin_str"): ("builtin", "builtins", "certain"),    # compat 별칭
    ("service.py::Service.work", "self.lock.acquire"): ("stdlib", "threading", "inferred"),
    ("service.py::Service.work", "self.items.append"): ("builtin", "builtins", "inferred"),
    ("service.py::Service.work", "name.strip"): ("builtin", "builtins", "inferred"),
    ("service.py::Service.work", "kwargs.get"): ("builtin", "builtins", "inferred"),
    ("service.py::Service.work", "parser.add_argument"): ("stdlib", "argparse", "inferred"),
    ("api.py::main", "print"): ("builtin", "builtins", "certain"),
}


def test_expected_edges_exist():
    missing = Counter(EXPECTED_EDGES) - Counter(_edges(mini()))
    assert not missing, f"있어야 할 연결이 없음(또는 개수가 모자람): {dict(missing)}"


def test_no_forbidden_edges():
    wrong = [(f, t) for f, t, _, _ in _edges(mini()) if (f, t) in FORBIDDEN_CALLERS]
    assert not wrong, f"증명할 수 없는데 연결된 호출(잘못된 연결): {wrong}"


def test_no_unexpected_edges():
    """정답지에 없는 연결이 새로 생기면 알려준다 (의도한 개선이면 EXPECTED_EDGES 에 추가)"""
    extra = Counter(_edges(mini())) - Counter(EXPECTED_EDGES)
    assert not extra, f"정답지에 없는 연결: {dict(extra)}"


def test_external_classification():
    ext = {(e["from"], e["detail"]): (e["category"], e["package"], e["confidence"])
           for e in mini()["external_edges"]}
    bad = {k: (v, ext.get(k)) for k, v in EXPECTED_EXTERNAL.items() if ext.get(k) != v}
    assert not bad, f"외부 분류가 다름 (기대, 실제): {bad}"


def test_no_project_unresolved():
    """'프로젝트 안 이름인데 못 찾음'은 분석기가 놓쳤다는 신호라서 0이어야 한다"""
    bad = [u for u in mini()["unresolved"] if u["category"] == "project_unresolved"]
    assert not bad, f"점검 필요: {bad}"


def test_traps_stay_unresolved():
    """함정 호출들은 '미확정'으로 남아야 한다 (연결도, 외부 분류도 되면 안 됨)"""
    unk = {(u["from"], u["expr"]) for u in mini()["unresolved"]}
    for caller in ("reassigned", "shadowed.<locals>.inner", "tuple_unpack", "loop_var", "ambiguous_union"):
        assert (f"service.py::Service.{caller}", "c.ping") in unk, f"{caller}: c.ping 이 미확정이 아님"


# =====================================================================
# 1-2) src 레이아웃 프로젝트
# =====================================================================
API, MODELS, UTILS = "src/mypkg/api.py", "src/mypkg/models.py", "src/mypkg/utils.py"
T_RE, T_MP = "tests/usage_api.py::test_reexport", "tests/usage_api.py::test_module_paths"
LAYOUT_EXPECTED = [
    # 패키지 안에서 `from mypkg.utils import helper` (절대 import) -> import 루트 보정이 필요
    (f"{API}::fetch", f"{MODELS}::Client", "calls", None),
    (f"{API}::fetch", f"{UTILS}::helper", "calls", None),
    (f"{API}::fetch", f"{MODELS}::Client.get", "calls", None),
    # tests -> 코드 : 패키지가 재수출한 이름
    (T_RE, f"{API}::fetch", "calls", None),                    # mypkg.fetch()
    (T_RE, f"{API}::fetch", "calls", None),                    # fetch()   (from mypkg import fetch)
    (T_RE, f"{MODELS}::Client", "calls", None),                # mypkg.Client()
    (T_RE, f"{MODELS}::Client", "calls", None),                # c = Client()
    (T_RE, f"{MODELS}::Client", "calls", None),                # Client().get() 의 Client()
    (T_RE, f"{MODELS}::Client.close", "calls", None),          # c.close()
    (T_RE, f"{MODELS}::Client.close", "calls", None),          # Client.close(c)  (재수출된 클래스 이름으로 직접 호출)
    (T_RE, f"{MODELS}::Client.get", "calls", "constructor"),   # Client().get()
    # tests -> 코드 : 모듈 경로
    (T_MP, f"{UTILS}::helper", "calls", None),                 # helper()
    (T_MP, f"{UTILS}::helper", "calls", None),                 # u.helper()
    (T_MP, f"{UTILS}::helper", "calls", "module_path"),        # mypkg.utils.helper()
    (T_MP, f"{MODELS}::Client", "calls", "module_path"),       # mypkg.models.Client()
    (T_MP, f"{MODELS}::Client.get", "calls", "constructor"),   # mypkg.models.Client().get()
]
LAYOUT_FORBIDDEN = [
    ("tests/usage_api.py::test_shadowed_module", f"{API}::fetch"),   # 지역 변수 mypkg 가 모듈을 가림
    ("tests/usage_api.py::test_shadowed_name", f"{API}::fetch"),     # 매개변수 fetch 가 import 를 가림
]


def test_layout_expected_edges():
    r = layout()
    missing = Counter(LAYOUT_EXPECTED) - Counter(_edges(r))
    assert not missing, f"있어야 할 연결이 없음(또는 개수가 모자람): {dict(missing)}"
    extra = Counter(_edges(r)) - Counter(LAYOUT_EXPECTED)
    assert not extra, f"정답지에 없는 연결: {dict(extra)}"


def test_layout_no_forbidden_edges():
    wrong = [(f, t) for f, t, _, _ in _edges(layout()) if (f, t) in LAYOUT_FORBIDDEN]
    assert not wrong, f"이름이 가려졌는데 import 로 연결됨: {wrong}"


def test_layout_import_alias_and_unresolved():
    r = layout()
    assert r["summary"]["import_aliases"] == {"mypkg": "src.mypkg"}, r["summary"]["import_aliases"]
    bad = [u for u in r["unresolved"] if u["category"] == "project_unresolved"]
    assert not bad, f"프로젝트 안 이름을 못 찾음: {bad}"
    unk = {(u["from"], u["expr"], u["category"]) for u in r["unresolved"]}
    assert ("tests/usage_api.py::test_shadowed_name", "fetch", "local_callable") in unk


def test_layout_run_on_package_dir():
    """분석 폴더가 패키지 자체(src/mypkg)여도 안에서 쓰는 절대 import 가 풀려야 한다"""
    r = layout("src/mypkg")
    got = set(_edges(r))
    for e in [("api.py::fetch", "utils.py::helper", "calls", None),
              ("api.py::fetch", "models.py::Client", "calls", None),
              ("api.py::fetch", "models.py::Client.get", "calls", None)]:
        assert e in got, f"없는 연결: {e}"
    assert r["summary"]["import_aliases"] == {}                                # 모듈 이름에 이미 패키지 이름이 붙어서 따로 보정할 것이 없다


# =====================================================================
# 1-3) v2.3 : 반환 타입힌트 / 모듈 변수 / 표준 라이브러리 반환표
# =====================================================================
U, M = "use.py", "models.py"
TYPED_EXPECTED = [
    # 반환 타입힌트: resp = c.send() -> Resp
    (f"{U}::return_type_local", f"{M}::Client.send", "calls", "type_hint"),
    (f"{U}::return_type_local", f"{M}::SubClient.send", "calls_override", "type_hint"),
    (f"{U}::return_type_local", f"{M}::Resp.json", "calls", "local_assign"),
    (f"{U}::return_type_local", f"{M}::Resp.close", "calls", "local_assign"),
    # 호출 결과에 바로 붙은 메서드 (c.send().json(), c.chain().ping() ...)
    (f"{U}::return_type_chain", f"{M}::Client.send", "calls", "type_hint"),
    (f"{U}::return_type_chain", f"{M}::SubClient.send", "calls_override", "type_hint"),
    (f"{U}::return_type_chain", f"{M}::Resp.json", "calls", "return_type"),
    (f"{U}::return_type_chain", f"{M}::Client.chain", "calls", "type_hint"),
    (f"{U}::return_type_chain", f"{M}::make_client", "calls", None),
    (f"{U}::return_type_chain", f"{M}::make_client", "calls", None),
    (f"{U}::return_type_chain", f"{M}::make_client_quoted", "calls", None),
] + [(f"{U}::return_type_chain", f"{M}::Client.ping", "calls", "return_type"),                 # Self / 함수 반환 / 모듈 경로 / 문자열 힌트
     (f"{U}::return_type_chain", f"{M}::SubClient.ping", "calls_override", "return_type")] * 4 + [
    (f"{U}::optional_return", f"{M}::Client.maybe", "calls", "type_hint"),
    (f"{U}::optional_return", f"{M}::Resp.json", "calls", "local_assign"),                    # Optional[Resp]
    # 모듈 변수: registry = Registry()
    (f"{U}::module_vars", f"{M}::Registry.get", "calls", "module_var"),                       # registry.get
    (f"{U}::module_vars", f"{M}::Registry.get", "calls", "module_var"),                       # models.registry.get
    # 함정 함수들에서도 '파라미터 힌트로 알 수 있는 호출'은 연결된다
    (f"{U}::traps", f"{M}::Client.async_send", "calls", "type_hint"),
    (f"{U}::traps", f"{M}::Client.decorated", "calls", "type_hint"),
    (f"{U}::traps", f"{M}::Client.untyped", "calls", "type_hint"),
    (f"{U}::traps", f"{M}::Resp.json", "calls", "self_attr"),                       # c.prop.json() : property 를 속성으로 읽으면 그 반환 힌트(Resp)가 타입이다
    (f"{U}::shadow_local", f"{M}::Client", "calls", None),
    (f"{U}::shadow_local", f"{M}::Client.ping", "calls", None),                               # 지역 변수 registry 의 타입이 우선
    (f"{U}::tuple_rebind", f"{M}::Client.send", "calls", "type_hint"),
    (f"{U}::tuple_rebind", f"{M}::SubClient.send", "calls_override", "type_hint"),
    # models.py 안
    (f"{M}::Client.send", f"{M}::Resp", "calls", None),
    (f"{M}::Client.async_send", f"{M}::Resp", "calls", None),
    (f"{M}::Client.prop", f"{M}::Resp", "calls", None),
    (f"{M}::Client.decorated", f"{M}::Resp", "calls", None),
    (f"{M}::Client.untyped", f"{M}::Resp", "calls", None),
    (f"{M}::SubClient.send", f"{M}::Resp", "calls", None),
    (f"{M}::Client.run", f"{M}::Client.send", "calls", None),                                 # self.send()
    (f"{M}::Client.run", f"{M}::SubClient.send", "calls_override", None),
    (f"{M}::Client.run", f"{M}::Resp.json", "calls", "local_assign"),                         # resp = self.send(); resp.json()
    (f"{M}::Client.run", f"{M}::Client.chain", "calls", None),
    (f"{M}::Client.run", f"{M}::Client.ping", "calls", "return_type"),                        # self.chain().ping() : -> Self
    (f"{M}::Client.run", f"{M}::SubClient.ping", "calls_override", "return_type"),
    (f"{M}::bump", f"{M}::Registry", "calls", None),
    (f"{M}::make_client", f"{M}::Client", "calls", None),
    (f"{M}::make_client_quoted", f"{M}::Client", "calls", None),
]

# (호출한 함수, 호출 표현식) -> (category, package, confidence)
TYPED_EXTERNAL = {
    (f"{U}::std_table", "re.compile"): ("stdlib", "re", "certain"),
    (f"{U}::std_table", "p.match"): ("stdlib", "re", "inferred"),            # p = re.compile(..) : 반환표
    (f"{U}::std_table", "PATTERN.search"): ("stdlib", "re", "inferred"),     # 모듈 변수 PATTERN = re.compile(..)
    (f"{U}::std_table", "s.connect"): ("stdlib", "socket", "inferred"),      # s = socket.socket()
    (f"{U}::std_table", "name.upper"): ("builtin", "builtins", "inferred"),  # name = os.path.join(..) -> str
    (f"{U}::std_table", "DEFAULTS.get"): ("builtin", "builtins", "inferred"),   # DEFAULTS: Final = {...}
    (f"{U}::typing_and_builtin_traps", "items.append"): ("builtin", "builtins", "inferred"),   # List[int] -> list
    # next()/getattr() 결과는 무엇이든 될 수 있어서 '타입 추적'이 아니라 메서드 이름 추정에 머문다
    (f"{U}::typing_and_builtin_traps", "first.bit_length"): ("builtin_type_method", "builtins", "heuristic"),
    (f"{U}::traps", "mystery.get"): ("builtin", "builtins", "certain"),         # `mystery: Any = {}` 도 값이 dict 리터럴이고 한 번만 대입됨
}

# 타입을 증명할 수 없어서 '미확정'으로 남아야 하는 호출: (호출한 함수, 표현식) -> category
TYPED_UNRESOLVED = {
    (f"{U}::traps", "r.json"): "unknown_receiver",            # async 함수 : 코루틴을 돌려줌
    (f"{U}::traps", "d.json"): "unknown_receiver",            # 알 수 없는 데코레이터
    (f"{U}::traps", "u.json"): "unknown_receiver",            # 반환 타입힌트 없음
    (f"{U}::traps", "rebinding.get"): "project_unresolved",   # 모듈에서 두 번 대입된 변수
    (f"{U}::traps", "counter.get"): "project_unresolved",     # global 로 바뀌는 변수
    (f"{U}::shadow_param", "registry.get"): "unknown_receiver",   # 매개변수가 모듈 변수를 가림
    (f"{U}::shadow_local", "registry.get"): "project_unresolved",  # 지역 변수의 타입(Client)에는 get 이 없음
    (f"{U}::tuple_rebind", "r.json"): "unknown_receiver",     # 튜플 풀기
    (f"{U}::typing_and_builtin_traps", "cb.run"): "unknown_receiver",     # Callable
    (f"{U}::typing_and_builtin_traps", "anyv.get"): "unknown_receiver",   # Any
    (f"{U}::typing_and_builtin_traps", "value.run"): "unknown_receiver",  # getattr() 결과
}


def test_typed_expected_edges():
    r = typed()
    missing = Counter(TYPED_EXPECTED) - Counter(_edges(r))
    assert not missing, f"있어야 할 연결이 없음(또는 개수가 모자람): {dict(missing)}"
    extra = Counter(_edges(r)) - Counter(TYPED_EXPECTED)
    assert not extra, f"정답지에 없는 연결(잘못된 연결 후보): {dict(extra)}"


def test_typed_external_classification():
    ext = {(e["from"], e["detail"]): (e["category"], e["package"], e["confidence"])
           for e in typed()["external_edges"]}
    bad = {k: (v, ext.get(k)) for k, v in TYPED_EXTERNAL.items() if ext.get(k) != v}
    assert not bad, f"외부 분류가 다름 (기대, 실제): {bad}"


def test_typed_traps_stay_unresolved():
    got = {(u["from"], u["expr"]): u["category"] for u in typed()["unresolved"]}
    wrong = {k: (v, got.get(k)) for k, v in TYPED_UNRESOLVED.items() if got.get(k) != v}
    assert not wrong, f"함정 호출이 미확정으로 남지 않음 (기대, 실제): {wrong}"
    ext_exprs = {(e["from"], e["detail"]) for e in typed()["external_edges"]}
    assert not (set(TYPED_UNRESOLVED) & ext_exprs), "함정 호출이 외부로 분류됨"


# =====================================================================
# 2-3) 구조 간선: imports / extends / contains
# =====================================================================
def test_structure_edges_mini_project():
    r = mini()
    assert _structure(r, "extends") == {("models.py::Child", "base.py::Base"), ("models.py::Other", "base.py::Base")}
    assert _structure(r, "imports") == {("api.py", "service.py"), ("models.py", "base.py"), ("service.py", "base.py"),
                                        ("service.py", "compat.py"), ("service.py", "models.py")}
    contains = _structure(r, "contains")
    assert ("models.py", "models.py::Child") in contains and ("models.py::Child", "models.py::Child.run") in contains
    assert ("api.py", "api.py::main") in contains
    nodes = {n["id"]: n for n in r["nodes"]}
    assert nodes["models.py::Child.run"]["parent"] == "models.py::Child"
    assert nodes["models.py::Child"]["parent"] == "models.py"
    assert nodes["models.py"]["type"] == "module"
    # 모든 함수/클래스/메서드는 정확히 하나의 부모를 가진다
    parents = Counter(t for _, t in contains)
    assert all(parents[n["id"]] == 1 for n in r["nodes"] if n["type"] != "module")


def test_structure_edges_external_bases_and_layout():
    r = typed()
    nodes = {n["id"]: n for n in r["nodes"]}
    assert _structure(r, "extends") == {("models.py::SubClient", "models.py::Client")}
    assert _structure(r, "imports") == {("use.py", "models.py")}
    lay = layout()
    imp = _structure(lay, "imports")
    assert ("src/mypkg/api.py", "src/mypkg/utils.py") in imp and ("src/mypkg/api.py", "src/mypkg/models.py") in imp
    assert ("tests/usage_api.py", "src/mypkg/__init__.py") in imp          # `import mypkg` / `from mypkg import ...`
    assert not any(f == t for f, t in imp), "자기 자신을 import 한 간선"


def test_summary_counts_structure():
    s = mini()["summary"]
    assert s["module_nodes"] == 5
    assert s["structure_edges"]["extends"] == 2 and s["structure_edges"]["imports"] == 5
    assert s["nodes"] == sum(1 for n in mini()["nodes"] if n["type"] != "module")


# =====================================================================
# 2-4) 중첩 정의 / 참조 간선 / 암묵 호출 / 노드 메타데이터  (tests/fixtures/refs_project)
# =====================================================================
A = "app.py"
REFS_CALLS = [
    (f"{A}::Outer.Inner.go", f"{A}::Outer.Inner.stop", "calls", None),                  # 중첩 클래스의 self.메서드()
    (f"{A}::run", f"{A}::Child", "calls", None),
    (f"{A}::run", f"{A}::Resource", "calls", None),
    (f"{A}::run", f"{A}::Resource", "calls", None),
    (f"{A}::run", f"{A}::run.<locals>.helper", "calls", "closure"),                   # 중첩 함수 호출
    (f"{A}::run.<locals>.helper", f"{A}::on_done", "calls", None),                      # 중첩 함수 안의 호출은 중첩 함수의 것
    (f"{A}::outer_typed", f"{A}::Resource", "calls", None),
    (f"{A}::outer_typed", f"{A}::outer_typed.<locals>.inner_use", "calls", "closure"),
    (f"{A}::outer_typed.<locals>.inner_use", f"{A}::Resource.start", "calls", None),    # 바깥 함수의 r = Resource() 를 물려받음
    (f"{A}::outer_shadow", f"{A}::Resource", "calls", None),
    (f"{A}::outer_shadow", f"{A}::outer_shadow.<locals>.inner", "calls", "closure"),
    (f"{A}::outer_hint", f"{A}::outer_hint.<locals>.inner", "calls", "closure"),
    (f"{A}::outer_hint.<locals>.inner", f"{A}::Resource.start", "calls", "type_hint"),   # 바깥 함수의 매개변수 타입힌트를 물려받음
    (f"{A}::outer_hint_shadow", f"{A}::outer_hint_shadow.<locals>.inner", "calls", "closure"),
]   # outer_shadow.<locals>.inner 의 r.start() 는 매개변수 r 이 가리므로 연결되면 안 된다

# (from, to, type, via) -> 횟수
REFS_EXTRA = {
    (f"{A}::Resource.start", f"{A}::Resource._helper", "references", "callback"): 1,   # Thread(target=self._helper)
    (f"{A}::Resource.start", f"{A}::Resource.label", "calls_implicit", "property"): 1,   # self.label
    (f"{A}::Tagged", "deco.py::tagged", "references", "decorator"): 1,                 # 클래스 데코레이터
    (f"{A}::decorated", "deco.py::log_calls", "references", "decorator"): 1,
    (f"{A}::run", f"{A}::Resource.__init__", "calls_implicit", "init"): 3,             # Resource() x2 + Child() (물려받은 __init__)
    (f"{A}::outer_typed", f"{A}::Resource.__init__", "calls_implicit", "init"): 1,
    (f"{A}::outer_shadow", f"{A}::Resource.__init__", "calls_implicit", "init"): 1,
    (f"{A}::run", f"{A}::Resource.__enter__", "calls_implicit", "context_manager"): 1,
    (f"{A}::run", f"{A}::Resource.__exit__", "calls_implicit", "context_manager"): 1,
    (f"{A}::run", f"{A}::Resource.label", "calls_implicit", "property"): 1,            # r2.label = ... (setter)
    (f"{A}::run", f"{A}::on_done", "references", "value"): 2,                           # 리스트 + 딕셔너리 안
    (f"{A}::run", f"{A}::other", "references", "value"): 1,
    (f"{A}::run", f"{A}::other", "references", "callback"): 1,                          # map(other, ...)
    (f"{A}::run", f"{A}::run.<locals>.helper", "references", "callback"): 1,           # sorted(key=helper)
    (f"{A}::rebound", f"{A}::other", "references", "value"): 1,                         # f = other
    ("deco.py::log_calls", "deco.py::log_calls.<locals>.wrapper", "references", "value"): 1,    # return wrapper
    ("deco.py::tagged", "deco.py::tagged.<locals>.decorate", "references", "value"): 1,
}


def _extra(result):
    c = Counter()
    for e in result["edges"]:
        if e["type"] in ("references", "calls_implicit"):
            c[(e["from"], e["to"], e["type"], e["via"])] += e["count"]
    return c


def test_refs_call_edges_exact():
    r = refs()
    assert Counter(_edges(r)) == Counter(REFS_CALLS), \
        f"차이: 부족 {dict(Counter(REFS_CALLS) - Counter(_edges(r)))} / 초과 {dict(Counter(_edges(r)) - Counter(REFS_CALLS))}"


def test_refs_reference_and_implicit_edges_exact():
    got = _extra(refs())
    assert got == Counter(REFS_EXTRA), \
        f"차이: 부족 {dict(Counter(REFS_EXTRA) - got)} / 초과 {dict(got - Counter(REFS_EXTRA))}"


def test_refs_traps():
    r = refs()
    unk = {(u["from"], u["expr"]): u["category"] for u in r["unresolved"]}
    assert unk[(f"{A}::rebound", "f")] == "local_callable"                  # 다시 대입된 이름은 중첩 함수 호출로 풀지 않는다
    assert unk[(f"{A}::dup", "g")] == "local_callable"                      # 이름이 겹치는 조건부 정의
    assert unk[(f"{A}::outer_shadow.<locals>.inner", "r.start")] == "unknown_receiver"   # 매개변수가 바깥 변수를 가림
    assert unk[(f"{A}::outer_hint_shadow.<locals>.inner", "res.start")] == "unknown_receiver"   # 타입힌트도 마찬가지
    # 중첩 클래스의 부모(같은 범위에 정의된 Base)는 이름으로 찾을 수 없으니 '외부 부모'라고 단정하면 안 된다
    assert unk[(f"{A}::make.<locals>.Sub.go", "self.ping")] == "local_callable"
    assert not any(e["from"] == f"{A}::make.<locals>.Sub.go" for e in r["external_edges"])
    ids = {n["id"] for n in r["nodes"]}
    assert not any(i.startswith(f"{A}::dup.<locals>") for i in ids), "이름이 겹치는 정의는 수집하지 않는다"
    assert r["summary"]["nested_defs_not_collected"] == 2
    # 호출 지점은 한 곳에서만 센다 (중첩 함수 안의 호출이 바깥 함수에 중복되지 않음)
    s = r["summary"]
    ext = sum(e["count"] for e in r["external_edges"])
    assert s["total_calls"] == sum(1 for e in r["edges"] if e["type"] == "calls") + ext + len(r["unresolved"])


def test_refs_structure_with_nested():
    r = refs()
    contains = _structure(r, "contains")
    for pair in [(f"{A}", f"{A}::Outer"), (f"{A}::Outer", f"{A}::Outer.Inner"), (f"{A}::Outer.Inner", f"{A}::Outer.Inner.go"),
                 (f"{A}::run", f"{A}::run.<locals>.helper")]:
        assert pair in contains, f"소속 간선이 없음: {pair}"
    parents = Counter(t for _, t in contains)
    assert all(parents[n["id"]] == 1 for n in r["nodes"] if n["type"] != "module")
    assert _structure(r, "extends") == {(f"{A}::Child", f"{A}::Resource")}
    nodes = {n["id"]: n for n in r["nodes"]}
    assert nodes[f"{A}::run.<locals>.helper"]["nested"] is True and nodes[f"{A}::run.<locals>.helper"]["type"] == "function"
    assert nodes[f"{A}::Outer.Inner"]["type"] == "class" and nodes[f"{A}::Outer.Inner.go"]["type"] == "method"


def test_node_metadata():
    nodes = {n["id"]: n for n in refs()["nodes"]}
    res = nodes[f"{A}::Resource"]
    assert (res["line"], res["end_lineno"], res["loc"]) == (15, 41, 27)
    assert res["docstring"] == "자원 클래스" and res["visibility"] == "public" and res["bases"] == []
    assert nodes[f"{A}::Child"]["bases"] == ["Resource"]
    helper = nodes[f"{A}::Resource._helper"]
    assert helper["visibility"] == "private" and helper["kind"] == "instance" and helper["signature"] == "(self)"
    assert nodes[f"{A}::Resource.__init__"]["visibility"] == "dunder"
    assert nodes[f"{A}::Resource.label"]["kind"] == "property"
    d = nodes[f"{A}::decorated"]
    assert d["decorators"] == ["log_calls"] and d["is_async"] is False
    assert nodes[f"{A}::fetch"]["is_async"] is True
    assert nodes[f"{A}::branchy"]["complexity"] == 7                     # 1 + if + and + for + if + 컴프리헨션 for + if
    assert nodes[f"{A}::branchy"]["signature"] == "(x, items)"
    mod = nodes[A]
    assert mod["type"] == "module" and mod["loc"] == 148 and mod["docstring"].startswith("앱 모듈")
    # 별도 노드가 된 중첩 함수의 복잡도는 바깥 함수에 더해지지 않는다
    assert nodes[f"{A}::run"]["complexity"] == 1
    for n in nodes.values():
        if n["type"] in ("function", "method", "class"):
            assert n["end_lineno"] >= n["line"] and n["loc"] == n["end_lineno"] - n["line"] + 1 and n["parent"] in nodes


def test_skipped_files_and_schema_version():
    r = refs()
    assert r["schema_version"] == ap.SCHEMA_VERSION == 1
    assert [s["file"] for s in r["skipped_files"]] == ["broken.py"]
    assert "SyntaxError" in r["skipped_files"][0]["error"]
    assert r["summary"]["skipped_files"] == 1 and r["summary"]["files"] == 2
    assert mini()["skipped_files"] == [] and mini()["schema_version"] == 1


# =====================================================================
# 구조 점검
# =====================================================================
def test_graph_integrity():
    r = mini()
    ids = {n["id"] for n in r["nodes"]}
    assert len(ids) == len(r["nodes"]), "노드 ID 중복"
    for e in r["edges"]:
        assert e["from"] in ids and e["to"] in ids, f"존재하지 않는 노드를 가리킴: {e}"
    ext_ids = {n["id"] for n in r["external_nodes"]}
    for e in r["external_edges"]:
        assert e["from"] in ids and e["to"] in ext_ids, f"외부 간선 오류: {e}"


def test_summary_adds_up():
    r = mini()
    s = r["summary"]
    ext = sum(e["count"] for e in r["external_edges"])
    unk = len(r["unresolved"])
    internal = sum(1 for e in r["edges"] if e["type"] == "calls")
    assert s["total_calls"] == internal + ext + unk, "전체 호출 수가 합계와 다름"
    assert s["internal_resolved"] == internal


# =====================================================================
# 2-5) 그래프용 정보
# =====================================================================
def _imp(result, a, b):
    return next(e for e in result["edges"] if e["type"] == "imports" and (e["from"], e["to"]) == (a, b))


def test_import_scopes():
    r = graph()
    cases = [("imp/a.py", "imp/b.py", "top", True), ("imp/d.py", "imp/c.py", "function", False),
             ("imp/e.py", "imp/f.py", "type_checking", False), ("imp/g.py", "imp/h.py", "conditional", True)]
    for a, b, scope, import_time in cases:
        e = _imp(r, a, b)
        assert (e["scope"], e["import_time"]) == (scope, import_time), (a, b, e)
    g = _imp(r, "imp/g.py", "imp/h.py")        # try/except + TYPE_CHECKING + 함수 안, 세 가지가 한 간선에 모인다
    assert g["scopes"] == {"conditional": 1, "function": 1, "type_checking": 1} and g["count"] == 3 and g["line"] == 2
    assert sum(e["count"] for e in r["edges"] if e["type"] == "imports") == len(
        [1 for e in r["edges"] if e["type"] == "imports" for _ in range(e["count"])])
    # classify_imports 단독: 클래스 본문은 최상단, else 쪽은 TYPE_CHECKING 이 아니다
    import ast
    tree = ast.parse("import a\nclass C:\n    import b\nif TYPE_CHECKING:\n    import c\nelse:\n    import d\n"
                     "def f():\n    if TYPE_CHECKING:\n        import e\n")
    got = {n.names[0].name: ap.classify_imports(tree)[id(n)] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    assert got == {"a": "top", "b": "top", "c": "type_checking", "d": "conditional", "e": "function"}, got


def test_import_cycles():
    r = graph()
    got = {(c["kind"], tuple(c["modules"])) for c in r["import_cycles"]}
    assert got == {("import_time", ("imp/a.py", "imp/b.py")), ("deferred", ("imp/c.py", "imp/d.py")),
                   ("type_only", ("imp/e.py", "imp/f.py"))}, got
    for c in r["import_cycles"]:
        assert c["path"][0] == c["path"][-1] == c["modules"][0] and set(c["path"]) == set(c["modules"])
        assert {e["from"] for e in c["edges"]} == set(c["modules"])
    by_id = {n["id"]: n for n in r["nodes"]}
    assert by_id["imp/a.py"]["import_cycles"] == by_id["imp/b.py"]["import_cycles"]
    assert "import_cycles" not in by_id["imp/g.py"] and "import_cycles" not in by_id["imp/h.py"]
    assert r["summary"]["import_cycles"] == {"import_time": 1, "deferred": 1, "type_only": 1}
    # 함정: 순환이 없는 프로젝트에서는 아무것도 보고하지 않는다
    assert mini()["import_cycles"] == [] and refs()["import_cycles"] == []


def test_tarjan_is_iterative_and_exact():
    n = 6000                                                    # 재귀로 구현했다면 RecursionError 가 나는 크기
    chain = {i: [i + 1] for i in range(n)}
    assert ap._tarjan_sccs(chain) == []
    chain[n] = [0]
    comps = ap._tarjan_sccs(chain)
    assert len(comps) == 1 and len(comps[0]) == n + 1
    two = {"a": ["b"], "b": ["a", "c"], "c": ["d"], "d": ["c"], "e": ["a"]}
    assert sorted(ap._tarjan_sccs(two)) == [["a", "b"], ["c", "d"]]


def test_star_imports():
    r = graph()
    calls = {(a, b) for a, b, t, _ in _edges(r) if a == "star/user.py::go"}
    assert calls == {("star/user.py::go", "star/lib.py::shown"),          # __all__ 에 있는 이름
                     ("star/user.py::go", "star/lib2.py::pub"),            # __all__ 이 없으면 공개 이름
                     ("star/user.py::go", "star/user.py::collide")}, calls  # 자기 정의가 star 로 들어온 같은 이름을 이긴다
    # not_exported(__all__ 밖), _priv(밑줄), x(__all__ 을 확정할 수 없음), os.path(프로젝트 밖)는 연결하면 안 된다
    assert not any(b.endswith(("::not_exported", "::_priv", "::x")) for _, b in calls)
    assert r["summary"]["star_imports"] == {"statements": 4, "project_statements": 3, "names_bound": 2}
    assert "star/lib2.py" in {e["to"] for e in r["edges"] if e["type"] == "imports" and e["from"] == "star/user.py"}
    assert _imp(r, "star/user.py", "star/lib.py")["star"] == 1
    # __all__ 해석
    import ast
    files = {"m": ("m.py", ast.parse('__all__ = ["a"]\n__all__ += ["b"]\n__all__.append("c")\n__all__.extend(["d"])')),
             "n": ("n.py", ast.parse('__all__ = ["a"] + other.__all__')),
             "o": ("o.py", ast.parse('def f():\n    global __all__\n    __all__ = ["z"]\n__all__ = ["a"]')),
             "p": ("p.py", ast.parse('x = 1'))}
    assert ap.collect_all_lists(files) == {"m": ["a", "b", "c", "d"], "n": None, "o": None}


ENTRY_EXPECTED = {
    ("console_script", "pkg/cli.py::main"), ("console_script", "pkg/cli.py::Runner.go"),
    ("main_guard", "tool.py"), ("main_call", "tool.py::main"),
    ("main_module", "pkg/__main__.py"), ("main_call", "pkg/cli.py::main"),
    ("cli_command", "pkg/cli.py::cli"), ("cli_command", "pkg/cli.py::sub"), ("cli_command", "pkg/cli.py::solo"),
    ("cli_command", "web/jobs.py::hello"), ("task", "web/jobs.py::add"), ("task", "web/jobs.py::sub"),
    ("route", "web/app.py::items"), ("route", "web/app.py::ping"), ("route", "web/other.py::other"),
    ("route", "web/routes.py::create_user"), ("web_hook", "web/app.py::pre"),
    ("public_api", "api/impl.py::alpha"), ("public_api", "star/lib.py::shown"),
    ("public_api", "pkg/core.py::run"), ("public_api", "pkg/core.py::Engine"),
    ("fixture", "tests/test_things.py::data"), ("test", "tests/test_things.py::test_a"),
    ("test", "tests/test_things.py::TestX.test_b"),
    # 앱 팩토리 · 클래스 기반 뷰 · URL 등록 호출
    ("route", "web2/factory.py::create_app.<locals>.f"), ("route", "web2/factory.py::create_app.<locals>.g"),
    ("route", "web2/other.py::legacy"), ("route", "web2/views.py::UserAPI.get"), ("route", "web2/views.py::UserAPI.post"),
    ("route", "web2/views.py::SubAPI.delete"), ("route", "web2/views.py::Item.get"), ("route", "web2/views.py::NsThing.put"),
    ("route", "web2/dj.py::Home.get"), ("route", "web2/dj.py::about"),
    ("route", "web2/dj.py::ItemViewSet.list"), ("route", "web2/dj.py::ItemViewSet.create"),
}


def test_entry_points():
    r = graph()
    got = {(e["kind"], e["id"]) for e in r["entry_points"]}
    assert got == ENTRY_EXPECTED, (sorted(got - ENTRY_EXPECTED), sorted(ENTRY_EXPECTED - got))
    by = {(e["kind"], e["id"]): e for e in r["entry_points"]}
    assert by[("route", "web/app.py::items")]["detail"] == "GET|POST /items"
    assert by[("route", "web/app.py::ping")]["detail"] == "GET /ping"
    assert by[("route", "web/other.py::other")]["detail"] == "GET /other"          # methods 를 안 쓰면 GET
    assert by[("route", "web/routes.py::create_user")]["detail"] == "POST /users"
    assert by[("console_script", "pkg/cli.py::main")]["source"] == "setup.py"
    assert by[("console_script", "pkg/cli.py::main")]["detail"].startswith("gp ")
    assert {e["category"] for e in r["entry_points"] if e["kind"] == "test"} == {"test"}
    # 함정: 이름만 같은 것은 진입점이 아니다
    for nid in ("web/app.py::nope",               # Cache().get 데코레이터 (Flask 객체가 아님)
                "web/lookalike.py::z",            # 직접 만든 Flask 클래스 (flask 에서 import 하지 않음)
                "pkg/_private.py::_hidden",       # 밑줄 이름은 다시 내보내도 공개 API 가 아니다
                "api/impl.py::beta",              # __all__ 에 없음
                "star/lib.py::not_exported", "tool.py::unused", "tests/test_things.py::TestX.helper",
                "tests/test_things.py::NotATest.test_c"):
        assert nid not in {e["id"] for e in r["entry_points"]}, nid
    assert not any(e["id"].startswith("setup.py") for e in r["entry_points"])      # ghost = nope.mod:f 는 무시
    for e in r["entry_points"]:
        assert e["confidence"] in ("certain", "inferred", "heuristic") and e["category"] in ("runtime", "api", "test")
    assert r["summary"]["entry_points"]["route"] == 16
    d = {e["id"]: e["detail"] for e in r["entry_points"] if e["kind"] == "route"}
    assert d["web2/factory.py::create_app.<locals>.f"] == "POST /f"                  # 앱 팩토리: 함수 안에서 만든 app
    assert d["web2/other.py::legacy"] == "GET|POST /legacy"                         # add_url_rule(path, name, func, methods=)
    assert d["web2/views.py::UserAPI.get"] == "GET /users (클래스 기반 뷰)"           # add_url_rule(view_func=Cls.as_view())
    assert d["web2/views.py::Item.get"] == "GET /items/<id>, /items2 (클래스 기반 뷰)"   # api.add_resource(Cls, path, path)
    assert d["web2/views.py::NsThing.put"] == "PUT /ns (클래스 기반 뷰)"               # @ns.route 가 붙은 클래스
    assert d["web2/views.py::SubAPI.delete"] == "DELETE (클래스 기반 뷰)"              # 프로젝트 안 부모를 거쳐 MethodView 를 상속
    assert d["web2/dj.py::about"] == "GET about/" and d["web2/dj.py::ItemViewSet.create"] == "POST (클래스 기반 뷰)"
    by_id = {e["id"]: e for e in r["entry_points"]}
    assert by_id["web2/factory.py::create_app.<locals>.f"]["in_function"] is True
    # 함정: 프레임워크 출처가 확인되지 않거나, 요청을 받는 메서드가 아닌 것
    for nid in ("web2/factory.py::not_flask.<locals>.n",        # Something() 은 Flask 가 아님
                "web2/factory.py::rebound.<locals>.r",          # app 을 다시 대입해서 믿을 수 없음
                "web2/fakes.py::LocalView.get", "web2/fakes.py::Plain.get", "web2/fakes.py::Plain.post",
                "web2/views.py::UserAPI.helper", "web2/dj.py::Home.render", "web2/dj.py::hidden",
                "web2/dj.py::ItemViewSet.helper"):
        assert nid not in by_id, nid
    assert not any(e["id"].startswith("dyn/") for e in r["entry_points"])      # __all__ 을 확정 못 하면 추측하지 않는다
    # 분석 폴더가 setup.py 보다 안쪽(graph_project/pkg)이어도 위쪽 폴더의 설정 파일에서 실행 명령을 찾는다
    inner = ap.analyze(GRAPH / "pkg")[1]
    assert {(e["kind"], e["id"]) for e in inner["entry_points"] if e["kind"] == "console_script"} == {
        ("console_script", "cli.py::main"), ("console_script", "cli.py::Runner.go")}


def test_stdlib_name_collision_inside_package():
    """패키지 안에 typing.py, json/ 이 있어도 `import typing`, `import json` 은 표준 라이브러리다 (Flask 에서 발견)"""
    for sub, pre in (("pkg", ""), (".", "pkg/")):
        r = ap.analyze(SHADOW / sub)[1]
        got = {(e["from"], e["to"], e["type"]) for e in r["edges"] if e["type"] not in ("contains",)}
        want = {(pre + "app.py::run", pre + "typing.py::helper", "calls"),                       # from . import typing  -> 프로젝트 모듈
                (pre + "app.py::run", pre + "json/__init__.py::dumps", "calls"),
                (pre + "json/__init__.py::dumps", pre + "json/provider.py::Provider.dumps", "calls"),
                (pre + "json/__init__.py::dumps", pre + "json/provider.py::Provider", "calls"),
                (pre + "__init__.py", pre + "json/__init__.py", "imports"),
                (pre + "app.py", pre + "json/__init__.py", "imports"),
                (pre + "app.py", pre + "typing.py", "imports"),
                (pre + "json/__init__.py", pre + "json/provider.py", "imports")}
        assert got == want, (sub, got ^ want)
        # provider.py 의 `import json` 은 표준 라이브러리: 프로젝트 json/__init__.py 로 가는 import·호출·순환이 없다
        assert r["import_cycles"] == []
        ext = {(e["from"], e["detail"], e["category"], e["package"]) for e in r["external_edges"]}
        assert (pre + "json/provider.py::Provider.dumps", "json.dumps", "stdlib", "json") in ext
        assert (pre + "app.py::run", "t.cast", "stdlib", "typing") in ext
        assert (pre + "typing.py::helper", "t.cast", "stdlib", "typing") in ext
        assert r["unresolved"] == []


def test_newer_builtins_are_builtin_on_any_python(tmp_path):
    """3.11 의 BaseExceptionGroup 같은 새 내장 이름도 낮은 버전 파이썬으로 분석할 때 내장으로 분류한다 (실행 환경에 따라 결과가 달라지지 않게)"""
    assert {"aiter", "anext", "ExceptionGroup", "BaseExceptionGroup", "EncodingWarning"} <= ap.BUILTIN_NAMES
    (tmp_path / "m.py").write_text("def f(it, errs):\n    x = anext(it)\n    raise BaseExceptionGroup('x', errs)\n", encoding="utf-8")
    r = ap.analyze(tmp_path)[1]
    assert r["unresolved"] == []
    assert {(e["detail"], e["category"]) for e in r["external_edges"]} == {("anext", "builtin"), ("BaseExceptionGroup", "builtin")}


def test_root_folder_name_is_not_a_project_import():
    """분석 폴더 이름이 celery 라고 해서 `from celery import Celery` 가 프로젝트 안의 이름이 되지는 않는다"""
    r = ap.analyze(ROOTNAME)[1]
    assert r["unresolved"] == [] and r["summary"]["unresolved"].get("project_unresolved", 0) == 0
    ext = {(e["from"], e["detail"], e["category"], e["package"]) for e in r["external_edges"]}
    assert ext == {("tasks.py::make_app", "Celery", "third_party", "celery"),
                   ("tasks.py::make_app", "app.config_from_object", "third_party", "celery")}      # 외부 호출 결과를 담은 변수까지 따라간다
    assert [(e["kind"], e["id"]) for e in r["entry_points"]] == [("task", "tasks.py::add")]


def test_importance_and_reading_order():
    r = graph()
    by = {n["id"]: n for n in r["nodes"]}
    imp = [n["importance"] for n in r["nodes"] if n["type"] != "module"]
    assert max(imp) == 1.0 and min(imp) >= 0
    assert max(n["importance"] for n in r["nodes"] if n["type"] == "module") == 1.0
    # pkg_a/x.py::f 는 여러 곳에서 불리는 핵심, 아무도 안 부르는 것은 낮다
    assert by["pkg_a/x.py::f"]["importance"] > by["pkg_b/y.py::g"]["importance"] > 0
    assert by["tool.py::unused"]["importance"] < by["tool.py::helper"]["importance"]
    # 테스트 파일에서 나가는 간선은 중요도에 반영하지 않는다 (tests/ 가 부르는 것만으로 오르지 않음)
    pr = ap.compute_importance({"a.py": {"id": "a.py", "type": "module", "file": "a.py"},
                                "tests/test_a.py::t": {"id": "tests/test_a.py::t", "type": "function", "file": "tests/test_a.py"},
                                "a.py::f": {"id": "a.py::f", "type": "function", "file": "a.py"},
                                "a.py::g": {"id": "a.py::g", "type": "function", "file": "a.py"}},
                               [{"from": "tests/test_a.py::t", "to": "a.py::f", "type": "calls"}])
    assert pr == 3
    nodes2 = {"a.py::f": {"id": "a.py::f", "type": "function", "file": "a.py"},
              "a.py::g": {"id": "a.py::g", "type": "function", "file": "a.py"},
              "tests/test_a.py::t": {"id": "tests/test_a.py::t", "type": "function", "file": "tests/test_a.py"}}
    ap.compute_importance(nodes2, [{"from": "tests/test_a.py::t", "to": "a.py::f", "type": "calls"}])
    assert nodes2["a.py::f"]["importance"] == nodes2["a.py::g"]["importance"] == 1.0         # 테스트가 부른다고 f 가 중요해지지 않는다
    nodes3 = {"a.py::f": {"id": "a.py::f", "type": "function", "file": "a.py"}, "a.py::g": {"id": "a.py::g", "type": "function", "file": "a.py"}}
    ap.compute_importance(nodes3, [{"from": "a.py::g", "to": "a.py::f", "type": "calls"}])
    assert nodes3["a.py::f"]["importance"] == 1.0 and nodes3["a.py::g"]["importance"] < 1.0   # 실제 코드가 부르면 중요해진다
    ro = r["reading_order"]
    mods = [m["id"] for m in ro["modules"]]
    assert [m["rank"] for m in ro["modules"]] == list(range(1, len(mods) + 1))
    assert not any(m.startswith("tests/") for m in mods) and "pkg/_private.py" in mods
    assert "pkg_a/__init__.py" not in mods                                   # 내용 없는 파일은 제외
    depth = [m["min_depth"] for m in ro["modules"]]
    reach = [d for d in depth if d is not None]
    assert reach == sorted(reach) and depth[len(reach):] == [None] * (len(depth) - len(reach)), depth
    first = {m["id"]: m for m in ro["modules"]}
    assert first["tool.py"]["read_first"][0] == "tool.py::main" and "main_guard" in first["tool.py"]["entry"]
    assert first["pkg_a/x.py"]["min_depth"] is None                            # 진입점에서 닿지 않는 파일은 뒤로
    assert first["imp/a.py"]["reasons"][-1] == "순환 import 에 포함"
    nodes = ro["nodes"]
    assert len(nodes) <= ap.READING_NODES and [x["rank"] for x in nodes] == list(range(1, len(nodes) + 1))
    keys = [(x["depth"] is None, x["depth"] or 0) for x in nodes]
    assert keys == sorted(keys)
    assert not any(x["id"].startswith("tests/") for x in nodes)
    assert r["summary"]["reading_order"] == {"modules": len(mods), "nodes": len(nodes)}


def test_entry_depth():
    r = graph()
    by = {n["id"]: n for n in r["nodes"]}
    assert by["tool.py::main"]["entry_depth"] == 0 and by["tool.py::helper"]["entry_depth"] == 1
    assert by["tool.py::unused"]["entry_depth"] is None
    assert by["pkg/core.py::run"]["entry_depth"] == 0                  # 공개 API 이면서
    assert by["pkg/core.py::Engine.start"]["entry_depth"] is None      # Engine 은 클래스 진입점이지만 start 는 호출 간선이 없다
    assert by["pkg/cli.py::main"]["entry"] == ["console_script", "main_call"]
    assert by["api/impl.py::alpha"]["entry_depth"] == 0 and by["api/impl.py::beta"]["entry_depth"] == 1
    assert "entry" not in by["tool.py::helper"] and by["tool.py"]["entry"] == ["main_guard"]
    assert "entry_depth" not in by["tool.py"]                          # 모듈 노드에는 깊이를 적지 않는다
    # 테스트 진입점은 깊이의 출발점이 아니다
    assert by["tests/test_things.py::test_a"]["entry_depth"] is None
    # 진입점이 하나도 없는 프로젝트: 모두 None
    assert all(n["entry_depth"] is None for n in mini()["nodes"] if n["type"] != "module")
    assert mini()["entry_points"] == []


def _hier_edges(r, level):
    return {(e["from"], e["to"]): e for e in r["hierarchy"]["edges"][level]}


def test_hierarchy_edges():
    r = graph()
    h = _hier_edges(r, "function")
    m, g, f = "function:pkg_b/y.py::Child.m", "function:pkg_b/y.py::g", "function:pkg_a/x.py::f"
    assert {k: v["weight"] for k, v in h.items() if "pkg_" in k[0]} == {(m, f): 2, (m, g): 1, (g, f): 1,
                                                                        (g, "function:pkg_b/y.py::Child.m"): 1}
    c = _hier_edges(r, "class")                      # 메서드는 바깥 클래스로 합쳐진다.  g()->Child 는 Child() 호출 + Child.m 호출
    assert c[("class:pkg_b/y.py::Child", "class:pkg_a/x.py::f")]["weight"] == 2
    assert c[("class:pkg_b/y.py::g", "class:pkg_b/y.py::Child")]["weight"] == 2
    assert c[("class:pkg_b/y.py::Child", "class:pkg_a/x.py::Base")] == {
        "from": "class:pkg_b/y.py::Child", "to": "class:pkg_a/x.py::Base", "weight": 0, "by_type": {}, "extends": 1}
    mod = _hier_edges(r, "module")[("module:pkg_b/y.py", "module:pkg_a/x.py")]
    assert (mod["weight"], mod["extends"], mod["import_weight"], mod["by_type"]) == (3, 1, 2, {"calls": 3})
    pk = _hier_edges(r, "package")[("package:pkg_b/", "package:pkg_a/")]
    assert (pk["weight"], pk["extends"], pk["import_weight"]) == (3, 1, 2)
    # 같은 단위 안의 호출은 간선이 아니라 internal_weight
    units = {u["uid"]: u for u in r["hierarchy"]["units"]}
    assert units["module:pkg_b/y.py"]["internal_weight"] == 3 and units["package:pkg_b/"]["internal_weight"] == 3
    assert units["module:pkg_b/y.py"]["out_weight"] == 3 and units["module:pkg_a/x.py"]["in_weight"] == 3
    assert units["package:pkg_b/"]["instability"] == 1.0 and units["package:pkg_a/"]["instability"] == 0.0
    # 상속은 함수 층에 없고, import 는 모듈·폴더 층에만 있다
    assert not any(v.get("extends") or v.get("import_weight") for v in h.values())
    assert not any(v.get("import_weight") for v in _hier_edges(r, "class").values())


def test_hierarchy_units_and_conservation():
    for r in (graph(), mini(), refs()):
        hier = r["hierarchy"]
        units = {u["uid"]: u for u in hier["units"]}
        assert len(units) == len(hier["units"]) and hier["levels"] == ["package", "module", "class", "function"]
        for u in units.values():
            assert u["parent"] is None or u["parent"] in units, u["uid"]
            assert u["uid"] == f"{u['level']}:{u['id']}"
        assert sum(1 for u in units.values() if u["parent"] is None) == 1                   # 뿌리는 하나 (package:./)
        for lv, es in hier["edges"].items():
            for e in es:
                assert e["from"] in units and e["to"] in units and e["from"] != e["to"], e
                assert units[e["from"]]["level"] == units[e["to"]]["level"] == lv
        calls = sum(e.get("count", 1) for e in r["edges"] if e["type"] in ap.CALL_LIKE)
        kind = {n["id"]: n["type"] for n in r["nodes"]}                          # 함수 층에는 클래스 끝점 간선이 없다
        to_class = sum(e.get("count", 1) for e in r["edges"] if e["type"] in ap.CALL_LIKE
                       and "class" in (kind[e["from"]], kind[e["to"]]))
        for lv in ("package", "module", "class", "function"):
            total = sum(e["weight"] for e in hier["edges"][lv]) + sum(
                u["internal_weight"] for u in units.values() if u["level"] == lv)
            assert total == calls - (to_class if lv == "function" else 0), (lv, total, calls, to_class)
        assert sum(u["loc"] for u in units.values() if u["level"] == "module") == sum(
            n["loc"] for n in r["nodes"] if n["type"] == "module")
        assert units["package:./"]["total_loc"] == sum(n["loc"] for n in r["nodes"] if n["type"] == "module")
        assert sum(u["functions"] for u in units.values() if u["level"] == "module") == sum(
            1 for n in r["nodes"] if n["type"] in ("function", "method"))


def test_rollup_packages():
    r = graph()
    whole = ap.rollup_packages(r["hierarchy"], 0)
    assert [u["uid"] for u in whole["units"]] == ["package:./"] and whole["edges"] == []
    units = {u["uid"]: u for u in r["hierarchy"]["units"] if u["level"] == "package"}
    assert whole["units"][0]["internal_weight"] == sum(u["internal_weight"] for u in units.values()) + sum(
        e["weight"] for e in r["hierarchy"]["edges"]["package"])
    one = ap.rollup_packages(r["hierarchy"], 1)
    assert {u["id"] for u in one["units"]} == {u["id"] for u in units.values() if u["id"].count("/") == 1} | {"./"}
    e = next(e for e in one["edges"] if (e["from"], e["to"]) == ("package:pkg_b/", "package:pkg_a/"))
    assert e["weight"] == 3
    src = ap.rollup_packages(layout()["hierarchy"], 1)                    # src/ 레이아웃: 깊이 1 이면 src/ 하나로 합쳐진다
    assert "package:src/" in {u["uid"] for u in src["units"]}


# =====================================================================
# 2-6) 타입·상속 보강
# =====================================================================
def _from(result, prefix, types=("calls", "calls_override", "calls_implicit")):
    """prefix 노드(와 그 안에 중첩된 노드)에서 나가는 간선 -> Counter((대상, 종류, via))  (같은 간선은 count 만큼 센다)"""
    c = Counter()
    for e in result["edges"]:
        if e["type"] in types and (e["from"] == prefix or e["from"].startswith(prefix + ".")):
            c[(e["to"], e["type"], e.get("via"))] += e.get("count", 1)
    return c


def test_c3_mro():
    # 다이아몬드: D(B, C), B(A), C(A) 에서 m 은 A 가 아니라 C 의 것이 먼저다 (깊이 우선이면 A.m 이라는 틀린 답)
    def mro(table, k):
        return ap.class_mro(k[0], k[1], table)
    t = {("m", "D"): [("m", "B"), ("m", "C")], ("m", "B"): [("m", "A")], ("m", "C"): [("m", "A")]}
    assert mro(t, ("m", "D")) == [("m", "D"), ("m", "B"), ("m", "C"), ("m", "A")]
    assert mro({("m", "A"): []}, ("m", "A")) == [("m", "A")]
    bad = {("m", "X"): [("m", "A"), ("m", "B")], ("m", "B"): [("m", "A")]}          # 파이썬도 TypeError
    assert mro(bad, ("m", "X")) == [("m", "X"), ("m", "A"), ("m", "B")]           # 깊이 우선으로 대신함
    cyc = {("m", "P"): [("m", "Q")], ("m", "Q"): [("m", "P")]}
    assert mro(cyc, ("m", "P"))[0] == ("m", "P")                                  # 순환 상속에도 죽지 않는다
    r = proto()
    assert _from(r, "mro.py::use") == Counter({("mro.py::C.m", "calls", "type_hint"): 1,
                                               ("mro.py::E.m", "calls", "type_hint"): 1})
    assert _from(r, "mro.py::E.m") == Counter({("mro.py::C.m", "calls", None): 1})       # super() 는 자기 다음 순서부터
    assert _from(r, "mro.py::Bad.go") == Counter({("mro.py::A.m", "calls", None): 1})    # 모순된 상속도 분석은 계속


def test_operator_and_protocol_edges():
    r = proto()
    impl = lambda fn: _from(r, f"ops.py::{fn}", ("calls_implicit",))
    V = "ops.py::Vec."
    assert impl("binary") == Counter({(V + "__add__", "calls_implicit", "operator"): 1})
    assert impl("reflected") == Counter({(V + "__radd__", "calls_implicit", "operator"): 1})     # 1 + v
    assert impl("inherited") == Counter({(V + "__sub__", "calls_implicit", "operator"): 1})      # 부모의 연산자
    assert impl("compare") == Counter({(V + "__eq__", "calls_implicit", "operator"): 2,          # ==, 그리고 __ne__ 가 없는 !=
                                       (V + "__lt__", "calls_implicit", "operator"): 1})
    assert impl("reflected_compare") == Counter({(V + "__lt__", "calls_implicit", "operator"): 1})   # 3 > v
    assert impl("unary") == Counter({(V + "__neg__", "calls_implicit", "operator"): 1})
    assert impl("aug") == Counter({(V + "__iadd__", "calls_implicit", "operator"): 1,
                                   (V + "__sub__", "calls_implicit", "operator"): 1})            # __isub__ 가 없으면 __sub__
    assert impl("container") == Counter({(V + n, "calls_implicit", "protocol"): c for n, c in (
        ("__getitem__", 1), ("__setitem__", 1), ("__len__", 1), ("__iter__", 1), ("__contains__", 1),
        ("__format__", 1), ("__str__", 2), ("__repr__", 1))})                                   # f"{v}", str(v) + print("a", v), f"{v!r}"
    # 함정: 타입을 모르는 쪽, 연산자가 없는 같은 타입, str 의 % 포맷은 연결하지 않는다
    for fn in ("unknown_left", "same_type_missing", "same_only_r", "untyped", "percent"):   # same_only_r: 같은 타입끼리는 반사 연산자를 쓰지 않는다
        assert not impl(fn), fn
    # obj(...) 는 __call__ 로 연결되는 진짜 호출이고, __call__ 이 없는 객체는 미확정으로 남는다
    assert _from(r, "ops.py::call_it") == Counter({(V + "__call__", "calls", "call_operator"): 1})
    unres = {(u["from"], u["expr"]) for u in r["unresolved"]}
    assert ("ops.py::call_it", "p") in unres and ("ops.py::untyped", "q") in unres
    assert ("ops.py::call_it", "v") not in unres


def test_return_type_inference():
    r = proto()
    go = _from(r, "rets.py::go")
    F, P = "rets.py::Factory.", "rets.py::Product."
    assert go == Counter({
        (F + "make", "calls", "type_hint"): 1, (F + "make", "calls", "return_type"): 4,      # me() / build() / create() / helper() 의 결과
        ("rets.py::SubFactory.make", "calls_override", "type_hint"): 1,                       # f: Factory 는 선언된 타입이라 자식의 오버라이드도 후보
        ("rets.py::SubFactory.make", "calls_override", "return_type"): 2,                     # return self / cls(...) 도 선언된 타입처럼 (자식일 수 있음)
        (P + "run", "calls", "return_type"): 4,                                              # make() / maybe() / helper().make() / chained()
        (F + "me", "calls", "type_hint"): 1, (F + "maybe", "calls", "type_hint"): 1,
        (F + "mixed", "calls", "type_hint"): 1, (F + "local", "calls", "type_hint"): 1,
        (F + "shadowed", "calls", "type_hint"): 1, (F + "build", "calls", None): 1, (F + "create", "calls", None): 1,
        (F + "prop", "calls_implicit", "property"): 1, ("rets.py::helper", "calls", None): 1,
        (F + "am", "calls", "type_hint"): 1, (F + "gen", "calls", "type_hint"): 1,
        ("rets.py::chained", "calls", None): 1}), go
    assert _from(r, "rets.py::chained") == Counter({("rets.py::helper", "calls", None): 1,
                                                    (F + "make", "calls", "return_type"): 1})     # 두 번째 패스에서 helper() 의 타입을 알게 됨
    # 함정: 두 종류를 돌려주는 함수, 지역 변수를 돌려주는 함수, property, 지역 이름이 클래스를 가린 경우는 추론하지 않는다
    unres = {(u["from"], u["expr"]) for u in r["unresolved"]}
    for e in ("f.mixed(1).run", "f.local().run", "f.prop.run", "f.shadowed().run", "f.am().run", "f.gen().run"):
        assert ("rets.py::go", e) in unres, e
    assert r["summary"]["type_inference_return_bodies"] > 0


def test_dynamic_calls():
    r = proto()
    got = [(d["from"], d["kind"], d["line"]) for d in r["dynamic_calls"]]
    assert got == [("dyn.py", "namespace_lookup", 32), ("dyn.py::Magic", "getattr_hook", 28),
                   ("dyn.py::dyn", "getattr", 10), ("dyn.py::dyn", "setattr", 12), ("dyn.py::dyn", "delattr", 13),
                   ("dyn.py::dyn", "eval", 14), ("dyn.py::dyn", "exec", 15), ("dyn.py::dyn", "import", 17),
                   ("dyn.py::dyn", "import", 18), ("dyn.py::dyn", "namespace_lookup", 19),
                   ("dyn.py::dyn", "namespace_lookup", 20), ("dyn.py::dyn", "type_new", 22),
                   ("dyn.py::dyn", "methodcaller", 23)], got
    by = {n["id"]: n for n in r["nodes"]}
    assert by["dyn.py::dyn"]["dynamic"] == ["delattr", "eval", "exec", "getattr", "import", "methodcaller",
                                            "namespace_lookup", "setattr", "type_new"]
    assert by["dyn.py::Magic"]["dynamic"] == ["getattr_hook"] and by["dyn.py"]["dynamic"] == ["namespace_lookup"]
    assert "dynamic" not in by["ops.py::binary"]
    assert r["summary"]["dynamic_calls"]["import"] == 2
    # 함정: 상수 이름(getattr(o, "x"), __dict__["x"], methodcaller("x"))과 같은 이름의 프로젝트 함수(compile)는 동적 호출이 아니다
    assert not [d for d in r["dynamic_calls"] if d["line"] in (11, 16, 21, 24)]


def test_proto_accounting_and_integrity():
    r = proto()
    s = r["summary"]
    ext = sum(e["count"] for e in r["external_edges"])
    assert s["total_calls"] == sum(1 for e in r["edges"] if e["type"] == "calls") + ext + len(r["unresolved"])
    ids = {n["id"] for n in r["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in r["edges"])
    assert all(d["from"] in ids for d in r["dynamic_calls"])


# =====================================================================
# 2-7) PyCG 비교 도구
# =====================================================================
def test_pycg_name_mapping():
    import compare_pycg as cp
    r = refs()
    by = {n["id"]: n for n in r["nodes"]}
    assert cp.node_dotted(by["app.py"]) == "app" and cp.node_dotted(by["app.py::Resource.__init__"]) == "app.Resource.__init__"
    nested = next(i for i in by if ".<locals>." in i)
    assert ".<locals>." not in cp.node_dotted(by[nested]) and cp.node_dotted(by[nested]).startswith("app.")
    nodes = [{"id": "pkg/__init__.py", "type": "module", "file": "pkg/__init__.py"},
             {"id": "pkg/a.py::f", "type": "function", "file": "pkg/a.py"},
             {"id": "pkg/a.py::Cls.m", "type": "method", "file": "pkg/a.py"},
             {"id": "pkg/b.py::f", "type": "function", "file": "pkg/b.py"},
             {"id": "pkg/c.py::only", "type": "function", "file": "pkg/c.py"},
             {"id": "pkg/sub/a.py::g", "type": "function", "file": "pkg/sub/a.py"}]
    idx = cp.NameIndex(nodes)
    assert cp.node_dotted(nodes[0]) == "pkg" and idx.lookup("pkg.a.Cls.m") == "pkg/a.py::Cls.m"
    assert idx.lookup("c.only") == "pkg/c.py::only"                       # 앞부분(src. 등)이 다르면 꼬리가 유일할 때만 맞춘다
    assert idx.lookup("a.f") == "pkg/a.py::f" and idx.lookup("f") is None  # 꼬리가 둘 이상(pkg.a.f / pkg.b.f)이면 포기
    assert idx.lookup("<builtin>.print") is None and idx.lookup("os.path.join") is None
    assert idx.is_module("pkg") and not idx.is_module("pkg.a.f")


def test_pycg_compare_on_saved_output():
    import compare_pycg as cp
    pycg = json.loads((HERE / "fixtures" / "pycg_mini.json").read_text(encoding="utf-8"))     # 실제 PyCG 0.0.8 출력
    report, _ = cp.run(FIXTURE, pycg=pycg)
    st = report["stats"]
    assert (st["knit_pairs"], st["pycg_pairs"], st["both"], st["only_knit"], st["only_pycg"]) == (11, 9, 8, 3, 1), st
    assert st["agreement_jaccard"] == round(8 / 12, 4)
    assert st["only_knit_by_via"] == {"type_hint": 2, "direct": 1}
    assert st["only_pycg_why"] == {"override_candidate": 1}           # self.run() 이 자식 Child.run 일 수 있다는 KnitCode 의 오버라이드 후보
    assert [(x["from"], x["to"]) for x in report["only_pycg"]] == [("base.py::Base.execute", "models.py::Child.run")]
    both = {(x["from"], x["to"]) for x in report["both"]}
    assert ("api.py::main", "service.py::Service.__init__") in both   # Service() <-> Service.__init__ (constructor 로 맞춤)
    assert ("service.py::Service.work", "models.py::Child.ping") in both
    ok = [x for x in report["only_knit"] if x["to"] == "base.py::Base.__init__"]
    assert ok and ok[0]["source"] == "super().__init__()"               # 표본에 소스 줄이 붙는다
    assert st["pycg_calls_outside_project"] > 0 and st["pycg_module_level_calls_skipped"] == 0
    # 오버라이드 후보를 포함하면 KnitCode 쪽 쌍이 늘고 PyCG 만 찾은 쌍은 사라진다
    inc, _ = cp.run(FIXTURE, pycg=pycg, include_overrides=True)
    assert inc["stats"]["only_pycg"] == 0 and inc["stats"]["knit_pairs"] > st["knit_pairs"]


def test_pycg_compare_reasons_and_errors():
    import compare_pycg as cp
    r = proto()
    nodes = {n["id"]: n for n in r["nodes"]}
    # 가짜 PyCG 결과: dyn 이 Magic 의 존재하지 않는 호출, ops.call_it 이 p(...) 를 부른다고 주장
    kp = cp.knit_pairs(r)
    assert ("ops.py::call_it", "ops.py::Vec.__call__") in kp and ("mro.py::use", "mro.py::C.m") in kp
    fake = {("ops.py::call_it", "ops.py::Plain"), ("mro.py::use", "mro.py::A.m"), ("rets.py::go", "rets.py::Other.run"),
            ("ops.py::binary", "ops.py::Vec.__add__")}
    stats, both, only_k, only_p, why = cp.compare(kp, fake, r)
    assert stats["both"] == 0 and stats["only_pycg"] == 4
    assert why[("mro.py::use", "mro.py::A.m")] == "not_seen"                       # KnitCode 가 그 호출을 어떻게도 못 봄
    assert why[("ops.py::call_it", "ops.py::Plain")] == "not_seen"
    assert why[("ops.py::binary", "ops.py::Vec.__add__")] == "not_seen"             # 연산자 호출은 PyCG 도 모델링하지 않으므로 호출 쌍에 넣지 않는다
    assert why[("rets.py::go", "rets.py::Other.run")] == "unresolved:unknown_receiver"   # f.mixed(1).run() 을 봤지만 확정 못 함
    # 실행할 PyCG 가 없으면 죽지 않고 안내 메시지가 달린 RuntimeError
    try:
        cp.run_pycg(FIXTURE, python=str(HERE / "no_such_python"))
        raise AssertionError("RuntimeError 가 나야 함")
    except RuntimeError as e:
        assert "--pycg-python" in str(e)
    assert cp.spread(list(range(100)), 5) == [0, 20, 40, 60, 80] and cp.spread([3, 1, 2], 5) == [1, 2, 3]


# =====================================================================
# 2) 실제 프로젝트: 기준선보다 나빠지지 않았는지
# =====================================================================
def fingerprint(root):
    h = hashlib.sha1()
    for p in sorted(Path(root).rglob("*.py")):
        if any(part in ap.SKIP_DIRS for part in p.parts):
            continue
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))          # Windows 줄바꿈 차이 무시
    return h.hexdigest()


def measure(root):
    s = ap.analyze(root)[1]["summary"]
    return {"internal_resolved": s["internal_resolved"],
            "unresolved_total": sum(s["unresolved"].values()),
            "project_unresolved": s["unresolved"].get("project_unresolved", 0)}


def _check_real(name):
    root = REAL_PROJECTS[name]
    if sys.version_info < MIN_PYTHON.get(name, (3, 0)):
        _skip(f"{name} 는 파이썬 {'.'.join(map(str, MIN_PYTHON[name]))} 이상으로 만든 기준선이라 건너뜀 (소스에 새 문법이 있어 일부 파일을 못 읽음)")
    if not root.exists():
        _skip(f"{name} 가 없어 건너뜀 (README 의 clone 방법 참고)")
    base = json.loads(BASELINE.read_text(encoding="utf-8")).get(name)
    if base is None:
        _skip(f"{name} 기준선이 없음 (--update-baseline 으로 만들 수 있음)")
    if fingerprint(root) != base["fingerprint"]:
        _skip(f"{name} 가 기준선과 다른 버전이라 건너뜀 (기준 커밋: {base['commit']})")
    now = measure(root)
    assert now["internal_resolved"] >= base["internal_resolved"], \
        f"{name}: 내부 연결이 줄었음 {base['internal_resolved']} -> {now['internal_resolved']}"
    assert now["unresolved_total"] <= base["unresolved_total"], \
        f"{name}: 미확정이 늘었음 {base['unresolved_total']} -> {now['unresolved_total']}"
    assert now["project_unresolved"] <= base["project_unresolved"], \
        f"{name}: 프로젝트 안 이름을 못 찾는 호출이 늘었음"


def test_real_sublist3r():
    _check_real("Sublist3r")


def test_real_requests():
    _check_real("requests")


def test_real_flask():
    _check_real("flask")


def test_real_click():
    _check_real("click")


def test_real_httpx():
    _check_real("httpx")


def test_real_requests_root():
    """저장소 루트를 통째로 분석 (tests/ 가 src/ 의 패키지를 절대 import 로 쓰는 구조)"""
    _check_real("requests_root")


def update_baseline():
    out = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    for name, root in REAL_PROJECTS.items():
        if not root.exists():
            print(f"- {name}: 폴더가 없어 건너뜀")
            continue
        old = out.get(name, {})
        out[name] = {"commit": old.get("commit", "(직접 기록)"), "fingerprint": fingerprint(root), **measure(root)}
        print(f"- {name}: {out[name]}")
    BASELINE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("baseline.json 갱신 완료")


def main():
    if "--update-baseline" in sys.argv:
        update_baseline()
        return 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = skipped = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Skip as e:
            skipped += 1
            print(f"SKIP  {name}: {e}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


def test_class_value_calls_and_typing_aliases():
    """클래스를 값으로 들고 호출하는 경우(cls, 클래스 변수, type[X], 별칭)와 TYPE_CHECKING 안의 타입 표시용 클래스.
    기대값은 fixture 소스의 주석에 적어 둔 대로 손으로 정했다"""
    r = ap.analyze(CLASSVAL)[1]
    got = Counter((e["from"], e["to"], e["type"], e.get("via")) for e in r["edges"]
                  if e["type"] in ("calls", "calls_override", "calls_implicit"))
    M = "models.py::"

    def ctor(frm, cls, init=True):          # Foo() 는 클래스로 가는 calls 와 __init__ 으로 가는 calls_implicit 두 개
        out = [(M + frm, M + cls, "calls", "class_value")]
        if init:
            out.append((M + frm, M + cls + ".__init__", "calls_implicit", "init"))
        return out

    want = []
    want += ctor("Base.make_config", "Config")
    want += ctor("Base.make_session", "Session")
    want += ctor("Base.pick", "Client")
    want += ctor("Base.make_provider", "Provider") + [(M + "Base.make_provider", M + "FastProvider", "calls_override", "class_value")]
    want += ctor("Base.build", "Provider") + [(M + "Base.build", M + "FastProvider", "calls_override", "class_value")]
    for fn in ("create", "clone", "clone2"):                       # 자기 클래스 + 자식 (Base 에는 __init__ 이 없다)
        want += ctor("Base." + fn, "Base", init=False) + [(M + "Base." + fn, M + "Child", "calls_override", "class_value")]
    want += [(M + "Base.uses_cls_method", M + "Base.helper", "calls", "class_value")]
    want += [("alias.py::via_module_alias", M + "Config", "calls", "class_value"),
             ("alias.py::via_module_alias", M + "Config.__init__", "calls_implicit", "init"),
             ("alias.py::via_local_alias", M + "Config", "calls", "class_value"),
             ("alias.py::via_local_alias", M + "Config.__init__", "calls_implicit", "init"),
             ("tpkg/app.py::App.__init__", "tpkg/app.py::Service", "calls", None),
             ("tpkg/use.py::use", "tpkg/app.py::App.run", "calls", "module_var"),            # current: AppProxy -> App
             ("tpkg/use.py::use", "tpkg/app.py::Service.go", "calls", "self_attr"),          # current.service.go()
             ("tpkg/use.py::local_chain", "tpkg/app.py::Service.go", "calls", "local_assign")]
    assert got == Counter(want), (got - Counter(want), Counter(want) - got)
    # 믿을 수 없는 경우는 연결하지 않고 '값으로 담긴 함수 호출'로 남긴다
    assert {(u["from"], u["expr"]) for u in r["unresolved"]} == {
        ("alias.py::shadowed", "Config"), ("alias.py::reassigned", "k"), ("alias.py::two_classes", "k"),
        ("alias.py::shadowed_alias", "k"), ("tpkg/use.py::ambiguous", "both.run")}
    # 타입 표시용 클래스는 노드가 되지 않는다 (실제로 존재하지 않는 코드)
    assert not any(n["id"].endswith("AppProxy") for n in r["nodes"])


def test_generic_containers_with_property_await_truthiness():
    """컨테이너 원소 타입, with-as, 프로퍼티, 삼항식, await, 진리값 검사, @overload, 외부 클래스 값 (기대값은 fixture 주석대로 손으로 정함)"""
    r = ap.analyze(GENERIC)[1]
    got = Counter((e["from"], e["to"], e["type"], e.get("via")) for e in r["edges"]
                  if e["type"] in ("calls", "calls_override", "calls_implicit"))
    M, U = "models.py::", "use.py::"
    run, tag, chk = M + "Item.run", M + "Tag.tag", M + "Tag.check"
    want = Counter([
        (M + "Bag.get", M + "Bag.__getitem__", "calls_implicit", "protocol"),                 # self[k]
        (M + "Box.loop_self", tag, "calls", "local_assign"),                                  # for tg in self.tags
        (M + "Holder.item", M + "Item", "calls", None),
        (M + "Holder.via_self", M + "Holder.item", "calls_implicit", "property"),
        (M + "Holder.via_self", run, "calls", "self_attr"),                                   # self.item.run()
        (M + "Req2.__init__", M + "make_unknown", "calls", None),
        (M + "fetch", M + "Item", "calls", None),
        (U + "awaited", run, "calls", "local_assign"),                                        # await fetch() 만
        (U + "awaited", M + "fetch", "calls", None), (U + "awaited", M + "fetch", "calls", None),
        (U + "bag", M + "Bag.get", "calls", "type_hint"),
        (U + "ctxvar", run, "calls", "return_type"), (U + "ctxvar", run, "calls", "local_assign"),   # ContextVar[Item].get(), walrus
        (U + "dict_get", run, "calls", "return_type"), (U + "dict_get", run, "calls", "container_elem"),
        (U + "dict_values", run, "calls", "local_assign"),
        (U + "items_loop", run, "calls", "local_assign"), (U + "items_loop", tag, "calls", "local_assign"),
        (U + "loop_list", chk, "calls", "local_assign"), (U + "loop_list", M + "DictTag.check", "calls_override", "local_assign"),
        (U + "overloaded", run, "calls", "return_type"), (U + "overloaded", M + "get_item", "calls", None),
        (U + "prop", M + "Holder.item", "calls_implicit", "property"), (U + "prop", run, "calls", "self_attr"),
        (U + "sorted_loop", tag, "calls", "local_assign"),
        (U + "ternary", M + "Item", "calls", None), (U + "ternary", M + "Item", "calls", None),
        (U + "ternary", M + "Item", "calls", None), (U + "ternary", run, "calls", "local_assign"),
        (U + "ternary", M + "Tag", "calls", None),
        (U + "truth", M + "Flag.__bool__", "calls_implicit", "protocol"),
        (U + "truth", M + "Sized.__len__", "calls_implicit", "protocol"),
        (U + "tuple_loop", run, "calls", "local_assign"),
        (U + "with_as", M + "Conn", "calls", None), (U + "with_as", M + "Weird", "calls", None),
        (U + "with_as", M + "Conn.__enter__", "calls_implicit", "context_manager"),
        (U + "with_as", M + "Conn.__exit__", "calls_implicit", "context_manager"),
        (U + "with_as", M + "Weird.__enter__", "calls_implicit", "context_manager"),
        (U + "with_as", M + "Weird.__exit__", "calls_implicit", "context_manager"),
        (U + "with_as", M + "Conn.send", "calls", "local_assign"),                           # __enter__ 가 self 를 돌려줄 때만
    ])
    assert got == want, (got - want, want - got)
    # 믿지 않는 경우는 미확정으로 남는다: await 없는 async 호출, 서로 다른 타입의 삼항식, 프로젝트가 정한 속성
    assert {(u["from"], u["expr"]) for u in r["unresolved"]} == {
        (M + "make_unknown", "something_dynamic"), (U + "awaited", "r2.run"), (U + "ternary", "y.run"),
        (U + "externals", "r2.args.get")}
    ext = {(x["from"], x["detail"], x["category"], x["package"]) for x in r["external_edges"]}
    assert (U + "externals", "r.form.get", "inherited_external", "fakelib") in ext          # 외부 부모가 준 속성
    assert (U + "externals", "signal.send", "third_party", "fakelib") in ext               # 외부 객체의 메서드가 돌려준 값
    assert (U + "externals", "r.__dict__.copy", "builtin", "builtins") in ext              # __dict__ 는 항상 dict
    assert (M + "Site.bind", "self.url_map.bind", "third_party", "fakelib") in ext         # url_map_class = Map (외부 클래스 값)
    assert (U + "with_as", "w.run", "builtin", "builtins") in ext                          # __enter__ 가 int 를 돌려주는 Weird
