"""KnitCode Flow: an offline, function-centred call graph viewer.

    python flow_viewer.py output            # rebuild output/flow.html
    (main.py writes flow.html automatically)

Instead of drawing the whole graph, the page shows one focus function with
its callers on the left and callees on the right (1-3 steps). Clicking an
edge shows why the analyzer connected the two functions (call sites, rule,
certainty). Explicit calls, constructors, implicit calls and override
candidates are drawn differently and can be toggled.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

TEST_HINTS = ('test', 'tests', 'testing')


def _strip(path: str, repository: str | None) -> str:
    path = (path or '').replace('\\', '/')
    head, _, rest = path.partition('/')
    return rest if repository and head == repository and rest else path


def _is_test(path: str) -> bool:
    parts = path.split('/')
    name = parts[-1]
    return (any(p in TEST_HINTS for p in parts[:-1]) or name.startswith('test_')
            or name.endswith('_test.py') or name == 'conftest.py')


def build_view_data(graph: dict, symbols: dict) -> dict:
    repository = symbols.get('repository') or graph.get('repository')
    nodes, index = [], {}

    def add_node(node_id, kind, name, qualified, file, line, end_line, module):
        if node_id in index:
            return index[node_id]
        index[node_id] = len(nodes)
        path = _strip(file, repository)
        nodes.append({'id': node_id, 'k': kind, 'n': name, 'q': qualified, 'f': path,
                      'l': line, 'e': end_line, 'm': module, 't': _is_test(path)})
        return index[node_id]

    for s in symbols.get('symbols', []):
        add_node(s['id'], s['kind'], s['name'], s.get('qualified_name') or s['name'], s['file'],
                 s.get('line'), s.get('end_line'), s.get('module'))
    for n in graph.get('nodes', []):
        if n['kind'] == 'MODULE':
            add_node(n['id'], 'MODULE', n['name'], '(모듈 최상위)', n['file'], 1, None, n['canonical_name'])

    # (source, target, type) -> call sites. A constructor call is shown as
    # Foo() -> Foo.__init__; the class node is used only without an __init__.
    grouped = defaultdict(list)
    ctor_sites = {(e['source'], (e.get('evidence') or {}).get('call_id'))
                  for e in graph.get('edges', [])
                  if e['type'] == 'CALLS' and (e.get('evidence') or {}).get('rule') == 'constructor_init'}

    def site(ev, **extra):
        return {'line': ev.get('line'), 'col': ev.get('column'), 'src': ev.get('source'),
                'rule': ev.get('rule'), 'cert': ev.get('certainty'), **extra}

    for e in graph.get('edges', []):
        ev = e.get('evidence') or {}
        if e['type'] not in ('CALLS', 'INSTANTIATES') or not ev.get('call_id'):
            continue
        if e['type'] == 'INSTANTIATES':
            if (e['source'], ev.get('call_id')) in ctor_sites:
                continue
            kind = 'ctor'
        else:
            kind = 'ctor' if ev.get('rule') == 'constructor_init' else 'call'
        extra = {}
        if ev.get('external_bases_checked'):
            extra['ext'] = ev['external_bases_checked'].get('bases')
        grouped[(e['source'], e['target'], kind)].append(site(ev, **extra))
    for e in graph.get('implicit_edges', []):
        ev = e['evidence']
        grouped[(e['source'], e['target'], 'implicit')].append(
            site(ev, ik=ev.get('implicit_kind'), member=ev.get('member')))
    for e in graph.get('override_edges', []):
        ev = e['evidence']
        grouped[(e['source'], e['target'], 'override')].append(
            site(ev, via=ev.get('via_subclass'), ok=ev.get('override_kind'), base=ev.get('base_method_kind')))

    edges = []
    for (source, target, kind), sites in grouped.items():
        if source in index and target in index:
            unique = {(s['line'], s['col'], s.get('ik'), s.get('member')): s for s in sites}
            edges.append([index[source], index[target], kind,
                          sorted(unique.values(), key=lambda s: (s['line'] or 0, s['col'] or 0))])

    # Every call site inside a function, including external/unknown ones.
    sites_by_caller = defaultdict(list)
    for row in graph.get('call_statuses', []):
        caller = row.get('caller_id')
        if caller not in index:
            continue
        hint = row.get('likely_origin') or {}
        sites_by_caller[index[caller]].append([
            row.get('line'), row.get('callee_text'), row['status'], row.get('origin_kind'),
            (row.get('evidence') or {}).get('rule'),
            [index[t] for t in row.get('target_ids', []) if t in index],
            f"{hint['kind']}·{hint['confidence']}" if hint else None])
    for rows in sites_by_caller.values():
        rows.sort(key=lambda r: (r[0] or 0, r[1] or ''))

    sources = {}
    for file, text in (symbols.get('analysis_sources') or {}).items():
        sources[_strip(file, repository)] = text
    status = (graph.get('status_summary') or {}).get('statuses', {})
    return {
        'repository': repository, 'commit': graph.get('commit'),
        'summary': {'files': len(sources), 'calls': graph.get('total_call_sites'),
                    'internal': status.get('RESOLVED_INTERNAL', graph.get('resolved_call_sites')),
                    'external': status.get('KNOWN_NON_INTERNAL'), 'unknown': status.get('UNKNOWN'),
                    'implicit': len(graph.get('implicit_edges', [])),
                    'override': len(graph.get('override_edges', []))},
        'nodes': nodes, 'edges': edges, 'sites': {str(k): v for k, v in sites_by_caller.items()},
        'sources': sources,
    }


def write_flow_html(graph: dict, symbols: dict, dest: str | Path) -> None:
    data = json.dumps(build_view_data(graph, symbols), ensure_ascii=False, separators=(',', ':'))
    # Keep the JSON inert inside <script>: no "</" sequences, no U+2028/9.
    data = data.replace('</', '<\\/').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    title = f"KnitCode Flow · {graph.get('repository') or ''}"
    html = TEMPLATE.replace('__TITLE__', title.replace('<', '&lt;')).replace('__DATA__', data)
    Path(dest).write_text(html, encoding='utf-8')


TEMPLATE = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#1e1f22;--panel:#25262a;--card:#2b2d31;--line:#3a3c42;--text:#e6e6e6;--muted:#9aa0a6;
--accent:#3ddc97;--accent-soft:rgba(61,220,151,.14);--ctor:#6cb6ff;--implicit:#c9a0ff;--override:#ffb454;
--warn:#ffb454;--bad:#ff7b72;--code:#1a1b1e;--hl:rgba(61,220,151,.16);
font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
@media (prefers-color-scheme:light){:root{--bg:#f6f7f9;--panel:#fff;--card:#fff;--line:#d9dce1;--text:#1f2328;
--muted:#5f6670;--accent:#0f9d63;--accent-soft:rgba(15,157,99,.1);--ctor:#1f6feb;--implicit:#8250df;
--override:#b26b00;--warn:#b26b00;--bad:#cf222e;--code:#f6f8fa;--hl:rgba(15,157,99,.14)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-size:14px;height:100vh;display:flex;flex-direction:column}
button{font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer}
button:hover:not(:disabled){border-color:var(--accent)}
button:disabled{opacity:.45;cursor:default}
code,.mono{font-family:ui-monospace,"Cascadia Code","D2Coding",Consolas,monospace}
header{display:flex;align-items:baseline;gap:16px;padding:16px 24px 10px;border-bottom:1px solid var(--line);flex-wrap:wrap}
header h1{margin:0;font-size:22px;color:var(--accent)} header h1 span{color:var(--text);font-weight:500}
.meta{color:var(--muted);font-size:13px}
.legend{margin-left:auto;display:flex;gap:12px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.legend label{display:flex;align-items:center;gap:6px;cursor:pointer}
.swatch{width:26px;height:0;border-top:2px solid}
main{flex:1;display:grid;grid-template-columns:250px minmax(0,1fr) 350px;min-height:0}
aside,section{min-height:0;overflow:auto}
#side{border-right:1px solid var(--line);padding:14px}
#search{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
.file{margin-top:10px} .file>div{cursor:pointer;color:var(--accent);padding:3px 2px;word-break:break-all}
.file ul{list-style:none;margin:2px 0 6px;padding-left:12px}
.file li{padding:4px 8px;border-radius:6px;cursor:pointer;color:var(--text);font-size:13px;word-break:break-all}
.file li:hover{background:var(--card)} .file li.on{background:var(--accent-soft);outline:1px solid var(--accent)}
.file li .cls{color:var(--muted)}
#center{display:flex;flex-direction:column;overflow:auto}
.focus-head{padding:18px 24px 12px;border-bottom:1px solid var(--line)}
.eyebrow{letter-spacing:.18em;font-size:11px;color:var(--accent)}
.focus-head h2{margin:6px 0 4px;font-size:26px;word-break:break-all}
.toolbar{display:flex;gap:8px;align-items:center;padding:12px 24px;flex-wrap:wrap}
.toolbar .hint{color:var(--muted);font-size:12px}
#stage{position:relative;padding:8px 24px 24px;overflow-x:auto;overflow-y:visible;flex:none}
#cols{display:flex;gap:44px;align-items:flex-start;position:relative;z-index:1;width:max-content;min-width:100%}
.col{width:210px;flex:none}
.col h3{font-size:12px;font-weight:600;color:var(--accent);margin:0 0 10px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.col.focus h3{color:var(--text)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:12px;cursor:pointer}
.card:hover{border-color:var(--accent)} .card.focus{border:2px solid var(--accent);cursor:default}
.card .k{font-size:10px;letter-spacing:.08em;color:var(--muted)}
.card .n{font-weight:600;margin:3px 0;word-break:break-all}
.card.focus .n{color:var(--accent)}
.card .f{font-size:11.5px;color:var(--muted);word-break:break-all}
.card .row{display:flex;gap:6px;margin-top:8px} .card .row button{padding:3px 9px;font-size:12px}
.tag{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:999px;border:1px solid var(--line);color:var(--muted);margin-left:4px}
.more{width:100%;font-size:12px;color:var(--muted)}
.empty{color:var(--muted);font-size:12px;padding:6px 0}
#wires{position:absolute;left:0;top:0;z-index:2;pointer-events:none;overflow:visible}
#wires path.hit{stroke:transparent;stroke-width:14;fill:none;pointer-events:stroke;cursor:pointer}
#wires path.vis{fill:none;stroke-width:1.8;pointer-events:none;opacity:.85}
#wires g:hover path.vis,#wires g.on path.vis{stroke-width:3.2;opacity:1}
.t-call{stroke:var(--accent)} .t-ctor{stroke:var(--ctor)} .t-implicit{stroke:var(--implicit);stroke-dasharray:6 4}
.t-override{stroke:var(--override);stroke-dasharray:2 4}
#evidence{border-top:1px solid var(--line);padding:16px 24px 28px}
#evidence h3,#panel h3{margin:0 0 10px;font-size:15px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}
.chip{font-size:11px;padding:1px 7px;border-radius:999px;white-space:nowrap}
.s-RESOLVED_INTERNAL{background:var(--accent-soft);color:var(--accent)}
.s-KNOWN_NON_INTERNAL{background:rgba(108,182,255,.14);color:var(--ctor)}
.s-UNKNOWN{background:rgba(255,180,84,.14);color:var(--warn)}
a.jump{color:var(--accent);cursor:pointer;text-decoration:none} a.jump:hover{text-decoration:underline}
#panel{border-left:1px solid var(--line);padding:18px;background:var(--panel)}
.kv{display:grid;grid-template-columns:90px 1fr;gap:6px 10px;font-size:13px;margin-bottom:14px}
.kv div:nth-child(odd){color:var(--muted)}
.site{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin-bottom:8px;background:var(--card)}
.site code{display:block;white-space:pre-wrap;word-break:break-all;font-size:12px;margin:4px 0}
.site .why{font-size:11.5px;color:var(--muted)}
pre.codeview{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:8px 0;font-size:12px;overflow:auto;max-height:52vh;margin:0}
pre.codeview div{padding:0 10px;white-space:pre} pre.codeview div.h{background:var(--hl)}
pre.codeview span.ln{display:inline-block;width:42px;color:var(--muted);user-select:none}
.placeholder{margin-top:18px;padding:10px;border:1px dashed var(--line);border-radius:8px;color:var(--muted);font-size:12px}
@media (max-width:1100px){main{grid-template-columns:220px minmax(0,1fr)} #panel{grid-column:1/-1;border-left:0;border-top:1px solid var(--line)}}
@media (max-width:700px){main{display:block;overflow:auto} #side{border-right:0;max-height:40vh}}
</style>
</head>
<body>
<header>
  <h1>KnitCode <span>Flow</span></h1>
  <div class="meta" id="meta"></div>
  <div class="legend" id="legend"></div>
</header>
<main>
  <aside id="side"><input id="search" placeholder="파일·함수 검색" autocomplete="off"><div id="tree"></div></aside>
  <section id="center">
    <div class="focus-head"><div class="eyebrow">CURRENT FOCUS</div><h2 id="focusName"></h2><div class="meta" id="focusMeta"></div></div>
    <div class="toolbar">
      <button id="back">← 이전 선택</button>
      <button id="less">− 단계</button><b id="depth"></b><button id="more">+ 단계</button>
      <label class="hint"><input type="checkbox" id="hideTests" checked> 테스트 코드 숨기기</label>
      <span class="hint" id="hidden"></span>
      <span class="hint">호출자는 왼쪽 · 호출 대상은 오른쪽 · 선을 누르면 근거</span>
    </div>
    <div id="stage"><svg id="wires"></svg><div id="cols"></div></div>
    <div id="evidence"></div>
  </section>
  <aside id="panel"></aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById('data').textContent);
const N = D.nodes, E = D.edges;
const TYPES = {call: '명시적 호출', ctor: '생성자', implicit: '암묵 호출', override: '오버라이드 후보'};
const IMPLICIT = {property_get: 'property 읽기', property_set: 'property 쓰기', property_delete: 'property 삭제',
  operator: '연산자', subscript: '인덱싱', context_manager: 'with 문', iteration: '반복', protocol_builtin: '내장 함수'};
const CERT = {definite_within_supported_flow: '흐름상 확정', multiple_possible_targets: '후보 여러 개',
  conditional_may_call: '조건부(일부 경로)', declared_parameter_type_may_call: '타입 힌트 기반 후보',
  interprocedural_may_call: '객체 흐름 추론', receiver_hierarchy_may_call: '상속 계층 후보',
  module_initialization_may_execute: '모듈 로드 시 실행', class_customization_may_change: '데코레이터 미확인 클래스',
  annotation_evaluation_version_dependent: '타입 주석 평가', implicit_may_call: '암묵 호출(객체 흐름)',
  subclass_override_may_call: '런타임 타입이 자식 클래스일 때만'};
const STATUS = {RESOLVED_INTERNAL: '내부 연결', KNOWN_NON_INTERNAL: '외부', UNKNOWN: '미해결'};
const CAP = 8, MAX_DEPTH = 3;
const RANK = {call: 0, ctor: 1, implicit: 2, override: 3};
const outE = N.map(() => []), inE = N.map(() => []);
E.forEach((e, i) => { outE[e[0]].push(i); inE[e[1]].push(i); });
const byId = new Map(N.map((n, i) => [n.id, i]));
const state = {focus: null, depth: 1, history: [], types: {call: true, ctor: true, implicit: true, override: true},
  hideTests: true, expanded: {}, selected: null};
const $ = id => document.getElementById(id);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') el.className = v; else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const label = n => n.k === 'MODULE' ? n.f + ' (모듈)' : n.q;
const where = n => n.f + (n.l ? ':' + n.l : '');

function visible(e) {
  if (!state.types[e[2]]) return false;
  if (state.hideTests) for (const end of [e[0], e[1]]) if (end !== state.focus && N[end].t) return false;
  return true;
}
function levels(dir) {           // dir 'in' = callers, 'out' = callees
  const seen = new Set([state.focus]), out = [];
  let frontier = [state.focus];
  for (let d = 0; d < state.depth; d++) {
    const next = new Map();          // node -> [best edge rank, call sites]
    for (const node of frontier) for (const i of (dir === 'in' ? inE : outE)[node]) {
      const e = E[i]; if (!visible(e)) continue;
      const other = dir === 'in' ? e[0] : e[1];
      if (seen.has(other)) continue;
      const cur = next.get(other) || [9, 0];
      next.set(other, [Math.min(cur[0], RANK[e[2]]), cur[1] + e[3].length]);
    }
    // Certain relations first (explicit, constructor), candidates last;
    // functions before module-level code (e.g. __main__ demo blocks).
    const isModule = i => N[i].k === 'MODULE' ? 1 : 0;
    const ordered = [...next.entries()].sort((a, b) => a[1][0] - b[1][0] || isModule(a[0]) - isModule(b[0]) ||
      b[1][1] - a[1][1] ||
      label(N[a[0]]).localeCompare(label(N[b[0]]))).map(x => x[0]);
    ordered.forEach(x => seen.add(x)); out.push(ordered); frontier = ordered;
    if (!ordered.length) break;
  }
  return out;
}
function hiddenTestCount() {
  if (!state.hideTests) return 0;
  const s = new Set();
  for (const i of [...inE[state.focus], ...outE[state.focus]]) {
    const e = E[i]; const other = e[0] === state.focus ? e[1] : e[0];
    if (state.types[e[2]] && N[other].t) s.add(other);
  }
  return s.size;
}

function card(i, isFocus) {
  const n = N[i];
  const el = h('div', {class: 'card' + (isFocus ? ' focus' : ''), 'data-node': i,
      onclick: isFocus ? null : () => setFocus(i)},
    h('div', {class: 'k'}, n.k, n.t ? h('span', {class: 'tag'}, 'test') : null),
    h('div', {class: 'n'}, n.k === 'MODULE' ? n.f : n.q),
    h('div', {class: 'f mono'}, where(n)),
    h('div', {class: 'row'},
      h('button', {onclick: ev => { ev.stopPropagation(); showCode(i); }}, '코드 ↗'),
      isFocus ? null : h('button', {onclick: ev => { ev.stopPropagation(); setFocus(i); }}, '중심으로')));
  return el;
}
function column(key, title, ids, cls) {
  const col = h('div', {class: 'col ' + (cls || '')}, h('h3', {}, title));
  if (!ids.length) col.append(h('div', {class: 'empty'}, '없음'));
  const show = state.expanded[key] ? ids : ids.slice(0, CAP);
  show.forEach(i => col.append(card(i, false)));
  if (ids.length > show.length)
    col.append(h('button', {class: 'more', onclick: () => { state.expanded[key] = true; render(); }},
      `+${ids.length - show.length}개 더 보기`));
  return col;
}

function render() {
  const f = state.focus, n = N[f];
  $('focusName').textContent = label(n);
  const callers = new Set(), callees = new Set();
  inE[f].forEach(i => visible(E[i]) && callers.add(E[i][0]));
  outE[f].forEach(i => visible(E[i]) && callees.add(E[i][1]));
  $('focusMeta').textContent = `${where(n)} · ${n.k} · 호출자 ${callers.size} · 호출 대상 ${callees.size}`;
  $('depth').textContent = state.depth + '단계';
  $('less').disabled = state.depth <= 1; $('more').disabled = state.depth >= MAX_DEPTH;
  $('back').disabled = !state.history.length;
  const hidden = hiddenTestCount();
  $('hidden').textContent = hidden ? `테스트 쪽 ${hidden}개 숨김` : '';
  const left = levels('in'), right = levels('out');
  const cols = $('cols'); cols.replaceChildren();
  for (let d = state.depth - 1; d >= 0; d--)
    cols.append(column('in' + d, `${d + 1}단계 호출자 →`, left[d] || []));
  const mid = h('div', {class: 'col focus'}, h('h3', {}, '선택한 코드')); mid.append(card(f, true)); cols.append(mid);
  for (let d = 0; d < state.depth; d++)
    cols.append(column('out' + d, `→ ${d + 1}단계 호출 대상`, right[d] || []));
  requestAnimationFrame(drawWires);
  renderEvidence(); renderTree(); syncHash();
  if (!state.selected) showNode(f);
}

function drawWires() {
  const svg = $('wires'), stage = $('stage'), base = stage.getBoundingClientRect();
  svg.replaceChildren();
  svg.setAttribute('width', $('cols').scrollWidth + 48); svg.setAttribute('height', $('cols').scrollHeight + 32);
  const colOf = new Map();                    // node -> [column index, element]
  [...$('cols').children].forEach((col, ci) => col.querySelectorAll('.card').forEach(c => {
    const key = +c.dataset.node; if (!colOf.has(key)) colOf.set(key, []); colOf.get(key).push([ci, c]); }));
  const pairs = new Map();
  E.forEach((e, i) => {
    if (!visible(e) || !colOf.has(e[0]) || !colOf.has(e[1])) return;
    for (const [ca, a] of colOf.get(e[0])) for (const [cb, b] of colOf.get(e[1])) {
      if (cb !== ca + 1) continue;
      const key = ca + ':' + e[0] + '>' + e[1];
      const p = pairs.get(key) || {a, b, src: e[0], dst: e[1], edges: []};
      p.edges.push(i); pairs.set(key, p);
    }
  });
  for (const p of pairs.values()) {
    const ra = p.a.getBoundingClientRect(), rb = p.b.getBoundingClientRect();
    const x1 = ra.right - base.left + stage.scrollLeft, y1 = ra.top + ra.height / 2 - base.top + stage.scrollTop;
    const x2 = rb.left - base.left + stage.scrollLeft, y2 = rb.top + rb.height / 2 - base.top + stage.scrollTop;
    const dx = Math.max(24, (x2 - x1) / 2);
    const d = `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 6},${y2}`;
    const type = p.edges.map(i => E[i][2]).sort((a, b) => RANK[a] - RANK[b])[0];
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    const sel = state.selected && state.selected.src === p.src && state.selected.dst === p.dst;
    if (sel) g.setAttribute('class', 'on');
    for (const cls of ['vis t-' + type, 'hit']) {
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', d); path.setAttribute('class', cls);
      if (cls.startsWith('vis')) path.setAttribute('marker-end', 'url(#arrow-' + type + ')');
      g.append(path);
    }
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    title.textContent = `${label(N[p.src])} → ${label(N[p.dst])} (${p.edges.map(i => TYPES[E[i][2]]).join(', ')})`;
    g.append(title);
    g.addEventListener('click', () => showEdge(p.src, p.dst));
    svg.append(g);
  }
  const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  for (const t of Object.keys(TYPES)) {
    const m = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    Object.entries({id: 'arrow-' + t, viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 9, markerHeight: 9,
      markerUnits: 'userSpaceOnUse', orient: 'auto'})
      .forEach(([k, v]) => m.setAttribute(k, v));
    const tip = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    tip.setAttribute('d', 'M0,0 L10,5 L0,10 z'); tip.setAttribute('class', 't-' + t);
    tip.style.fill = getComputedStyle(document.documentElement).getPropertyValue('--' + (t === 'call' ? 'accent' : t));
    tip.style.strokeDasharray = 'none'; m.append(tip); defs.append(m);
  }
  svg.prepend(defs);
}

function jump(i, text) { return h('a', {class: 'jump', onclick: () => setFocus(i)}, text || label(N[i])); }
function renderEvidence() {
  const rows = D.sites[state.focus] || [];
  const box = $('evidence'); box.replaceChildren(h('h3', {}, `선택한 코드 안의 호출 ${rows.length}개`));
  if (!rows.length) { box.append(h('div', {class: 'empty'}, '이 코드 안에는 호출문이 없습니다.')); return; }
  const tbody = h('tbody');
  for (const [line, callee, status, origin, rule, targets, hint] of rows) {
    const what = targets.length ? targets.map((t, k) => [k ? ', ' : '', jump(t)]) :
      status === 'KNOWN_NON_INTERNAL' ? origin : (hint ? '추정: ' + hint : '근거 없음');
    tbody.append(h('tr', {},
      h('td', {class: 'mono'}, h('a', {class: 'jump', onclick: () => showCode(state.focus, line)}, line)),
      h('td', {class: 'mono'}, callee || '(식)'),
      h('td', {}, h('span', {class: 'chip s-' + status}, STATUS[status] || status)),
      h('td', {}, what), h('td', {class: 'mono meta'}, rule || '')));
  }
  box.append(h('table', {}, h('thead', {}, h('tr', {}, ['줄', '호출', '상태', '대상 / 출처', '규칙'].map(t => h('th', {}, t)))), tbody));
}

function codeLines(file, from, to, mark) {
  const text = D.sources[file]; if (text === undefined) return h('div', {class: 'empty'}, '소스가 결과에 포함되지 않았습니다.');
  const lines = text.split(/\r\n|\r|\n/); from = Math.max(1, from); to = Math.min(lines.length, to);
  const pre = h('pre', {class: 'codeview mono'});
  for (let k = from; k <= to; k++)
    pre.append(h('div', {class: mark && mark(k) ? 'h' : null, 'data-line': k}, h('span', {class: 'ln'}, k), lines[k - 1]));
  return pre;
}
function showNode(i) {
  const n = N[i];
  $('panel').replaceChildren(h('h3', {}, '선택한 코드'),
    h('div', {class: 'kv'}, h('div', {}, '이름'), h('div', {class: 'mono'}, label(n)), h('div', {}, '종류'), h('div', {}, n.k),
      h('div', {}, '위치'), h('div', {class: 'mono'}, where(n)), h('div', {}, '모듈'), h('div', {class: 'mono'}, n.m || '')),
    n.k === 'MODULE' ? h('div', {class: 'empty'}, '모듈 최상위에서 실행되는 호출의 출발점입니다.')
      : codeLines(n.f, n.l || 1, Math.min((n.e || n.l || 1), (n.l || 1) + 80)),
    placeholder());
}
function showCode(i, line) {
  const n = N[i]; state.selected = null;
  const start = line ? Math.max(1, line - 6) : (n.l || 1), end = line ? line + 10 : Math.min(n.e || start + 40, start + 120);
  $('panel').replaceChildren(h('h3', {}, '코드'), h('div', {class: 'kv'}, h('div', {}, '위치'), h('div', {class: 'mono'}, n.f + ':' + (line || n.l))),
    codeLines(n.f, start, end, line ? k => k === line : k => k >= (n.l || 0) && k <= (n.e || 0)));
  requestAnimationFrame(drawWires);
}
function showEdge(src, dst) {
  state.selected = {src, dst};
  const list = E.filter(e => ((e[0] === src && e[1] === dst) || (e[0] === dst && e[1] === src)) && state.types[e[2]]);
  const panel = $('panel');
  panel.replaceChildren(h('h3', {}, '호출 관계의 근거'),
    h('div', {class: 'kv'}, h('div', {}, '호출하는 쪽'), h('div', {}, jump(src)), h('div', {}, '호출되는 쪽'), h('div', {}, jump(dst))));
  for (const e of list) {
    panel.append(h('div', {class: 'meta', style: 'margin:10px 0 6px'},
      `${TYPES[e[2]]} · ${label(N[e[0]])} → ${label(N[e[1]])} · 호출 위치 ${e[3].length}곳`));
    for (const s of e[3]) {
      const why = [CERT[s.cert] || (s.cert ? s.cert : '소스 근거'), s.ik ? IMPLICIT[s.ik] + ' (' + s.member + ')' : null,
        s.via ? `${s.via}에서 재정의 · ${s.ok === 'extends' ? 'super() 호출로 확장' : '대체'}` : null,
        s.ext ? '외부 부모 확인: ' + s.ext.join(', ') : null].filter(Boolean).join(' · ');
      panel.append(h('div', {class: 'site'},
        h('a', {class: 'jump mono', onclick: () => showCode(e[0], s.line)}, `${N[e[0]].f}:${s.line}`),
        h('code', {}, s.src || ''), h('div', {class: 'why'}, why), h('div', {class: 'why mono'}, s.rule || '')));
    }
  }
  panel.append(placeholder());
  drawWires();
}
function placeholder() { return h('div', {class: 'placeholder'}, 'Git 이력 · 함께 변경된 파일: 추후 연결 예정'); }

let treeQuery = '';
function renderTree() {
  const q = treeQuery.trim().toLowerCase(), tree = $('tree'); tree.replaceChildren();
  const files = new Map();
  N.forEach((n, i) => {
    if (n.k === 'CLASS') return;
    if (q && !(label(n).toLowerCase().includes(q) || n.f.toLowerCase().includes(q))) return;
    if (!files.has(n.f)) files.set(n.f, []); files.get(n.f).push(i);
  });
  const focusFile = N[state.focus].f;
  for (const [file, ids] of [...files.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
    const open = q || file === focusFile;
    const ul = h('ul', {}, ids.sort((a, b) => (N[a].l || 0) - (N[b].l || 0)).map(i => {
      const n = N[i], parts = n.k === 'MODULE' ? ['(모듈 최상위)'] : n.q.split('.');
      return h('li', {class: i === state.focus ? 'on' : null, onclick: () => setFocus(i)},
        parts.length > 1 ? h('span', {class: 'cls'}, parts.slice(0, -1).join('.') + '.') : null, parts[parts.length - 1]);
    }));
    const box = h('div', {class: 'file'}, h('div', {onclick: () => { ul.hidden = !ul.hidden; }}, (open ? '▾ ' : '▸ ') + file), ul);
    ul.hidden = !open; tree.append(box);
  }
}

function setFocus(i, push = true) {
  if (i === state.focus) return;
  if (push && state.focus !== null) state.history.push(state.focus);
  state.focus = i; state.expanded = {}; state.selected = null; render();
  const on = document.querySelector('.file li.on'); if (on) on.scrollIntoView({block: 'nearest'});
}
function syncHash() { history.replaceState(null, '', '#' + encodeURIComponent(N[state.focus].id)); }

function init() {
  const s = D.summary;
  $('meta').textContent = `${D.repository || ''}${D.commit ? ' @ ' + D.commit.slice(0, 10) : ''} · Python 파일 ${s.files} · 호출 ${s.calls} ` +
    `(내부 ${s.internal} · 외부 ${s.external} · 미해결 ${s.unknown}) · 암묵 ${s.implicit} · 오버라이드 후보 ${s.override} · 정적 분석`;
  const legend = $('legend');
  for (const [t, name] of Object.entries(TYPES)) {
    const box = h('input', {type: 'checkbox', checked: true, onchange: ev => { state.types[t] = ev.target.checked; render(); }});
    legend.append(h('label', {}, box, h('span', {class: 'swatch t-' + t,
      style: `border-top-style:${t === 'implicit' ? 'dashed' : t === 'override' ? 'dotted' : 'solid'};border-color:var(--${t === 'call' ? 'accent' : t})`}), name));
  }
  $('search').addEventListener('input', ev => { treeQuery = ev.target.value; renderTree(); });
  $('back').onclick = () => { if (state.history.length) setFocus(state.history.pop(), false); };
  $('less').onclick = () => { state.depth--; render(); };
  $('more').onclick = () => { state.depth++; render(); };
  $('hideTests').onchange = ev => { state.hideTests = ev.target.checked; render(); };
  window.addEventListener('resize', () => requestAnimationFrame(drawWires));
  $('stage').addEventListener('scroll', () => requestAnimationFrame(drawWires));
  const fromHash = byId.get(decodeURIComponent(location.hash.slice(1)));
  let start = fromHash;
  if (start === undefined) {               // busiest non-test function
    let best = -1;
    N.forEach((n, i) => { if (n.t || n.k === 'CLASS' || n.k === 'MODULE') return;
      const score = inE[i].length + outE[i].length; if (score > best) { best = score; start = i; } });
  }
  if (start === undefined) { $('focusName').textContent = '표시할 함수가 없습니다.'; return; }
  setFocus(start, false);
}
init();
</script>
</body>
</html>
'''


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'output')
    graph = json.loads((out / 'graph_result.json').read_text(encoding='utf-8'))
    symbols = json.loads((out / 'symbol_result.json').read_text(encoding='utf-8'))
    write_flow_html(graph, symbols, out / 'flow.html')
    print(f'Flow viewer: {(out / "flow.html").resolve()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
