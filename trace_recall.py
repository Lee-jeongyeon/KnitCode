#!/usr/bin/env python3
"""재현율(recall) 측정 : 실제로 실행된 호출을 정답으로 삼아, KnitCode 가 놓친 호출을 센다.

정적 분석은 "코드에 이런 호출이 있다"를 추측하고, 이 도구는 테스트(또는 스크립트)를 실제로 돌리면서
"A 함수가 B 함수를 불렀다"를 기록한다.  기록한 실제 호출을 KnitCode 간선과 맞춰 보면
  - 실제로 일어난 호출 중 몇 %를 KnitCode 가 알고 있는가 (재현율)
  - 놓친 호출은 왜 놓쳤는가 (원인별 집계 + 소스 줄이 붙은 표본)
를 알 수 있다.

사용법 (분석할 프로젝트의 의존 패키지가 설치된 파이썬으로 실행)
    1) 기록:  python trace_recall.py run <분석 루트> [--out dyn.json] [--pythonpath 경로 ...] [--cwd 폴더] -- <pytest 인자>
              python trace_recall.py run <분석 루트> --script app.py -- <스크립트 인자>
    2) 비교:  python trace_recall.py report <분석 루트> dyn.json [--samples 3] [--json report.json]
    예)   python trace_recall.py run requests/src/requests --cwd requests --pythonpath src -- -q tests -k "not Timeout"
          python trace_recall.py report requests/src/requests dyn.json

주의
  - 테스트가 실행하지 않은 코드의 호출은 기록되지 않으므로, 재현율은 "실행된 호출 중에서"의 값입니다 (실행 안 된 코드는 판단 불가).
  - 실행 중에는 기록하느라 느려집니다 (보통 몇 배).  파이썬 3.9 이상에서 동작합니다.
"""
import argparse
import json
import os
import runpy
import sys
import threading
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

_ANON = ("<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>")


# =====================================================================
# 1) 기록
# =====================================================================
class Tracer:
    """sys.setprofile 로 프로젝트 안 코드끼리의 호출을 기록한다 (호출자 코드, 호출 줄, 피호출자 코드)"""

    def __init__(self, root):
        self.root = os.path.realpath(str(root))
        self._ok = {}
        self.pairs = {}                 # (호출자키, 줄, 피호출자키, via) -> 횟수
        self.outer = Counter()          # (피호출자키, 바깥 함수) -> 횟수   프로젝트 밖(테스트·표준 라이브러리)에서 들어온 호출
        self.entered = set()            # 한 번이라도 실행된 프로젝트 함수
        self._cmark = {}                # id(프레임) -> [C 함수 이름, 깊이]   (C 함수를 거쳐서 파이썬 함수가 불리는 경우 표시)

    def _in_project(self, fn):
        ok = self._ok.get(fn)
        if ok is None:
            if not fn or fn.startswith("<"):
                ok = False
            else:
                p = os.path.realpath(fn)
                ok = p.endswith(".py") and (p == self.root or p.startswith(self.root + os.sep))
            self._ok[fn] = ok
        return ok

    def key(self, code):
        return (os.path.relpath(os.path.realpath(code.co_filename), self.root).replace(os.sep, "/"), code.co_name, code.co_firstlineno)

    def profile(self, frame, event, arg):
        if event == "call":
            code = frame.f_code
            if not self._in_project(code.co_filename):
                return
            back = frame.f_back
            ck = self.key(code)
            self.entered.add(ck)
            if back is None:
                return
            bc = back.f_code
            if self._in_project(bc.co_filename):
                mark = self._cmark.get(id(back))
                via = mark[0] if mark else None
                k = (self.key(bc), back.f_lineno, ck, via)
                self.pairs[k] = self.pairs.get(k, 0) + 1
            else:                                      # 프로젝트 밖에서 들어옴: 바깥 함수 이름만 기록
                self.outer[(ck, bc.co_name)] += 1
        elif event == "c_call":
            if self._in_project(frame.f_code.co_filename):
                m = self._cmark.get(id(frame))
                if m:
                    m[1] += 1
                else:
                    self._cmark[id(frame)] = [getattr(arg, "__name__", type(arg).__name__), 1]
        elif event in ("c_return", "c_exception"):
            m = self._cmark.get(id(frame))
            if m:
                m[1] -= 1
                if m[1] <= 0:
                    del self._cmark[id(frame)]

    def start(self):
        threading.setprofile(self.profile)
        sys.setprofile(self.profile)

    def stop(self):
        sys.setprofile(None)
        threading.setprofile(None)

    def dump(self, extra=None):
        def k2d(k):
            return {"file": k[0], "name": k[1], "line": k[2]}
        data = {"root": self.root, "python": sys.version.split()[0],
                "pairs": [{"caller": k2d(c), "line": ln, "callee": k2d(e), "via": via, "count": n}
                          for (c, ln, e, via), n in self.pairs.items()],
                "outer": [{"callee": k2d(e), "from": fn, "count": n} for (e, fn), n in self.outer.items()],
                "entered": [k2d(k) for k in sorted(self.entered)]}
        data.update(extra or {})
        return data


