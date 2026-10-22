"""CSV export of implicit calls (CALLS_IMPLICIT), kept apart from `edges`."""
from __future__ import annotations

import csv
from pathlib import Path


def write_implicit_csv(graph: dict, out: str | Path) -> None:
    fields = ('file', 'line', 'column', 'source', 'implicit_kind', 'member', 'caller', 'target', 'receiver_classes')
    with open(Path(out) / 'implicit_calls.csv', 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for edge in graph.get('implicit_edges', []):
            ev = edge['evidence']
            writer.writerow({**{k: ev.get(k) for k in fields}, 'caller': edge['source'], 'target': edge['target'],
                             'receiver_classes': ';'.join(ev['receiver_classes'])})
