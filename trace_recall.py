"""Runtime validation: compare the static call graph with calls observed
while the target project's own tests run.

    python trace_recall.py prepare --dest targets [--venv .venv-validate] [--only rich]
    python trace_recall.py record  <repo> --out trace.json [--python PY] [--tests tests] [-- PYTEST_ARGS]
    python trace_recall.py compare --output-dir output --trace trace.json [--baseline-dir OLD] [--report r.json]
    python trace_recall.py run     <repo> --output-dir output [--python PY] [--trace trace.json]

`record` EXECUTES the target project's tests (through pytest, with
runtime_validation/calltrace_plugin.py). The static analyzer never runs target
code; this tool does, by design. Use trusted repositories only.

Metrics (function -> function calls; module-level callers are excluded):
  recall     observed calls that the graph contains / observed calls
  confirmed  graph edges observed at runtime / graph edges on an executed
             site (the caller ran and some run reached that line). A lower
             bound on precision: tests do not take every branch, so an
             unobserved edge is not necessarily wrong.
Explicit edges (`edges`) and implicit edges (`implicit_edges`) are reported
separately and combined.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TOOL_DIR / 'runtime_validation'
TARGETS = TOOL_DIR / 'validation' / 'runtime_targets.json'


# -------------------------------------------------------------------- prepare
def venv_python(venv):
    venv = Path(venv)
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def prepare(dest, *, venv=None, only=None):
    """Clone the pinned target commits; optionally build one venv with pytest,
    each target (editable) and its extra test packages."""
    targets = [t for t in json.loads(TARGETS.read_text(encoding='utf-8'))['targets']
               if not only or t['name'] in only]
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for t in targets:
        repo = dest / t['name']
        if not (repo / '.git').exists():
            subprocess.check_call(['git', 'clone', '--quiet', t['url'], str(repo)])
        subprocess.check_call(['git', '-C', str(repo), 'checkout', '--quiet', t['commit']])
        print(f"{t['name']}: {repo} @ {t['commit']}")
    if venv:
        python = venv_python(venv)
        if not python.exists():
            subprocess.check_call([sys.executable, '-m', 'venv', str(venv)])
        packages = ['pytest', *{p for t in targets for p in t['extra_packages']}]
        for t in targets:
            packages += ['-e', str(dest / t['name'])]
        subprocess.check_call([str(python), '-m', 'pip', 'install', '--quiet', *packages])
        print(f'Validation interpreter: {python}')
    for t in targets:
        print(f"  python trace_recall.py run {dest / t['name']} --output-dir out/{t['name']} "
              f"--tests {t['tests']}" + (f' --python {venv_python(venv)}' if venv else ''))


# --------------------------------------------------------------------- record
def record(repo, out, *, python=None, tests='tests', pytest_args=()):
    repo, out = Path(repo).resolve(), Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, KNIT_TRACE_ROOT=str(repo), KNIT_TRACE_OUT=str(out))
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(PLUGIN_DIR), env.get('PYTHONPATH')]))
    command = [python or sys.executable, '-m', 'pytest', '-p', 'calltrace_plugin',
               '-p', 'no:cacheprovider', '-q', *([tests] if tests else []), *pytest_args]
    print('Running:', ' '.join(command), f'(cwd={repo})')
    status = subprocess.call(command, cwd=repo, env=env)
    if not out.exists():
        raise SystemExit(f'No trace written (pytest exit {status}). Is pytest installed for {command[0]}?')
    data = json.loads(out.read_text(encoding='utf-8'))
    data['commit'] = _git_head(repo)
    out.write_text(json.dumps(data), encoding='utf-8')
    print(f"Trace: {len(data['calls'])} distinct calls, pytest exit {status} "
          f"({data['tests_failed']} failed of {data['tests_collected']} collected) -> {out}")
    if status not in (0, 1):
        print('WARNING: pytest did not finish normally; coverage of the trace may be low.')
    return data


def _git_head(repo):
    try:
        return subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# ---------------------------------------------------------- function lookup
class FunctionIndex:
    """Resolve a code object (file, first line, name) to the analyzer's
    qualified name using the source AST, identically on every Python 3.10+.

    co_firstlineno of a decorated function is its first decorator's line.
    Comprehension and lambda frames (separate frames before Python 3.12) are
    attributed to the enclosing function, as the static graph does."""

    def __init__(self, repo: Path):
        self.repo = repo
        self.by_start: dict[str, dict] = {}
        self.spans: dict[str, list] = {}

    def _load(self, rel):
        if rel in self.by_start:
            return
        starts, spans = {}, []
        try:
            tree = ast.parse((self.repo / rel).read_text(encoding='utf-8-sig'))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            tree = None

        def walk(node, prefix):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    qual = '.'.join([*prefix, child.name])
                    if not isinstance(child, ast.ClassDef):
                        first = min([d.lineno for d in child.decorator_list] + [child.lineno])
                        starts[(first, child.name)] = qual
                        spans.append((child.lineno, child.end_lineno or child.lineno, qual))
                    walk(child, [*prefix, child.name])
                else:
                    walk(child, prefix)
        if tree is not None:
            walk(tree, [])
        self.by_start[rel] = starts
        self.spans[rel] = sorted(spans, key=lambda s: s[1] - s[0])     # innermost first

    def resolve(self, rel, first_line, name):
        self._load(rel)
        if name.startswith('<'):                  # <listcomp>, <genexpr>, <lambda>, ...
            return next((q for start, end, q in self.spans[rel] if start <= first_line <= end), None)
        return self.by_start[rel].get((first_line, name))


# -------------------------------------------------------------------- compare
def _strip(path, repository):
    path = path.replace('\\', '/')
    head, _, rest = path.partition('/')
    return rest if repository and head == repository and rest else path


def load_graph(output_dir):
    out = Path(output_dir)
    graph = json.loads((out / 'graph_result.json').read_text(encoding='utf-8'))
    symbols = json.loads((out / 'symbol_result.json').read_text(encoding='utf-8'))
    repository = symbols.get('repository')
    functions = {s['id']: (_strip(s['file'], repository), s['qualified_name'])
                 for s in symbols['symbols'] if s['kind'] in ('FUNCTION', 'METHOD')}
    meta = {s['id']: s for s in symbols['symbols']}

    def edge_set(edges, explicit=False):
        result = set()
        for e in edges:
            ev = e.get('evidence') or {}
            if explicit and (e['type'] != 'CALLS' or not ev.get('call_id')):
                continue
            if e['source'] in functions and e['target'] in functions:
                result.add((*functions[e['source']], ev.get('line'), *functions[e['target']]))
        return result

    return {'graph': graph, 'repository': repository, 'functions': functions, 'meta': meta,
            'explicit': edge_set(graph['edges'], explicit=True),
            'implicit': edge_set(graph.get('implicit_edges', []))}


def observed_calls(trace, repo, functions):
    known = set(functions.values())
    index = FunctionIndex(Path(repo))
    observed, unresolved = set(), 0
    for caller_file, caller_first, caller_name, line, callee_file, callee_first, callee_name in trace['calls']:
        caller = index.resolve(caller_file, caller_first, caller_name)
        callee = index.resolve(callee_file, callee_first, callee_name)
        if caller is None or callee is None:
            unresolved += 1
            continue
        if (caller_file, caller) in known and (callee_file, callee) in known:
            observed.add((caller_file, caller, line, callee_file, callee))
    return observed, unresolved


def furthest_lines(trace, repo):
    """(file, qualified name) -> furthest line any run of it reached."""
    index = FunctionIndex(Path(repo))
    furthest = {}
    for file, first_line, name, line in trace.get('reached', []):
        if name.startswith('<'):
            continue                              # attributed via the caller frame
        qual = index.resolve(file, first_line, name)
        if qual and line > furthest.get((file, qual), 0):
            furthest[(file, qual)] = line
    return furthest


def _miss_kind(call, by_name):
    symbol = by_name.get((call[3], call[4]))
    name = call[4].rsplit('.', 1)[-1]
    decorators = (symbol or {}).get('decorators') or []
    if any(d.split('(')[0].split('.')[-1] in ('property', 'cached_property') or
           d.endswith(('.setter', '.getter', '.deleter')) for d in decorators):
        return 'property'
    if name in ('__enter__', '__exit__', '__aenter__', '__aexit__'):
        return 'context_manager'
    if name.startswith('__') and name.endswith('__') and name != '__init__':
        return 'special_method'
    return 'other'


def compare(output_dir, trace_path, *, repo=None, baseline_dir=None):
    trace = json.loads(Path(trace_path).read_text(encoding='utf-8'))
    current = load_graph(output_dir)
    repo = repo or trace['root']
    observed, unresolved = observed_calls(trace, repo, current['functions'])
    reached = furthest_lines(trace, repo)
    for call in observed:
        # A function can return early on a line above one it already ran
        # (e.g. inside a loop); an observed call line was reached by definition.
        key = (call[0], call[1])
        reached[key] = max(reached.get(key, 0), call[2])
    ran = {(c[0], c[1]) for c in observed}
    by_name = {v: current['meta'][k] for k, v in current['functions'].items()}

    def executed_site(e):
        # The caller ran and some run got at least as far as this line.
        return (e[0], e[1]) in ran and e[2] is not None and e[2] <= reached.get((e[0], e[1]), 0)

    def score(edges):
        executed = {e for e in edges if executed_site(e)}
        hit = observed & edges
        return {'edges': len(edges), 'edges_on_executed_sites': len(executed),
                'confirmed': len(executed & observed),
                'confirmed_rate': round(len(executed & observed) / len(executed), 4) if executed else None,
                'recall_hits': len(hit),
                'recall': round(len(hit) / len(observed), 4) if observed else None}

    sets = {'explicit': current['explicit'],
            'explicit+implicit': current['explicit'] | current['implicit'],
            'implicit_only': current['implicit']}
    missed = observed - sets['explicit+implicit']
    report = {
        'repository': current['repository'], 'commit': trace.get('commit'),
        'trace_python': trace.get('python'), 'analyzer_python': '%d.%d.%d' % sys.version_info[:3],
        'tests_collected': trace.get('tests_collected'), 'tests_failed': trace.get('tests_failed'),
        'observed_function_calls': len(observed), 'trace_frames_not_mapped': unresolved,
        'scores': {name: score(edges) for name, edges in sets.items()},
        'missed_by_kind': dict(Counter(_miss_kind(m, by_name) for m in missed)),
        'missed_examples': [dict(zip(('caller_file', 'caller', 'line', 'callee_file', 'callee'), m))
                            for m in sorted(missed)[:50]],
    }
    if baseline_dir:
        base = load_graph(baseline_dir)
        old = base['explicit'] | base['implicit']
        new = sets['explicit+implicit']
        added, lost = new - old, old - new
        added_executed = {e for e in added if executed_site(e)}
        report['baseline'] = {
            'recall': round(len(observed & old) / len(observed), 4) if observed else None,
            'added_edges': len(added), 'added_confirmed': len(added_executed & observed),
            'added_on_executed_sites': len(added_executed),
            'lost_edges': len(lost), 'lost_confirmed': len(lost & observed)}
    return report


def print_report(report):
    def pct(x):
        return '-' if x is None else f'{x:.1%}'
    print(f"\n## Runtime validation: {report['repository']} @ {(report['commit'] or '?')[:12]}")
    print(f"tests: {report['tests_collected']} collected, {report['tests_failed']} failed | "
          f"observed function->function calls: {report['observed_function_calls']}")
    print('\n| edge set | recall | confirmed / edges on executed sites |')
    print('|---|---|---|')
    for name, s in report['scores'].items():
        print(f"| {name} | {pct(s['recall'])} ({s['recall_hits']}) | "
              f"{s['confirmed']} / {s['edges_on_executed_sites']} ({pct(s['confirmed_rate'])}) |")
    if 'baseline' in report:
        b = report['baseline']
        print(f"\nbaseline recall {pct(b['recall'])}; added edges {b['added_edges']} "
              f"(confirmed {b['added_confirmed']} of {b['added_on_executed_sites']} on executed sites); "
              f"lost edges {b['lost_edges']} (of which observed {b['lost_confirmed']})")
    print(f"\nmissed by kind: {report['missed_by_kind']}")
    print('Note: confirmed rate is a lower bound on precision, not precision.')


# ----------------------------------------------------------------------- CLI
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = []
    if '--' in argv:
        cut = argv.index('--')
        argv, extra = argv[:cut], argv[cut + 1:]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare', help='clone the pinned targets (validation/runtime_targets.json)')
    prep.add_argument('--dest', default='targets')
    prep.add_argument('--venv', help='create/update this venv with pytest and the targets')
    prep.add_argument('--only', nargs='*', help='target names, e.g. --only rich click')
    rec = sub.add_parser('record', help='run the target tests and record observed calls')
    rec.add_argument('repo')
    rec.add_argument('--out', default='trace.json')
    rec.add_argument('--python', help='interpreter that has pytest and the target dependencies')
    rec.add_argument('--tests', default='tests', help='test path inside the repo ("" for pytest default)')
    cmp_ = sub.add_parser('compare', help='compare an analyzer output directory with a trace')
    cmp_.add_argument('--output-dir', default='output')
    cmp_.add_argument('--trace', required=True)
    cmp_.add_argument('--repo', help='source checkout (default: the root stored in the trace)')
    cmp_.add_argument('--baseline-dir', help='older analyzer output for the same commit')
    cmp_.add_argument('--report', help='write the JSON report here')
    run = sub.add_parser('run', help='analyze, record (unless --trace exists) and compare')
    run.add_argument('repo')
    run.add_argument('--output-dir', default='output')
    run.add_argument('--trace', help='reuse or write this trace (default: <output-dir>/trace.json)')
    run.add_argument('--python')
    run.add_argument('--tests', default='tests')
    run.add_argument('--baseline-dir')
    args = parser.parse_args(argv)

    if args.command == 'prepare':
        prepare(args.dest, venv=args.venv, only=args.only)
        return 0
    if args.command == 'record':
        record(args.repo, args.out, python=args.python, tests=args.tests, pytest_args=extra)
        return 0
    if args.command == 'compare':
        report = compare(args.output_dir, args.trace, repo=args.repo, baseline_dir=args.baseline_dir)
    else:
        import main as analyzer
        analyzer.run(args.repo, args.output_dir)
        trace = Path(args.trace or Path(args.output_dir) / 'trace.json')
        if not trace.exists():
            record(args.repo, trace, python=args.python, tests=args.tests, pytest_args=extra)
        report = compare(args.output_dir, trace, repo=args.repo, baseline_dir=args.baseline_dir)
        args.report = str(Path(args.output_dir) / 'runtime_validation.json')
    print_report(report)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Report: {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
