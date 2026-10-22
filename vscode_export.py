"""VS Code JSON adapter for this checkout's static engine (no target imports)."""
import argparse
import json
from pathlib import Path
import sys

# Allow isolated-mode execution, without adding the analyzed project to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ast_parser import scan_repository
from symbol_builder import build_symbol_result
from graph_builder import build_graph
from unresolved_classifier import reclassify_graph
from call_status import annotate_call_statuses
from vscode_history import read_history


def export_project(root, days=90, limit=300):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Analysis root must be a directory')
    facts = scan_repository(root)
    symbols = build_symbol_result(facts)
    graph = annotate_call_statuses(reclassify_graph(build_graph(symbols), symbols), symbols)

    def relative(file):
        return file.removeprefix(root.name + '/')

    files = [relative(f['file']) for f in facts['files']]
    nodes = [{**n, 'name': n.get('canonical_name', n['name']),
              'file': relative(n['file']), 'type': n['kind'].lower(),
              'lineno': n.get('line') or 1} for n in graph['nodes']]
    nodes += [dict(id='file:' + f, name=Path(f).name, file=f, type='file', lineno=1)
              for f in files]
    # Implicit calls (property access, operators, with, sorted key callbacks)
    # are kept apart from call-site edges as calls_implicit.
    edges = [{**e, 'type': e['type'].lower(),
              'lineno': e.get('evidence', {}).get('line')}
             for e in [*graph['edges'], *graph.get('implicit_edges', [])]]
    edges += [dict(source='file:' + n['file'], target=n['id'], type='contains')
              for n in nodes if n['type'] != 'file']
    history = read_history(root, set(files), days, limit)
    edges += [dict(source='file:' + p['source'], target='file:' + p['target'],
                   type='cochange', count=len(p['commits']), commits=p['commits'])
              for p in history['pairs']]
    callers = {}
    for edge in edges:
        if edge['type'] in ('calls', 'instantiates'):
            callers.setdefault(edge['target'], set()).add(edge['source'])
    for node in nodes:
        node['callers'] = len(callers.get(node['id'], set()))
        node['changes'] = history['changes'].get(node['file'], 0)
    return dict(nodes=nodes, edges=edges, history=history,
                summary=dict(files=len(files), resolved=graph['resolved_call_sites'],
                             unresolved=graph['unresolved_call_sites']),
                status_summary=graph['status_summary'],
                call_statuses=[{**c, 'file': relative(c['file'])}
                               for c in graph['call_statuses']],
                warnings=[f"{relative(e['file'])}: {e['message']}" for e in facts['parse_errors']],
                flow=graph.get('object_flow_stats', {}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--limit', type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(export_project(args.root, max(1, min(args.days, 3650)),
                                    max(1, min(args.limit, 2000))), ensure_ascii=True))