def cmd_run(a, rest):
    root = Path(a.root).resolve()
    if a.cwd:
        os.chdir(a.cwd)
    for p in reversed(a.pythonpath or []):
        sys.path.insert(0, str(Path(p).resolve()))
    sys.path.insert(0, os.getcwd())
    tr = Tracer(root)
    code, err = 0, None
    tr.start()
    try:
        if a.script:
            sys.argv = [a.script] + rest
            runpy.run_path(a.script, run_name="__main__")
        elif a.module:
            sys.argv = [a.module] + rest
            runpy.run_module(a.module, run_name="__main__", alter_sys=True)
        else:
            import pytest
            code = int(pytest.main(rest))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    except BaseException as e:                                # 실행이 도중에 죽어도 그때까지의 기록은 저장
        err = f"{type(e).__name__}: {e}"
    finally:
        tr.stop()
    data = tr.dump({"exit_code": code, "error": err})
    Path(a.out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"\n{a.out} 저장: 실제 호출 {len(data['pairs'])}쌍 (총 {sum(p['count'] for p in data['pairs'])}회), "
          f"실행된 함수 {len(data['entered'])}개, 종료 코드 {code}" + (f", 오류 {err}" if err else ""))
    return 0


# =====================================================================
# 2) 비교
# =====================================================================
class NodeIndex:
    """(파일, 이름, 줄) 로 기록한 실제 코드를 KnitCode 노드 id 로 바꾼다"""

    def __init__(self, nodes):
        self.nodes = {n["id"]: n for n in nodes}
        self.by_file_name = defaultdict(list)
        self.code_nodes = defaultdict(list)           # 파일 -> 함수·메서드 노드 (감싸는 함수 찾기용)
        for n in nodes:
            if n["type"] in ("function", "method", "class"):
                self.by_file_name[(n["file"], n["name"])].append(n)
            if n["type"] in ("function", "method"):
                self.code_nodes[n["file"]].append(n)
        self.module_ids = {n["id"] for n in nodes if n["type"] == "module"}

    def enclosing(self, f, line):
        best = None
        for n in self.code_nodes.get(f, ()):
            if n["line"] <= line <= (n.get("end_lineno") or n["line"]) and (best is None or n["line"] >= best["line"]):
                best = n
        return best

    def lookup(self, key):
        """-> (노드 id 또는 None, 종류)   종류: node / module / classbody / anon / unmapped"""
        f, name, first = key["file"], key["name"], key["line"]
        if name == "<module>":
            return (f, "module") if f in self.module_ids else (None, "unmapped")
        if name in _ANON:
            n = self.enclosing(f, first)
            return (n["id"], "anon") if n else (None, "unmapped")
        cands = [n for n in self.by_file_name.get((f, name), ()) if 0 <= n["line"] - first <= 30]
        if not cands:
            return None, "unmapped"
        n = min(cands, key=lambda c: c["line"] - first)
        return n["id"], ("classbody" if n["type"] == "class" else "node")


