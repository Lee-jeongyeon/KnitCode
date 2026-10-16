"""KnitCode 뷰어(knit_viewer.py + viewer_template.html) 테스트

  1) 데이터: 분석 결과가 빠짐없이, 안전하게(</script> 이스케이프) HTML 에 들어가는지
  2) 화면: 실제 브라우저(playwright)로 열어 두 화면이 그려지고 오류가 없는지 (playwright/chromium 이 없으면 건너뜀)
"""
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import analyze_project as ap  # noqa: E402
import knit_viewer as kv  # noqa: E402

GRAPH = HERE / "fixtures" / "graph_project"
PROTO = HERE / "fixtures" / "proto_project"
_cache = {}


def result(path):
    if path not in _cache:
        _cache[path] = ap.analyze(path)
    return _cache[path]


def extract(html):
    m = re.search(r'<script id="knit-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "데이터 블록이 없다"
    return json.loads(m.group(1))


def test_data_roundtrip_and_integrity():
    root, r = result(GRAPH)
    data = kv.slim(r, root, True, 40, project="graph_project")
    html = kv.build_html(data)
    back = extract(html)
    assert back == json.loads(json.dumps(data))                              # 넣은 것이 그대로 나온다
    ids = {n["id"] for n in back["nodes"]}
    assert len(ids) == len(r["nodes"])
    assert all(e["from"] in ids and e["to"] in ids for e in back["edges"])
    assert {e["type"] for e in back["edges"]} <= kv.KEEP_EDGES
    assert all(x["id"] in ids for x in back["reading_order"]["nodes"] + back["reading_order"]["modules"])
    assert all(x["id"] in ids for x in back["entry_points"])
    assert {u["level"] for u in back["hierarchy"]["units"]} <= {"package", "module"}
    assert back["hierarchy"]["edges"]["module"] == r["hierarchy"]["edges"]["module"]
    # 호출 간선 개수는 분석 결과와 같다 (뷰어에서 빠진 것이 없다)
    for t in ("calls", "calls_override", "calls_implicit", "references", "extends", "contains"):
        assert sum(e["type"] == t for e in back["edges"]) == sum(e["type"] == t for e in r["edges"]), t
    assert [c["modules"] for c in back["import_cycles"]] == [c["modules"] for c in r["import_cycles"]]


def test_source_snippets():
    root, r = result(GRAPH)
    data = kv.slim(r, root, True, 3)
    src = data["source"]
    fn = [n for n in r["nodes"] if n["type"] == "function" and n["loc"] > 3]
    assert fn, "테스트용 긴 함수가 없다"
    for n in fn[:5]:
        first = (root / n["file"]).read_text(encoding="utf-8").splitlines()[n["line"] - 1]
        assert src[n["id"]].splitlines()[0] == first                         # 정확히 그 줄에서 시작
        assert "줄 생략" in src[n["id"]]                                      # 3줄 넘으면 잘렸다고 표시
    assert not any(n["type"] == "module" and n["id"] in src for n in r["nodes"])
    assert "source" not in kv.slim(r, root, False, 3) and "source" not in kv.slim(r, None, True, 3)


def test_embedding_is_safe():
    root, r = result(PROTO)
    data = kv.slim(r, root, False)
    data["nodes"][0]["docstring"] = "끝 </script><script>alert(1)</script>   끝"
    html = kv.build_html(data)
    assert html.count("</script>") == 2 and html.count("<script") == 2          # 데이터 블록 + 코드 블록뿐
    assert " " not in html
    assert extract(html)["nodes"][0]["docstring"].startswith("끝 </script>")    # 값은 그대로 복원


def test_docstring_trimmed_and_generator_cli(tmp_path):
    out = tmp_path / "v.html"
    assert kv.main([str(PROTO), "-o", str(out), "--no-source"]) == 0
    data = extract(out.read_text(encoding="utf-8"))
    assert "source" not in data and data["project"] == "proto_project"
    assert all(len(n.get("docstring", "")) <= 200 for n in data["nodes"])
    # JSON 파일로도 만들 수 있다
    root, r = result(PROTO)
    j = tmp_path / "graph_x.json"
    j.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    out2 = tmp_path / "v2.html"
    assert kv.main([str(j), "--root", str(PROTO), "-o", str(out2)]) == 0
    d2 = extract(out2.read_text(encoding="utf-8"))
    assert d2["project"] == "x" and d2["source"]


def _browser():
    pw = pytest.importorskip("playwright.sync_api")
    ctx = pw.sync_playwright().start()
    try:
        return ctx, ctx.chromium.launch()
    except Exception as e:                                                   # chromium 이 없는 환경
        ctx.stop()
        pytest.skip(f"브라우저를 띄울 수 없다: {e}")


def test_pages_render_in_browser(tmp_path):
    root, r = result(GRAPH)
    out = tmp_path / "v.html"
    out.write_text(kv.build_html(kv.slim(r, root, True, 40, project="graph_project")), encoding="utf-8")
    ctx, b = _browser()
    try:
        pg = b.new_page(viewport={"width": 1400, "height": 850})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: errs.append("PAGEERR " + str(e)))
        pg.goto(out.as_uri())
        pg.wait_for_timeout(600)
        mods = [u for u in r["hierarchy"]["units"] if u["level"] == "module"]
        assert pg.locator("#gNode .node").count() == len(mods)                # 파일마다 노드 하나
        assert pg.locator("#gEdge path").count() > 0
        # 읽는 순서 목록과 번호표
        assert pg.locator("#rlist li").count() == len(r["reading_order"]["modules"])
        assert pg.locator("#gNode .bd").count() == sum(1 for m in r["reading_order"]["modules"] if m["id"] in {u["id"] for u in mods})
        # 다음 버튼 -> 상세 패널에 그 파일이 나온다
        pg.click('.rnav[data-d="1"]')
        assert r["reading_order"]["modules"][0]["id"] in pg.inner_text("#detail")
        # 함수 호출 보기
        pg.click("#tab-calls")
        pg.wait_for_timeout(600)
        want = len(r["reading_order"]["nodes"])
        assert pg.locator("#gNode .node:not(.ghost)").count() == min(15, want)    # 기본 상위 15개
        pg.fill("#k", str(want))
        pg.wait_for_timeout(300)
        assert pg.locator("#gNode .node:not(.ghost)").count() == want
        assert pg.locator("#gNode .bd").count() >= want                         # 번호표
        # 검색 -> 선택 -> 호출하는 곳/불리는 곳이 상세에 나온다
        pg.fill("#q", "main")
        pg.click("#qres li >> nth=0")
        pg.wait_for_timeout(300)
        assert "이 코드가 부르는 곳" in pg.inner_text("#detail") or "안에 있는 것" in pg.inner_text("#detail")
        assert errs == []
    finally:
        b.close()
        ctx.stop()
