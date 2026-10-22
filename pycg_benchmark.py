"""PyCG macro-benchmark: KnitCode vs. the hand-made ground truth (and PyCG).

The five projects, their ground-truth call graphs and the scoring functions come
from the PyCG artifact (https://github.com/vitsalis/pycg-evaluation, ICSE'21).
Inputs follow scripts/generate_pycg_cgs.sh exactly: the same files and the same
`--package` root, so module names match the ground truth.

Usage:
    python pycg_benchmark.py --benchmark ../pycg-evaluation --output out/pycg
    python pycg_benchmark.py --benchmark ../pycg-evaluation --pycg-python PY39
    python pycg_benchmark.py --benchmark ../pycg-evaluation --pycg-cgs DIR

Scores (unique caller -> callee pairs, function-level, PyCG notation):
  internal   project functions only (KnitCode CALLS edges)
  full       internal + external calls with static provenance
             (KNOWN_NON_INTERNAL call sites: builtins, stdlib, imports)
precision/recall use the artifact's own measure_precision/measure_recall.
PyCG itself is run with --pycg-python (an interpreter with PyCG installed;
the 2021 artifact version needs Python <= 3.9) or read from --pycg-cgs, and
the overlap of the two tools is reported as well.
No ground-truth-driven renaming, suffix matching or edge deletion is done.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
from main import run  # noqa: E402

PROJECTS = ('Sublist3r', 'asciinema', 'autojump', 'fabric', 'face_classification')
# project -> (--package root, directory whose .py files are analyzed), as in
# generate_pycg_cgs.sh. fabric also drops paths containing "tests"/"setup.py".
LAYOUT = {'autojump': ('bin', 'bin'), 'fabric': ('.', '.'), 'asciinema': ('.', 'asciinema'),
          'face_classification': ('src', 'src'), 'Sublist3r': ('.', '.')}
# PyCG notation for methods of builtin types.
BUILTIN_TYPES = {'str': 'PyStr', 'bytes': 'PyBytes', 'list': 'PyList', 'dict': 'PyDict',
                 'set': 'PySet', 'tuple': 'PyTuple'}
# An external target must be a dotted name. Descriptive origins such as
# "Manager().dict" or "a.join or b.join" name no single function.
DOTTED = re.compile(r'[A-Za-z_]\w*(\.\w+)*')


def select_files(project_dir: Path, project: str) -> tuple[Path, list[Path]]:
    package, scope = LAYOUT[project]
    files = sorted((project_dir / scope).rglob('*.py'))
    if project == 'fabric':
        files = [p for p in files if 'tests' not in p.relative_to(project_dir).as_posix()
                 and 'setup.py' not in p.relative_to(project_dir).as_posix()]
    return (project_dir / package).resolve(), files


def pycg_external(name: str) -> str:
    if name.startswith('builtins.'):
        return '<builtin>.' + name[len('builtins.'):]
    head, dot, rest = name.partition('.')
    if dot and head in BUILTIN_TYPES:
        return f'<**{BUILTIN_TYPES[head]}**>.{rest}'
    return name


def to_pycg(symbols: dict, graph: dict) -> tuple[set, set, dict]:
    """(internal pairs, external pairs, skipped counts) in PyCG's notation."""
    nodes = {n['id']: n for n in graph['nodes']}
    # PyCG numbers lambdas per enclosing scope: scope.<lambda1>, <lambda2>, ...
    lambdas, counters = {}, defaultdict(int)
    for s in sorted((s for s in symbols['symbols'] if s['kind'] == 'LAMBDA'),
                    key=lambda s: (s['file'], s['line'], s.get('column', 0))):
        scope = s['canonical_name'].rsplit('.', 1)[0]
        counters[scope] += 1
        lambdas[s['canonical_name']] = f'{scope}.<lambda{counters[scope]}>'

    def name(node_id):
        canonical = nodes[node_id]['canonical_name']
        return lambdas.get(canonical, canonical)

    # PyCG's nodes are functions: `Cls()` is a call to Cls.__init__ (a CALLS
    # edge here). A class without a project __init__ runs no project function
    # body, so INSTANTIATES edges are not pairs; they are counted instead.
    internal, skipped = set(), {'class_instantiation_without_init': 0, 'non_dotted_external': 0}
    init_sites = {e['evidence'].get('call_id') for e in graph['edges']
                  if e['type'] == 'CALLS' and nodes[e['target']]['name'] == '__init__'}
    for e in graph['edges']:
        if e['type'] == 'INSTANTIATES':
            skipped['class_instantiation_without_init'] += e['evidence'].get('call_id') not in init_sites
        elif e['type'] == 'CALLS':
            internal.add((name(e['source']), name(e['target'])))

    module_of = {f: n['canonical_name'] for n in graph['nodes'] if n['kind'] == 'MODULE' for f in [n['file']]}
    external = set()
    for row in graph.get('call_statuses', []):
        target = (row.get('evidence') or {}).get('origin_name')
        if row['status'] != 'KNOWN_NON_INTERNAL' or not target:
            continue
        if not DOTTED.fullmatch(target):
            skipped['non_dotted_external'] += 1
            continue
        caller = name(row['caller_id']) if row.get('caller_id') in nodes else module_of.get(row['file'])
        if caller:
            external.add((caller, pycg_external(target)))
    return internal, external, skipped


def adjacency(pairs) -> dict:
    result = defaultdict(set)
    for a, b in pairs:
        result[a].add(b)
    return {a: sorted(bs) for a, bs in sorted(result.items())}