def static_pairs(result, lines=None):
    """KnitCode 간선 -> {(호출자, 피호출자): [종류...]}.  클래스 호출은 실제로 실행되는 __init__ 로 바꾼다
    lines: 딕셔너리를 넘기면 {(호출자, 줄): {피호출자...}} 도 채워 준다"""
    nodes = {n["id"]: n for n in result["nodes"]}
    out = defaultdict(set)
    for e in result["edges"]:
        if e["type"] not in ("calls", "calls_override", "calls_implicit", "references"):
            continue
        a, b = e["from"], e["to"]
        if a not in nodes or b not in nodes:
            continue
        if nodes[b]["type"] == "class":
            b = nodes[b].get("constructor") or b
        out[(a, b)].add(e["type"])
        if lines is not None and e.get("line"):
            lines[(a, e["line"])].add(b)
        if e.get("via") == "decorator" and nodes[a].get("parent") in nodes:     # @deco 는 정의 시점에 감싸는 쪽(모듈·클래스)이 부르는 것으로 실행된다
            out[(nodes[a]["parent"], b)].add("decorator(정의 시점)")
    return out


def ancestors(result):
    """클래스 id -> 조상 클래스 id 집합 (extends 간선을 따라)"""
    par = defaultdict(set)
    for e in result["edges"]:
        if e["type"] == "extends":
            par[e["from"]].add(e["to"])
    memo = {}

    def up(c, seen=()):
        if c in memo:
            return memo[c]
        out = set()
        for p in par.get(c, ()):
            if p not in seen:
                out.add(p)
                out |= up(p, seen + (c,))
        memo[c] = out
        return out
    return up


def source_line(root, f, line):
    try:
        return (Path(root) / f).read_text(encoding="utf-8", errors="replace").splitlines()[line - 1].strip()
    except (OSError, IndexError):
        return ""


def compare(result, dyn, root):
    idx = NodeIndex(result["nodes"])
    nodes = idx.nodes
    st_lines = defaultdict(set)
    st = static_pairs(result, st_lines)
    st_from = defaultdict(set)
    for (a, b) in st:
        st_from[a].add(b)
    up = ancestors(result)
    dynmark = {}                                    # (호출자, 줄) -> 동적 호출 종류
    for d in result.get("dynamic_calls", []):
        dynmark[(d["from"], d["line"])] = d["kind"]
    unres = defaultdict(list)                       # (호출자, 줄) -> KnitCode 가 그 줄에서 못 풀었거나 외부로 본 호출
    for u in result.get("unresolved", []):
        unres[(u["from"], u["line"])].append(("unresolved:" + u["category"], u.get("expr", "")))
    for x in result.get("external_edges", []):
        if x.get("line"):
            unres[(x["from"], x["line"])].append(("external:" + x.get("category", "?"), x.get("detail", "")))
    dyn_lines = set()
    for d in dyn["pairs"]:
        dyn_lines.add((d["caller"]["file"], d["line"]))

    skipped = Counter()
    edges = {}                                       # (호출자, 피호출자) -> 정보
    for d in dyn["pairs"]:
        a, ka = idx.lookup(d["caller"])
        b, kb = idx.lookup(d["callee"])
        if ka == "node" and kb == "node" and wrapper_of(nodes, a, b):
            skipped["데코레이터 wrapper 가 원본 함수를 부르는 호출"] += d["count"]      # 호출자 -> wrapper 쪽에서 원본 함수와 맞춘다
            continue
        if kb == "node" and d["callee"]["name"] == "__getattr__" and ka == "module":
            skipped["import 시점 모듈 __getattr__ 호출"] += d["count"]            # from x import y 에서 y 가 아직 없을 때 파이썬이 부르는 것
            continue
        if kb == "anon":
            skipped["익명 함수(lambda 등) 실행"] += d["count"]             # 호출 간선이 아니라 그 안의 호출이 감싸는 함수에 붙는다
            continue
        if ka == "classbody" or kb in ("classbody", "module"):
            skipped["클래스 본문/모듈 실행"] += d["count"]            # 정의 시점에 실행되는 코드 자체는 호출 간선이 아니다
            continue
        if kb == "unmapped" or ka == "unmapped":
            skipped["노드로 수집되지 않은 정의"] += d["count"]
            key = (("?" + d["caller"]["file"] + ":" + d["caller"]["name"]) if ka == "unmapped" else a,
                   ("?" + d["callee"]["file"] + ":" + d["callee"]["name"]) if kb == "unmapped" else b)
            e = edges.setdefault(key, {"count": 0, "lines": set(), "vias": set(), "unmapped": True, "file": d["caller"]["file"]})
        else:
            e = edges.setdefault((a, b), {"count": 0, "lines": set(), "vias": set(), "unmapped": False, "file": d["caller"]["file"]})
        e["count"] += d["count"]
        e["lines"].add(d["line"])
        if d["via"]:
            e["vias"].add(d["via"])

    rows = []
    for (a, b), e in edges.items():
        row = {"from": a, "to": b, "count": e["count"], "lines": sorted(e["lines"]), "via": sorted(e["vias"]), "file": e["file"]}
        row["module_level"] = a in idx.module_ids
        if not e["unmapped"] and (a, b) not in st and ".<locals>." in b and passthrough(nodes, a, b, row["lines"], st_lines):
            row["status"], row["reason"] = "hit", "decorator_passthrough"
        elif e["unmapped"]:
            row["status"], row["reason"] = "missed", "unmapped:정의가 노드로 수집되지 않음"
        elif (a, b) in st:
            row["status"], row["reason"] = "hit", "+".join(sorted(st[(a, b)]))
        else:
            row["status"] = "missed"
            row["reason"] = classify_miss(row, nodes, st_from, up, unres, dynmark)
        rows.append(row)
    return rows, skipped, st


