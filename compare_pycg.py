"""
KnitCode 와 PyCG 의 호출 그래프를 같은 프로젝트에서 비교한다.

사용법
    python compare_pycg.py <프로젝트 폴더>                 # PyCG 를 직접 실행해서 비교 (pip install pycg 필요)
    python compare_pycg.py <프로젝트 폴더> --pycg-json out.json   # 이미 만든 PyCG 결과를 쓰기

무엇을 재는가
    두 도구가 "함수 A 가 함수 B 를 부른다" 고 같이 말한 쌍이 얼마나 되는지 (프로젝트 안 함수끼리만).
    이것은 **정답과의 비교가 아니다.**  둘 다 틀릴 수 있고, 한쪽만 말한 쌍이 틀렸다는 뜻도 아니다.
    그래서 한쪽만 말한 쌍은 소스 줄과 함께 표본으로 뽑아 사람이 확인할 수 있게 하고,
    KnitCode 만 찾은 쌍은 근거(via), PyCG 만 찾은 쌍은 KnitCode 가 그 호출을 어떻게 봤는지(미확정·외부·못 봄)로 나눈다.

맞춰 주는 것
    - PyCG 는 `pkg.mod.Class.method` 같은 점 이름을 쓰고, KnitCode 는 `pkg/mod.py::Class.method` 를 쓴다 -> 점 이름으로 바꿔 맞춘다
    - 중첩 함수 (`outer.<locals>.inner` <-> `outer.inner`)
    - 클래스 호출: PyCG 는 `Class.__init__` 를 부르는 것으로, KnitCode 는 `Class` 를 부르는 것으로 적는다
      -> KnitCode 쪽을 노드의 `constructor` (MRO 로 찾은 실제 __init__) 로 바꿔 맞춘다.
         __init__ 이 아예 없는 클래스를 만드는 호출은 실행되는 함수 본문이 없어 PyCG 가 적지 않으므로 뺀다 (개수는 따로 보고)
    - 모듈 최상위 코드의 호출은 KnitCode 가 모델링하지 않으므로 PyCG 쪽에서 빼고 따로 센다
    - 프로젝트 밖(내장, 표준 라이브러리, 외부 패키지)으로 가는 호출은 양쪽에서 뺀다
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_project as ap  # noqa: E402


# ---------------------------------------------------------------------
# 이름 맞추기
# ---------------------------------------------------------------------
def module_dotted(file):
    parts = file[:-3].split("/") if file.endswith(".py") else file.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def node_dotted(node):
    """KnitCode 노드 -> PyCG 식 점 이름.  `pkg/mod.py::A.m.<locals>.f` -> `pkg.mod.A.m.f`"""
    mod = module_dotted(node["file"])
    if node["type"] == "module":
        return mod
    qual = node["id"].split("::", 1)[1].replace(".<locals>.", ".")
    return f"{mod}.{qual}" if mod else qual


class NameIndex:
    def __init__(self, nodes):
        self.exact = defaultdict(list)
        self.modules = set()
        for n in nodes:
            d = node_dotted(n)
            if n["type"] == "module":
                self.modules.add(d)
            else:
                self.exact[d].append(n["id"])
        self.suffix = defaultdict(set)                      # 앞부분이 다른 경우(src 레이아웃 등)를 위한 꼬리 일치
        for d, ids in self.exact.items():
            parts = d.split(".")
            for i in range(1, len(parts)):
                self.suffix[".".join(parts[i:])].update(ids)

    def lookup(self, name):
        """PyCG 이름 -> 노드 ID (프로젝트 안에서 하나로 정해질 때만)"""
        ids = self.exact.get(name)
        if ids and len(ids) == 1:
            return ids[0]
        if ids:
            return None
        cand = self.suffix.get(name)
        if cand and len(cand) == 1:
            return next(iter(cand))
        return None

    def is_module(self, name):
        if name in self.modules:
            return True
        return any(m.endswith("." + name) for m in self.modules) and name not in self.exact


# ---------------------------------------------------------------------
# 두 도구의 결과를 (호출자, 피호출자) 쌍으로
# ---------------------------------------------------------------------
def knit_pairs(result, include_overrides=False, skipped=None):
    """KnitCode 결과 -> {(호출자, 피호출자): {"vias": {...}, "line": 첫 줄}}.  함수·메서드 사이의 호출만
    skipped: 집합을 넘기면 비교에서 뺀 쌍(__init__ 이 없는 클래스를 만드는 호출)을 담아 준다"""
    nodes = {n["id"]: n for n in result["nodes"]}
    out = {}
    skipped = skipped if skipped is not None else set()
    for e in result["edges"]:
        if e["type"] not in ("calls", "calls_override") or (e["type"] == "calls_override" and not include_overrides):
            continue
        a, b = e["from"], e["to"]
        if nodes[a]["type"] not in ("function", "method") or b not in nodes:
            continue
        if nodes[b]["type"] == "class":                          # Foo() -> 실제로 실행되는 Foo.__init__
            if not nodes[b].get("constructor"):                  # __init__ 이 없으면 실행되는 함수 본문이 없어 PyCG 는 이 호출을 간선으로 적지 않는다
                skipped.add((a, b))
                continue
            b = nodes[b]["constructor"]
        if nodes[b]["type"] == "module":
            continue
        rec = out.setdefault((a, b), {"vias": set(), "line": e.get("line")})
        rec["vias"].add("override" if e["type"] == "calls_override" else (e.get("via") or "direct"))
        rec["line"] = min(rec["line"], e["line"]) if rec["line"] and e.get("line") else rec["line"]
    return out


def pycg_pairs(pycg, index, nodes):
    """PyCG 결과 -> ({(호출자, 피호출자)}, 통계).  양쪽이 모두 프로젝트 함수·메서드·클래스일 때만"""
    pairs, st = set(), Counter()
    for caller, callees in pycg.items():
        cid = index.lookup(caller)
        if cid is None:
            if index.is_module(caller):
                st["module_level_calls"] += len(callees)
            continue
        if nodes[cid]["type"] not in ("function", "method"):
            continue
        for callee in callees:
            tid = index.lookup(callee)
            if tid is None:
                st["outside_project"] += 1
                continue
            if nodes[tid]["type"] == "module":
                continue
            pairs.add((cid, tid))
    st["pycg_functions"] = sum(1 for c in pycg if index.lookup(c) in nodes and nodes[index.lookup(c)]["type"] in ("function", "method"))
    return pairs, st


def compare(kp, pp, result):
    """kp: knit_pairs 결과, pp: PyCG 쌍 집합.  한쪽만 말한 쌍을 원인별로 나눈다"""
    k = set(kp)
    both, only_k, only_p = k & pp, k - pp, pp - k
    union = len(k | pp)
    by_via = Counter()
    for pair in only_k:
        by_via[sorted(kp[pair]["vias"])[0]] += 1
    nodes = {n["id"]: n for n in result["nodes"]}
    unresolved = defaultdict(list)
    for u in result["unresolved"]:
        unresolved[u["from"]].append(u)
    external = defaultdict(list)
    for e in result["external_edges"]:
        external[e["from"]].append(e)
    overrides = {(e["from"], e["to"]) for e in result["edges"] if e["type"] == "calls_override"}
    why = Counter()
    why_of = {}
    for a, b in sorted(only_p):
        nb = nodes[b]
        name = nb["name"] if nb["type"] != "method" or nb["name"] != "__init__" else nodes[nb["parent"]]["name"]
        if (a, b) in overrides:                                   # self.run() 이 자식의 run 일 수 있다는 KnitCode 의 오버라이드 후보 (--overrides 로 포함)
            why["override_candidate"] += 1
            why_of[(a, b)] = "override_candidate"
            continue
        hit = [u for u in unresolved[a] if u["expr"].replace("()", "").split("(")[0].split(".")[-1] == name]
        if hit:
            reason = f"unresolved:{hit[0]['category']}"
        elif any(e["detail"].split(".")[-1] == name for e in external[a]):
            reason = "classified_external"
        else:
            reason = "not_seen"
        why[reason] += 1
        why_of[(a, b)] = reason
    return {
        "knit_pairs": len(k), "pycg_pairs": len(pp), "both": len(both), "only_knit": len(only_k),
        "only_pycg": len(only_p), "agreement_jaccard": round(len(both) / union, 4) if union else None,
        "knit_agrees_with_pycg": round(len(both) / len(k), 4) if k else None,
        "pycg_covered_by_knit": round(len(both) / len(pp), 4) if pp else None,
        "only_knit_by_via": dict(by_via.most_common()), "only_pycg_why": dict(why.most_common()),
    }, both, only_k, only_p, why_of


def source_line(root, nodes, nid, line):
    try:
        text = (Path(root) / nodes[nid]["file"]).read_text(encoding="utf-8").splitlines()[line - 1]
        return text.strip()
    except (OSError, IndexError, TypeError, UnicodeDecodeError):
        return ""


def spread(items, n):
    """정렬된 목록에서 고르게 n 개 (앞쪽에 치우치지 않게)"""
    items = sorted(items)
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# ---------------------------------------------------------------------
# PyCG 실행
# ---------------------------------------------------------------------
def run_pycg(root, python=None, timeout=900, max_iter=None):
    root = Path(root).resolve()
    entries = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if not any(part in ap.SKIP_DIRS for part in p.parts))
    out = Path(tempfile.mkdtemp(prefix="pycg_")) / "pycg.json"
    base = [python or sys.executable, "-m", "pycg", "--package", "."] + (["--max-iter", str(max_iter)] if max_iter else [])
    cmd = base + entries + ["-o", str(out)]
    if sum(len(c) + 1 for c in cmd) > 30000:
        raise RuntimeError("파일이 너무 많아 PyCG 명령줄이 길어집니다. 하위 폴더 하나만 골라 실행하거나 --pycg-json 을 쓰세요")
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
    except OSError as e:
        raise RuntimeError(f"PyCG 를 실행하지 못했습니다 ({e}). --pycg-python 으로 PyCG 가 설치된 파이썬을 지정하세요")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"PyCG 가 {timeout}초 안에 끝나지 않았습니다 (--timeout 으로 늘리거나 --max-iter 로 제한)")
    if proc.returncode != 0 or not out.exists():
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:])
        raise RuntimeError("PyCG 실행 실패: " + tail + "\n  - 설치: pip install pycg  (리눅스는 패키지 폴더 대소문자 때문에 PyCG -> pycg 링크가 필요할 수 있음)")
    return json.loads(out.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
def run(root, pycg=None, include_overrides=False, samples=8, python=None, timeout=900, max_iter=None):
    root = Path(root).resolve()
    _, result = ap.analyze(root)
    if pycg is None:
        pycg = run_pycg(root, python, timeout, max_iter)
    nodes = {n["id"]: n for n in result["nodes"]}
    index = NameIndex(result["nodes"])
    no_init = set()
    kp = knit_pairs(result, include_overrides, no_init)
    pp, pst = pycg_pairs(pycg, index, nodes)
    stats, both, only_k, only_p, why_of = compare(kp, pp, result)
    stats.update({"knit_class_calls_without_init_skipped": len(no_init), "pycg_module_level_calls_skipped": pst["module_level_calls"],
                  "pycg_calls_outside_project": pst["outside_project"],
                  "knit_functions": sum(1 for n in nodes.values() if n["type"] in ("function", "method")),
                  "pycg_functions_matched": pst["pycg_functions"]})

    def fmt(pair, extra):
        a, b = pair
        line = kp.get(pair, {}).get("line")
        return {"from": a, "to": b, "line": line, "source": source_line(root, nodes, a, line) if line else "", **extra}

    report = {"root": str(root), "stats": stats,
              "only_knit": [fmt(p, {"via": sorted(kp[p]["vias"])}) for p in sorted(only_k)],
              "only_pycg": [fmt(p, {"why": why_of[p]}) for p in sorted(only_p)],
              "both": [fmt(p, {"via": sorted(kp[p]["vias"])}) for p in sorted(both)]}
    return report, samples


def print_report(report, samples):
    s = report["stats"]
    pct = lambda x: "-" if x is None else f"{x * 100:.1f}%"
    print(f"\n===== KnitCode vs PyCG : {Path(report['root']).name} =====")
    print(f"함수·메서드 사이의 호출 쌍   KnitCode {s['knit_pairs']}  /  PyCG {s['pycg_pairs']}")
    print(f"  둘 다 찾음 {s['both']}   KnitCode 만 {s['only_knit']}   PyCG 만 {s['only_pycg']}")
    print(f"  일치율(둘 다 / 합집합) {pct(s['agreement_jaccard'])}   "
          f"KnitCode 쌍 중 PyCG 도 찾은 비율 {pct(s['knit_agrees_with_pycg'])}   "
          f"PyCG 쌍 중 KnitCode 도 찾은 비율 {pct(s['pycg_covered_by_knit'])}")
    print("  (정답과의 비교가 아닙니다. 한쪽만 찾은 쌍이 틀렸다는 뜻이 아니므로 아래 표본을 소스와 대조해 보세요)")
    if s["only_knit_by_via"]:
        print("  KnitCode 만 찾은 쌍의 근거: " + ", ".join(f"{k} {v}" for k, v in s["only_knit_by_via"].items()))
    if s["only_pycg_why"]:
        print("  PyCG 만 찾은 쌍을 KnitCode 는: " + ", ".join(f"{k} {v}" for k, v in s["only_pycg_why"].items()))
    print(f"  제외: PyCG 모듈 최상위 호출 {s['pycg_module_level_calls_skipped']}건, 프로젝트 밖으로 가는 PyCG 호출 {s['pycg_calls_outside_project']}건, "
          f"KnitCode 의 __init__ 없는 클래스 생성 호출 {s['knit_class_calls_without_init_skipped']}쌍")
    for title, key in (("KnitCode 만 찾은 쌍 (표본)", "only_knit"), ("PyCG 만 찾은 쌍 (표본)", "only_pycg")):
        rows = spread([(r["from"], r["to"], i) for i, r in enumerate(report[key])], samples)
        if rows:
            print(f"\n[{title}]")
        for a, b, i in rows:
            r = report[key][i]
            tag = ",".join(r["via"]) if "via" in r else r["why"]
            print(f"   {a}  ->  {b}   [{tag}]" + (f"\n        줄 {r['line']}: {r['source']}" if r.get("source") else ""))


def main():
    ap_ = argparse.ArgumentParser(description="KnitCode 와 PyCG 호출 그래프 비교")
    ap_.add_argument("root", help="분석할 프로젝트 폴더")
    ap_.add_argument("--pycg-json", help="이미 만든 PyCG 결과(JSON). 없으면 PyCG 를 직접 실행")
    ap_.add_argument("--pycg-python", help="PyCG 가 설치된 파이썬 실행 파일 (기본: 현재 파이썬)")
    ap_.add_argument("--timeout", type=int, default=900)
    ap_.add_argument("--max-iter", type=int, help="PyCG 반복 횟수 제한")
    ap_.add_argument("--overrides", action="store_true", help="KnitCode 의 오버라이드 후보(calls_override)도 포함")
    ap_.add_argument("--samples", type=int, default=8)
    a = ap_.parse_args()
    pycg = json.loads(Path(a.pycg_json).read_text(encoding="utf-8")) if a.pycg_json else None
    try:
        report, samples = run(a.root, pycg, a.overrides, a.samples, a.pycg_python, a.timeout, a.max_iter)
    except RuntimeError as e:
        print(f"[오류] {e}")
        return 1
    print_report(report, samples)
    out = Path(f"compare_{Path(report['root']).name}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{out} 저장 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