def score(pairs, truth: dict, scorer: dict) -> dict:
    actual = adjacency(pairs)
    expected = {(a, b) for a, bs in truth.items() for b in bs}
    precision = scorer['measure_precision'](actual, truth)
    recall = scorer['measure_recall'](actual, truth)
    return {'precision': precision, 'recall': recall,
            'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            'tp': len(pairs & expected), 'fp': len(pairs - expected), 'fn': len(expected - pairs)}


def overlap(knit: set, pycg: set, truth: dict) -> dict:
    expected = {(a, b) for a, bs in truth.items() for b in bs}
    both, only_knit, only_pycg = knit & pycg, knit - pycg, pycg - knit
    return {'both': len(both), 'only_knitcode': len(only_knit), 'only_pycg': len(only_pycg),
            'jaccard': len(both) / len(knit | pycg) if knit | pycg else 0.0,
            'both_correct': len(both & expected),
            'only_knitcode_correct': len(only_knit & expected),
            'only_pycg_correct': len(only_pycg & expected)}


def run_pycg(python: str, package: Path, files: list[Path], dest: Path) -> float:
    """Run PyCG like generate_pycg_cgs.sh; returns seconds."""
    start = time.perf_counter()
    result = subprocess.run([python, '-m', 'pycg', '--package', str(package), *map(str, files)],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f'PyCG failed for {package}:\n{result.stderr[-2000:]}')
    # PyCG builds module names by replacing "/" in relative paths
    # (utils/common.py: to_mod_name), so on Windows a nested module comes out
    # as "pkg\\mod.func". Backslashes never occur in Python names.
    graph = {caller.replace('\\', '.'): [callee.replace('\\', '.') for callee in callees]
             for caller, callees in json.loads(result.stdout).items()}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(graph, indent=2) + '\n', encoding='utf-8')
    return time.perf_counter() - start


def evaluate(benchmark: Path, project: str, out: Path, scorer: dict, pycg_cgs: Path | None,
             pycg_python: str | None = None) -> dict:
    source = benchmark / 'data/macro-benchmark/projects' / project
    package, files = select_files(source, project)
    truth = json.loads((benchmark / 'data/macro-benchmark/ground-truth-cgs' / (project + '.json')).read_text())
    out.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp, \
            (out / 'analysis.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
        root = Path(tmp)
        for path in files:
            dest = root / path.resolve().relative_to(package)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
        facts, symbols, graph = run(root, out / 'analysis', source_roots=['.'])
    seconds = time.perf_counter() - start
    internal, external, skipped = to_pycg(symbols, graph)
    full = internal | external
    (out / 'knitcode_cg.json').write_text(json.dumps(adjacency(full), indent=2) + '\n', encoding='utf-8')
    result = {'project': project, 'files': len(files), 'parse_errors': len(facts['parse_errors']),
              'seconds': round(seconds, 2), 'skipped': skipped,
              'internal': score(internal, truth, scorer), 'full': score(full, truth, scorer)}
    pycg_path = pycg_cgs / (project + '.json') if pycg_cgs else None
    if pycg_python:
        pycg_path = out / 'pycg_cg.json'
        result['pycg_seconds'] = round(run_pycg(pycg_python, package, files, pycg_path), 2)
    if pycg_path and pycg_path.exists():
        pycg = json.loads(pycg_path.read_text())
        pycg_pairs = {(a, b) for a, bs in pycg.items() for b in bs}
        result['pycg'] = score(pycg_pairs, truth, scorer)
        result['overlap_with_pycg'] = overlap(full, pycg_pairs, truth)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def print_table(results: list[dict]) -> None:
    def pct(x):
        return f'{x * 100:.1f}%'
    print('\n| project | KnitCode internal P / R | KnitCode full P / R (F1) | PyCG P / R (F1) | overlap (Jaccard) |')
    print('|---|---|---|---|---|')
    for r in results:
        i, f, p, o = r['internal'], r['full'], r.get('pycg'), r.get('overlap_with_pycg')
        print(f"| {r['project']} | {pct(i['precision'])} / {pct(i['recall'])} | "
              f"{pct(f['precision'])} / {pct(f['recall'])} ({pct(f['f1'])}) | "
              + (f"{pct(p['precision'])} / {pct(p['recall'])} ({pct(p['f1'])}) | " if p else '- | ')
              + (f"both {o['both']}, KnitCode only {o['only_knitcode']}, PyCG only {o['only_pycg']} "
                 f"({pct(o['jaccard'])}) |" if o else '- |'))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--benchmark', type=Path, required=True,
                        help='pycg-evaluation checkout (with data/macro-benchmark submodules)')
    parser.add_argument('--output', type=Path, default=Path('out/pycg'))
    parser.add_argument('--pycg-cgs', type=Path, help='directory of PyCG outputs <project>.json (optional)')
    parser.add_argument('--pycg-python', help='Python interpreter with PyCG installed; runs PyCG (optional)')
    parser.add_argument('--project', choices=PROJECTS, action='append')
    args = parser.parse_args(argv)
    scorer = runpy.run_path(str(args.benchmark / 'scripts/compare_macro_benchmark_cg.py'))
    results = []
    for project in args.project or PROJECTS:
        results.append(evaluate(args.benchmark, project, args.output / project, scorer,
                                args.pycg_cgs, args.pycg_python))
        print(f"{project}: done in {results[-1]['seconds']}s", flush=True)
    (args.output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
    print_table(results)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
