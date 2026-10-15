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
REAL_PROJECTS = {"Sublist3r": ROOT / "Sublist3r",
                 "requests": ROOT / "requests" / "src" / "requests",
                 "requests_root": ROOT / "requests"}          # 저장소 루트(tests/ 포함)를 통째로 분석


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
    assert r["summary"]["import_aliases"] == {"mypkg": ""}


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
    (f"{U}::traps", "c.prop.json"): "unknown_receiver",       # property
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
    assert r["summary"]["entry_points"]["route"] == 4
    assert not any(e["id"].startswith("dyn/") for e in r["entry_points"])      # __all__ 을 확정 못 하면 추측하지 않는다
    # 분석 폴더가 setup.py 보다 안쪽(graph_project/pkg)이어도 위쪽 폴더의 설정 파일에서 실행 명령을 찾는다
    inner = ap.analyze(GRAPH / "pkg")[1]
    assert {(e["kind"], e["id"]) for e in inner["entry_points"] if e["kind"] == "console_script"} == {
        ("console_script", "cli.py::main"), ("console_script", "cli.py::Runner.go")}


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
