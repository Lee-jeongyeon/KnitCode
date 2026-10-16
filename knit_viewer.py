#!/usr/bin/env python3
"""KnitCode 뷰어 생성기 : 분석 결과(JSON)를 파일 하나짜리 HTML 로 만든다.

사용법
    python knit_viewer.py <프로젝트 폴더>                 # 분석 후 바로 HTML 생성 (knit_<이름>.html)
    python knit_viewer.py graph_x.json --root <폴더>      # 이미 만든 JSON 으로 (코드 보기를 쓰려면 --root)
    옵션: -o 출력.html   --no-source (코드 조각을 넣지 않음)   --source-lines 40

만든 HTML 은 인터넷 없이 브라우저에서 바로 열린다 (외부 라이브러리 없음).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "viewer_template.html"
NEEDED_NODE_KEYS = ("id", "type", "file", "line", "end_lineno", "loc", "name", "signature", "docstring",
                    "decorators", "is_async", "visibility", "parent", "complexity", "entry_depth", "importance")
KEEP_EDGES = {"calls", "calls_override", "calls_implicit", "references", "extends", "contains"}


def slim(result, root=None, with_source=True, source_lines=40, project=None):
    """뷰어에 필요한 것만 남긴다 (용량을 줄이고, 긴 docstring 은 자른다)."""
    nodes = []
    for n in result["nodes"]:
        m = {k: n[k] for k in NEEDED_NODE_KEYS if k in n and n[k] is not None}
        if m.get("docstring"):
            m["docstring"] = " ".join(m["docstring"].split())[:200]
        nodes.append(m)
    edges = []
    for e in result["edges"]:
        if e["type"] in KEEP_EDGES:
            edges.append({k: e[k] for k in ("from", "to", "type", "line", "via", "count") if k in e})
    hier = result["hierarchy"]
    data = {
        "schema_version": result.get("schema_version"),
        "project": project,
        "summary": {k: result.get("summary", {}).get(k) for k in ("files", "nodes", "total_calls", "internal_resolved")},
        "nodes": nodes,
        "edges": edges,
        "entry_points": [{k: e.get(k) for k in ("id", "kind", "detail")} for e in result.get("entry_points", [])],
        "import_cycles": [{"id": c["id"], "kind": c["kind"], "modules": c["modules"], "edges": c["edges"]}
                          for c in result.get("import_cycles", [])],
        "dynamic_calls": [{"from": d.get("from"), "line": d.get("line")} for d in result.get("dynamic_calls", [])],
        "reading_order": result["reading_order"],
        "hierarchy": {"units": [u for u in hier["units"] if u["level"] in ("package", "module")],
                      "edges": {k: v for k, v in hier["edges"].items() if k in ("package", "module")}},
    }
    if with_source and root is not None:
        data["source"] = collect_source(nodes, Path(root), source_lines)
    return data


def collect_source(nodes, root, max_lines):
    """함수·메서드·클래스의 첫 max_lines 줄을 뽑는다 (읽을 수 없으면 건너뜀)."""
    cache, out = {}, {}
    for n in nodes:
        if n["type"] not in ("function", "method", "class") or not n.get("file"):
            continue
        f = n["file"]
        if f not in cache:
            try:
                cache[f] = (root / f).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                cache[f] = None
        lines = cache[f]
        if not lines:
            continue
        a, b = n["line"] - 1, n.get("end_lineno") or n["line"]
        chunk = lines[a:b]
        text = "\n".join(chunk[:max_lines])
        if len(chunk) > max_lines:
            text += f"\n... (이하 {len(chunk) - max_lines}줄 생략)"
        out[n["id"]] = text
    return out


def build_html(data):
    template = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")   # '<' 를 모두 이스케이프: </script>, <!-- 로 깨지지 않게
    assert template.count("/*DATA*/") == 1
    return template.replace("/*DATA*/", payload)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KnitCode 분석 결과를 HTML 뷰어로 만든다")
    ap.add_argument("target", help="프로젝트 폴더 또는 graph_*.json")
    ap.add_argument("--root", help="JSON 을 넘겼을 때 소스 폴더 (코드 보기용)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--no-source", action="store_true")
    ap.add_argument("--source-lines", type=int, default=40)
    a = ap.parse_args(argv)

    t = Path(a.target)
    if t.is_dir():
        sys.path.insert(0, str(HERE))
        import analyze_project
        root, result = analyze_project.analyze(t)
        name = root.name
    else:
        result = json.loads(t.read_text(encoding="utf-8"))
        root = Path(a.root).resolve() if a.root else None
        name = t.stem.replace("graph_", "")
    out = Path(a.out) if a.out else Path(f"knit_{name}.html")
    data = slim(result, root, not a.no_source, a.source_lines, project=name)
    out.write_text(build_html(data), encoding="utf-8")
    print(f"{out} 저장 완료 ({out.stat().st_size / 1024:.0f}KB, 노드 {len(data['nodes'])}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