def decorated_by(nodes, fid, outer_name):
    return any(d.split("(")[0].split(".")[-1] == outer_name for d in (nodes[fid].get("decorators") or []))


def wrapper_of(nodes, w, f):
    """w 가 데코레이터 안쪽 함수(wrapper)이고 f 가 그 데코레이터가 붙은 원본 함수인가"""
    if ".<locals>." not in w:
        return False
    outer = nodes.get(w.split(".<locals>.")[0])
    return bool(outer) and decorated_by(nodes, f, outer["name"])


def passthrough(nodes, a, w, lines, st_lines):
    """호출자 a 의 그 줄에서 KnitCode 가 데코레이터가 붙은 원본 함수로 연결해 두었는가 (실행할 때는 wrapper 를 거쳐 갈 뿐)"""
    outer = nodes.get(w.split(".<locals>.")[0])
    if not outer:
        return False
    return any(decorated_by(nodes, f, outer["name"]) for ln in lines for f in st_lines.get((a, ln), ()) if f in nodes)


def classify_miss(row, nodes, st_from, up, unres, dynmark):
    """놓친 호출의 원인을 정한다 (위에서부터 먼저 맞는 것)"""
    a, b = row["from"], row["to"]
    nb = nodes[b]
    # 1) 부모 클래스의 같은 이름 메서드로는 연결돼 있다 (자식 메서드가 실제로 불린 것)
    if nb["type"] == "method":
        cls = nb.get("parent")
        for p in st_from.get(a, ()):
            np_ = nodes.get(p)
            if np_ and np_["type"] == "method" and np_["name"] == nb["name"] and np_.get("parent") in up(cls):
                return "override_dispatch:부모 메서드로만 연결 (실제로는 자식이 실행됨)"
    # 1-2) 데코레이터가 만든 안쪽 함수 (wrapper) 를 거쳐 불렸다
    if ".<locals>." in b:
        outer = b.split(".<locals>.")[0]
        oname = nodes[outer]["name"] if outer in nodes else None
        for p in st_from.get(a, ()):
            if oname and oname in [d.split("(")[0].split(".")[-1] for d in (nodes[p].get("decorators") or [])]:
                return "decorator_wrapper:데코레이터가 만든 안쪽 함수를 거쳐 실행됨"
    # 2) KnitCode 가 이미 "동적 호출" 로 표시한 줄이다
    for ln in row["lines"]:
        if (a, ln) in dynmark:
            return "dynamic_call:" + dynmark[(a, ln)]
    # 2-2) KnitCode 가 그 줄에서 다르게 판단했다
    for ln in row["lines"]:
        hit = unres.get((a, ln))
        if hit:
            return hit[0][0]
    # 2-3) 모듈 최상위에서 실행된 호출 (import 시점).  KnitCode 는 모듈 최상위 호출을 간선으로 만들지 않는다
    if a in nodes and nodes[a]["type"] == "module":
        return "module_level_call:모듈 최상위(import 시점) 호출"
    # 3) C 함수 (map, sorted, partial …) 를 거쳐 불렸다
    if row["via"]:
        return "c_callback:" + row["via"][0]
    # 4) 호출문 없이 실행되는 것
    nm = nb["name"]
    if nm.startswith("__") and nm.endswith("__"):
        return "implicit_dunder:" + nm
    if any(d.split("(")[0] in ("property", "cached_property", "functools.cached_property") or d.endswith(".setter") or d.endswith(".getter")
           for d in (nb.get("decorators") or [])):
        return "property_access"
    return "no_static_call:그 줄에서 호출을 찾지 못함"


