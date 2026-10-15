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
REAL_PROJECTS = {"Sublist3r": ROOT / "Sublist3r",
                 "requests": ROOT / "requests" / "src" / "requests",
                 "requests_root": ROOT / "requests"}          # 저장소 루트(tests/ 포함)를 통째로 분석


class Skip(Exception):
    pass


def _skip(msg):
    try:
        import pytest
        pytest.skip(msg)
    except ImportError:
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
]

# 있으면 안 되는 연결: 타입을 증명할 수 없는데 연결해 버리는 실수를 잡는다
FORBIDDEN_CALLERS = [
    ("service.py::Service.reassigned", "models.py::Child.ping"),          # 재대입
    ("service.py::Service.shadowed", "models.py::Child.ping"),            # 중첩 함수 인자와 이름이 겹침
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
    for caller in ("reassigned", "shadowed", "tuple_unpack", "loop_var", "ambiguous_union"):
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
