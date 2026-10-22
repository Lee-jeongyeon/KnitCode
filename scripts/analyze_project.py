"""Run a pinned static analysis and record reproducibility/size/time metadata."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from main import run


def analyze(repo, output, source_roots=None):
    repo, output = repo.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with (output / 'analysis.log').open('w') as stream, contextlib.redirect_stdout(stream):
        facts, symbols, graph = run(repo, output, source_roots=source_roots)
    elapsed = time.perf_counter() - start
    metadata = dict(
        repo=str(repo), commit=facts['commit'], python=sys.version, platform=platform.platform(),
        seconds_including_exports=elapsed, source_roots=facts['source_roots'],
        python_files=facts['python_file_count'], lines=sum(len(f['source'].splitlines()) for f in facts['files']),
        parse_errors=facts['parse_errors'], nodes=graph['node_count'], edges=graph['edge_count'],
        calls=graph['total_call_sites'], statuses=graph['status_summary']['statuses'],
        flow=graph.get('object_flow_stats', {}),
        engine_sha256={f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(ROOT.glob('*.py'))},
        source_sha256={f['file']: hashlib.sha256(f['source'].encode()).hexdigest() for f in facts['files']},
        note='Status counts and structural verification are not precision/recall. No target code executed.')
    for name, script in [('graph', 'verify_v23.py'), ('statuses', 'verify_v231.py')]:
        result = subprocess.run([sys.executable, str(ROOT/script), '--graph', str(output/'graph_result.json'),
                                 '--symbols', str(output/'symbol_result.json'), '--repo', str(repo)],
                                capture_output=True, text=True, timeout=120)
        (output / f'verify_{name}.log').write_text(result.stdout + result.stderr)
        metadata[f'verify_{name}'] = result.returncode
    (output/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    print(json.dumps({k:v for k,v in metadata.items() if not k.endswith('sha256')}, indent=2))
    if metadata['verify_graph'] or metadata['verify_statuses']:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-root', action='append')
    args = parser.parse_args()
    analyze(args.repo, args.output, args.source_root)