def cause_key(reason):
    head = reason.split(":")
    return head[0] + ":" + head[1] if head[0] in ("unresolved", "external", "c_callback", "implicit_dunder", "dynamic_call") else head[0]


def summarize(rows, skipped, result, dyn, root, samples=3):
    total = len(rows)
    hit = [r for r in rows if r["status"] == "hit"]
    miss = [r for r in rows if r["status"] == "missed"]
    calls_total = sum(r["count"] for r in rows)
    calls_hit = sum(r["count"] for r in hit)
    by = defaultdict(list)
    for r in miss:
        by[cause_key(r["reason"])].append(r)
    func_rows = [r for r in rows if not r["module_level"]]
    fh = [r for r in func_rows if r["status"] == "hit"]
    out = {
        "dynamic_pairs": total, "hit": len(hit), "missed": len(miss), "recall": len(hit) / total if total else None,
        "calls": calls_total, "calls_hit": calls_hit, "call_recall": calls_hit / calls_total if calls_total else None,
        "function_level": {"pairs": len(func_rows), "hit": len(fh), "recall": len(fh) / len(func_rows) if func_rows else None},
        "module_level": {"pairs": total - len(func_rows), "hit": len(hit) - len(fh)},
        "skipped_calls": dict(skipped), "executed_functions": len(dyn["entered"]),
        "causes": {k: {"pairs": len(v), "calls": sum(x["count"] for x in v)} for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))},
        "samples": {},
    }
    for k, v in by.items():
        out["samples"][k] = [{"from": x["from"], "to": x["to"], "count": x["count"], "line": x["lines"][0] if x["lines"] else None,
                              "source": source_line(root, x["file"], x["lines"][0]) if x["lines"] else "", "reason": x["reason"], "via": x["via"]}
                             for x in sorted(v, key=lambda x: -x["count"])[:samples]]
    return out


def print_report(name, s):
    pct = lambda x: "-" if x is None else f"{x * 100:.1f}%"
    print(f"\n===== 재현율 : {name} =====")
    print(f"실제로 실행된 호출 쌍 {s['dynamic_pairs']}개 (총 {s['calls']}회)   그중 KnitCode 가 찾은 것 {s['hit']}개")
    print(f"  재현율(쌍 기준) {pct(s['recall'])}   (호출 횟수 기준 {pct(s['call_recall'])})")
    print(f"  함수·메서드 사이만: {s['function_level']['hit']}/{s['function_level']['pairs']} = {pct(s['function_level']['recall'])}"
          f"   모듈 최상위(import 시점) 호출: {s['module_level']['hit']}/{s['module_level']['pairs']}")
    if s["skipped_calls"]:
        print("  제외: " + ", ".join(f"{k} {v}회" for k, v in s["skipped_calls"].items()))
    print(f"  (실행된 프로젝트 함수 {s['executed_functions']}개.  실행되지 않은 코드의 호출은 판단할 수 없습니다)")
    if s["causes"]:
        print("\n[놓친 호출의 원인]")
        for k, v in s["causes"].items():
            print(f"  {k:<44} {v['pairs']:>4}쌍  ({v['calls']}회)")
        print("\n[원인별 표본]")
        for k, items in s["samples"].items():
            print(f"  ## {k}")
            for it in items:
                print(f"     {it['from']}  ->  {it['to']}   ({it['count']}회, 줄 {it['line']})")
                if it["source"]:
                    print(f"         {it['source'][:110]}")


