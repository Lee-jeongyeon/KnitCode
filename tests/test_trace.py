"""재현율 측정 도구(trace_recall.py) 테스트

정답지 tests/fixtures/trace_project 를 실제로 실행해서 기록한 호출과 KnitCode 간선을 비교한 결과가
미리 손으로 정리한 표와 정확히 같은지 확인한다 (찾은 것 / 놓친 것 / 놓친 이유).
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import analyze_project as ap  # noqa: E402
import trace_recall as tr  # noqa: E402

FIX = HERE / "fixtures" / "trace_project"
_cache = {}

# (호출자, 피호출자) -> (결과, 이유 앞부분)       hit = KnitCode 가 이미 알고 있었다 / missed = 놓쳤다
EXPECTED = {
    ("core.py", "core.py::deco"): ("hit", "decorator"),                       # @deco 는 정의 시점에 모듈이 부른다
    ("app.py", "app.py::main"): ("missed", "module_level_call"),
    ("app.py::main", "core.py::Child.__init__"): ("hit", "calls+calls_implicit"),
    ("core.py::Child.__init__", "core.py::helper"): ("hit", "calls"),
    ("core.py::helper", "core.py::inner_calc"): ("hit", "calls"),
    ("app.py::main", "core.py::Base.run"): ("hit", "calls"),
    ("core.py::Base.run", "core.py::Child.step"): ("hit", "calls_override"),
    ("app.py::main", "core.py::via_map"): ("hit", "calls"),
    ("core.py::via_map", "core.py::convert"): ("hit", "references"),
    ("app.py::main", "core.py::via_sorted"): ("hit", "calls"),
    ("core.py::via_sorted", "core.py::keyfn"): ("hit", "references"),
    ("app.py::main", "core.py::Vec.__init__"): ("hit", "calls"),
    ("app.py::main", "core.py::Vec.__add__"): ("hit", "calls_implicit"),
    ("core.py::Vec.__add__", "core.py::Vec.__init__"): ("hit", "calls"),
    ("app.py::main", "core.py::Vec.double"): ("missed", "property_access"),
    ("app.py::main", "core.py::dyn_call"): ("hit", "calls"),
    ("core.py::dyn_call", "core.py::target_a"): ("missed", "dynamic_call:namespace_lookup"),
    ("app.py::main", "core.py::deco.<locals>.wrapper"): ("hit", "decorator_passthrough"),    # 실행은 wrapper 를 거치지만 KnitCode 는 main -> decorated 로 연결해 둠
    ("core.py::decorated", "core.py::helper"): ("hit", "calls"),
    ("app.py::main", "core.py::with_lambda"): ("hit", "calls"),
    ("core.py::with_lambda", "core.py::inner_calc"): ("hit", "calls"),       # lambda 안의 호출은 감싸는 함수의 것
    ("app.py::main", "core.py::gen"): ("hit", "calls"),                      # 제너레이터는 다시 이어 실행될 때마다 센다
    ("core.py::gen", "core.py::helper"): ("hit", "calls"),
}


def run_all():
    if "all" not in _cache:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "dyn.json"
            p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "run", str(FIX), "--cwd", str(FIX), "--script", "app.py", "--out", str(out)],
                               capture_output=True, text=True, timeout=120)
            assert p.returncode == 0, p.stderr
            dyn = json.loads(out.read_text(encoding="utf-8"))
        root, result = ap.analyze(FIX)
        rows, skipped, st = tr.compare(result, dyn, root)
        _cache["all"] = (dyn, result, rows, skipped, st, root)
    return _cache["all"]


def test_dynamic_record_and_comparison_table():
    dyn, result, rows, skipped, st, root = run_all()
    got = {(r["from"], r["to"]): (r["status"], r["reason"]) for r in rows}
    assert set(got) == set(EXPECTED), (set(got) ^ set(EXPECTED))
    for k, (status, why) in EXPECTED.items():
        assert got[k][0] == status and got[k][1].startswith(why), (k, got[k])
    # 실행되지 않은 함수는 기록에도, 비교에도 없다
    assert not any("never_run" in str(r["from"]) + str(r["to"]) for r in rows)
    assert all(e["name"] != "never_run" for e in dyn["entered"]) and any(e["name"] == "helper" for e in dyn["entered"])
    # 클래스 본문·모듈 실행·lambda 호출 자체는 호출 간선이 아니라서 따로 센다
    assert skipped["익명 함수(lambda 등) 실행"] == 1 and skipped["클래스 본문/모듈 실행"] >= 3
    assert skipped["데코레이터 wrapper 가 원본 함수를 부르는 호출"] == 1
    assert skipped["import 시점 모듈 __getattr__ 호출"] >= 1                          # 파이썬 버전에 따라 __path__ 조회까지 합쳐 1~2번


def test_summary_numbers_and_causes():
    dyn, result, rows, skipped, st, root = run_all()
    s = tr.summarize(rows, skipped, result, dyn, root, samples=2)
    assert s["dynamic_pairs"] == 23 and s["hit"] == 20 and s["missed"] == 3
    assert abs(s["recall"] - 20 / 23) < 1e-9
    assert s["function_level"] == {"pairs": 21, "hit": 19, "recall": 19 / 21} and s["module_level"] == {"pairs": 2, "hit": 1}
    assert {k: v["pairs"] for k, v in s["causes"].items()} == {
        "module_level_call": 1, "property_access": 1, "dynamic_call:namespace_lookup": 1}
    smp = s["samples"]["dynamic_call:namespace_lookup"][0]
    assert smp["source"] == "return globals()[name]()" and smp["line"] == 64          # 소스 줄이 붙는다
    assert (s["calls"], s["calls_hit"]) == (29, 26) and abs(s["call_recall"] - 26 / 29) < 1e-9     # 호출 횟수 기준도 따로 계산된다


def test_via_c_and_override_dispatch():
    dyn, *_ = run_all()
    pairs = {(p["caller"]["name"], p["callee"]["name"]): p for p in dyn["pairs"]}
    assert pairs[("via_sorted", "keyfn")]["via"] == "sorted"                    # C 함수(sorted)를 거쳐 불린 것을 알아본다
    assert pairs[("helper", "inner_calc")]["via"] is None
    assert pairs[("__init__", "helper")]["via"] is None                          # 클래스 생성(type 호출)은 직접 호출로 본다


def test_classify_override_dispatch_and_decorator_rules():
    nodes = {"a.py::f": {"id": "a.py::f", "type": "function", "name": "f"},
             "a.py::B.m": {"id": "a.py::B.m", "type": "method", "name": "m", "parent": "a.py::B"},
             "a.py::C.m": {"id": "a.py::C.m", "type": "method", "name": "m", "parent": "a.py::C"}}
    up = lambda c: {"a.py::B"} if c == "a.py::C" else set()  # noqa: E731
    row = {"from": "a.py::f", "to": "a.py::C.m", "lines": [3], "via": []}
    why = tr.classify_miss(row, nodes, {"a.py::f": {"a.py::B.m"}}, up, {}, {})
    assert why.startswith("override_dispatch")
    # 부모에도 연결이 없으면 다른 이유로 떨어진다
    assert tr.classify_miss(row, nodes, {}, up, {}, {}).startswith("no_static_call")
    # C 함수를 거쳤다는 표시가 있으면 그 이유
    assert tr.classify_miss(dict(row, via=["map"]), nodes, {}, up, {}, {}) == "c_callback:map"
    # KnitCode 가 그 줄을 외부로 봤다면 그 판단이 이유
    assert tr.classify_miss(row, nodes, {}, up, {("a.py::f", 3): [("external:builtin", "iter")]}, {}) == "external:builtin"


def test_tracer_records_outside_callers_in_process():
    spec = importlib.util.spec_from_file_location("trace_core_mod", FIX / "core.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["trace_core_mod"] = mod
    t = tr.Tracer(FIX)
    t.start()
    try:
        spec.loader.exec_module(mod)           # 프로젝트 안에서 실행되는 import
        mod.helper(1)                          # 이 테스트 함수(프로젝트 밖)가 부른다
    finally:
        t.stop()
        sys.modules.pop("trace_core_mod", None)
    d = t.dump()
    outer = {(o["callee"]["name"], o["from"]) for o in d["outer"]}
    assert ("helper", "test_tracer_records_outside_callers_in_process") in outer
    assert {(p["caller"]["name"], p["callee"]["name"]) for p in d["pairs"]} >= {("helper", "inner_calc")}
    assert not any(p["caller"]["file"].startswith("..") for p in d["pairs"])         # 프로젝트 밖 파일은 기록하지 않는다


def test_report_cli(tmp_path):
    dyn_json = tmp_path / "dyn.json"
    p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "run", str(FIX), "--cwd", str(FIX), "--script", "app.py", "--out", str(dyn_json)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and dyn_json.exists()
    out_json = tmp_path / "rep.json"
    p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "report", str(FIX), str(dyn_json), "--json", str(out_json)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "재현율" in p.stdout and "87.0%" in p.stdout, p.stdout + p.stderr
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["summary"]["hit"] == 20 and len(rep["edges"]) == 23


def test_decorator_passthrough_rules():
    n = lambda i, t="function", **kw: dict({"id": i, "type": t, "name": i.split("::")[1].split(".")[-1], "decorators": []}, **kw)  # noqa: E731
    nodes = {x["id"]: x for x in [n("a.py::deco"), n("a.py::deco.<locals>.wrapper"), n("a.py::f", decorators=["deco"]),
                                  n("a.py::g"), n("a.py::h", decorators=["other(1)"]), n("a.py::x")]}
    w = "a.py::deco.<locals>.wrapper"
    assert tr.wrapper_of(nodes, w, "a.py::f") and not tr.wrapper_of(nodes, w, "a.py::g") and not tr.wrapper_of(nodes, w, "a.py::h")
    assert not tr.wrapper_of(nodes, "a.py::deco", "a.py::f")                       # 중첩 함수가 아니면 wrapper 가 아니다
    lines = {("a.py::x", 5): {"a.py::f"}, ("a.py::x", 7): {"a.py::g"}}
    assert tr.passthrough(nodes, "a.py::x", w, [5], lines)                          # 그 줄에서 데코레이터 붙은 f 로 연결돼 있다
    assert not tr.passthrough(nodes, "a.py::x", w, [6], lines)                      # 그 줄에 연결이 없다
    assert not tr.passthrough(nodes, "a.py::x", w, [7], lines)                      # 연결은 있지만 데코레이터가 안 붙은 g 다
    assert tr.passthrough(nodes, "a.py::x", w, [7, 5], lines)                        # 여러 줄 중 하나면 된다


def test_precision_cli(tmp_path):
    """precision: 둘 다 실행된 간선은 모두 관찰됨(의심 0), --old 에 같은 분석기를 주면 새 간선이 없다"""
    dyn_json = tmp_path / "dyn.json"
    p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "run", str(FIX), "--cwd", str(FIX), "--script", "app.py", "--out", str(dyn_json)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0
    p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "precision", str(FIX), str(dyn_json), "--list"],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    m = {k: int(v) for k, v in __import__("re").findall(r"'(direct_testable|direct_observed)': (\d+)", p.stdout)}
    # 의심은 데코레이터가 감싼 함수 하나뿐: 실행은 wrapper 를 거치므로 (main -> decorated) 쌍이 직접 관찰되지 않는다
    assert m["direct_testable"] - m["direct_observed"] == 1 and p.stdout.count("의심") == 1, p.stdout
    assert "main -> core.py::decorated" in p.stdout
    p = subprocess.run([sys.executable, str(ROOT / "trace_recall.py"), "precision", str(FIX), str(dyn_json), "--old", str(ROOT / "analyze_project.py")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0 and "새 간선   {}" in p.stdout, p.stdout
