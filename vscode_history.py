"""Analyze Git commit and file-change history."""
import subprocess
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path


def read_history(root, files, days=90, limit=300):
    """Count ordinary commits; bound large commits to avoid quadratic explosions."""
    root = Path(root).resolve()
    result = {'days': days, 'limit': limit, 'commits': [], 'changes': {}, 'pairs': [],
              'available': False, 'skipped_large_commits': 0}
    def git(*args):
        return subprocess.run(['git', '-C', str(root), *args], check=True,
                              capture_output=True, timeout=30).stdout
    try:
        top = Path(git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        prefix = root.relative_to(top).as_posix()
        prefix = '' if prefix == '.' else prefix + '/'
        hashes = git('log', '--no-merges', f'--since={days}.days', f'-{limit}', '--format=%H', '--', '.').decode().splitlines()
        changes, pairs = Counter(), defaultdict(list)
        for sha in hashes:
            meta = git('show', '-s', '--format=%s%x00%cI', sha).decode(errors='replace').strip().split('\0', 1)
            names = git('diff-tree', '--root', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', sha).decode(errors='replace').split('\0')
            changed = sorted({n[len(prefix):] for n in names if n.startswith(prefix) and n[len(prefix):] in files})
            if not changed:
                continue
            result['commits'].append({'sha': sha, 'subject': meta[0], 'date': meta[1], 'files': changed})
            changes.update(changed)
            if len(changed) > 50:
                result['skipped_large_commits'] += 1
                continue
            for pair in combinations(changed, 2):
                pairs[pair].append(sha)
        result.update(available=True, changes=dict(changes), pairs=[
            {'source': a, 'target': b, 'commits': shas}
            for (a, b), shas in sorted(pairs.items()) if len(shas) >= 2])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result['error'] = str(exc)
    return result