def cmd_report(a):
    sys.path.insert(0, str(HERE))
    import analyze_project
    root = Path(a.root).resolve()
    dyn = json.loads(Path(a.dyn).read_text(encoding="utf-8"))
    _, result = analyze_project.analyze(root)
    rows, skipped, _ = compare(result, dyn, root)
    s = summarize(rows, skipped, result, dyn, root, a.samples)
    print_report(root.name, s)
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": s, "edges": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n{a.json} 저장 완료")
    return 0


def cmd_precision(a):
    """의심 간선 점검: 호출자·피호출자가 모두 실행됐는데 그 쌍이 관찰되지 않은 간선.
    --old 로 이전 분석기를 주면 '새로 생긴 간선'만 따로 센다."""
    import importlib.util
    sys.path.insert(0, str(HERE))
    import analyze_project as new
    old = None
    if a.old:
        sp = importlib.util.spec_from_file_location("old_analyzer", a.old)
        old = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(old)
    root = Path(a.root).resolve()
    dyn = json.loads(Path(a.dyn).read_text(encoding="utf-8"))
    rn = new.analyze(root)[1]
    ro = old.analyze(root)[1] if old else None
    col = lambda x: x.split(".<locals>.")[0]
    idx = NodeIndex(rn["nodes"])
    seen, entered = set(), set()
    for d in dyn["pairs"]:
        x, kx = idx.lookup(d["caller"])
        y, ky = idx.lookup(d["callee"])
        if kx in ("node", "anon") and x:
            entered.add(col(x))
        if ky in ("node", "anon") and y:
            entered.add(col(y))
        if kx in ("node", "anon") and ky == "node" and x and y:
            seen.add((col(x), col(y)))

    def edges(r):
        nodes = {n["id"]: n for n in r["nodes"]}
        out = {}
        for e in r["edges"]:
            if e["type"] not in ("calls", "calls_override", "calls_implicit"):
                continue
            b = e["to"]
            if nodes.get(b, {}).get("type") == "class":
                b = nodes[b].get("constructor") or b
            out[(col(e["from"]), b, e["type"])] = e
        return out

    en = edges(rn)
    eo = edges(ro) if ro else {}

    def stat(keys):
        r = defaultdict(int)
        for (x, y, t) in keys:
            kind = "override" if t == "calls_override" else "direct"
            if x in entered and y in entered:
                r[kind + "_testable"] += 1
                if (x, y) in seen:
                    r[kind + "_observed"] += 1
            else:
                r[kind + "_untested"] += 1
        return dict(r)

    new_only = [k for k in en if k not in eo]
    print(f"[{root.name}]")
    if ro:
        print("  기존 간선", stat([k for k in en if k in eo]))
        print("  새 간선  ", stat(new_only))
    else:
        print("  전체 간선", stat(list(en)))
    if a.list:
        for (x, y, t) in (new_only if ro else en):
            if t != "calls_override" and x in entered and y in entered and (x, y) not in seen:
                e = en[(x, y, t)]
                print(f"  의심 {x} -> {y} {t} {e.get('via')} 줄 {e.get('line')}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="실제 실행 호출로 KnitCode 재현율 측정")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="테스트/스크립트를 실행하며 호출 기록")
    r.add_argument("root", help="분석 루트 (KnitCode 에 넣는 폴더와 같은 것)")
    r.add_argument("--out", default="dyn.json")
    r.add_argument("--cwd")
    r.add_argument("--pythonpath", action="append")
    r.add_argument("--script")
    r.add_argument("--module")
    p = sub.add_parser("report", help="기록한 호출과 KnitCode 간선 비교")
    p.add_argument("root")
    p.add_argument("dyn")
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--json")
    q = sub.add_parser("precision", help="의심 간선(둘 다 실행됐는데 쌍이 관찰되지 않음) 점검")
    q.add_argument("root")
    q.add_argument("dyn")
    q.add_argument("--old", help="이전 분석기 파일 (새로 생긴 간선만 따로 셈)")
    q.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "precision":
        return cmd_precision(a)
    if a.cmd == "run":
        return cmd_run(a, rest)
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
