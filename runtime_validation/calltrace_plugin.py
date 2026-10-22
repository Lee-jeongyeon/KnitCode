"""pytest plugin: record calls between functions of one repository.

Loaded by ``trace_recall.py record`` with ``-p calltrace_plugin``. Every Python
call whose caller and callee both live under KNIT_TRACE_ROOT is recorded once
as (caller file, caller first line, caller name, call line, callee file,
callee first line, callee name). Function names are resolved later from the
source AST, so the result is the same on Python 3.10 (no co_qualname) and newer.

This runs the target project's own tests, i.e. it EXECUTES target code. Use it
only on repositories you trust; the static analyzer itself never does this.
"""
from __future__ import annotations

import json
import os
import sys
import threading

ROOT = os.path.normcase(os.path.realpath(os.environ.get('KNIT_TRACE_ROOT', os.getcwd())))
OUT = os.environ.get('KNIT_TRACE_OUT', 'trace.json')
_seen: set = set()
# Furthest line each function reached when it returned or raised; a call
# below it on every run was never reached (e.g. after an early skipTest).
_reached: dict = {}
_relative: dict = {}


def _rel(filename: str):
    """Repository-relative '/' path, or None outside ROOT (cached)."""
    cached = _relative.get(filename, False)
    if cached is not False:
        return cached
    result = None
    path = os.path.normcase(os.path.realpath(filename)) if filename and not filename.startswith('<') else ''
    if path.startswith(ROOT + os.sep):
        result = os.path.relpath(path, ROOT).replace(os.sep, '/')
    _relative[filename] = result
    return result


def _profile(frame, event, arg):
    if event == 'return':
        code = frame.f_code
        rel = _rel(code.co_filename)
        if rel is not None:
            key = (rel, code.co_firstlineno, code.co_name)
            if frame.f_lineno > _reached.get(key, 0):
                _reached[key] = frame.f_lineno
        return
    if event != 'call':
        return
    caller = frame.f_back
    if caller is None:
        return
    callee_file = _rel(frame.f_code.co_filename)
    if callee_file is None:
        return
    caller_file = _rel(caller.f_code.co_filename)
    if caller_file is None:
        return
    _seen.add((caller_file, caller.f_code.co_firstlineno, caller.f_code.co_name, caller.f_lineno,
               callee_file, frame.f_code.co_firstlineno, frame.f_code.co_name))


def pytest_configure(config):
    sys.setprofile(_profile)
    threading.setprofile(_profile)


def pytest_sessionfinish(session, exitstatus):
    sys.setprofile(None)
    threading.setprofile(None)
    payload = {
        'format': 'knitcode-calltrace-1',
        'root': ROOT.replace(os.sep, '/'),
        'python': '%d.%d.%d' % sys.version_info[:3],
        'pytest_exit_status': int(exitstatus),
        'tests_collected': session.testscollected,
        'tests_failed': session.testsfailed,
        'fields': ['caller_file', 'caller_first_line', 'caller_name', 'call_line',
                   'callee_file', 'callee_first_line', 'callee_name'],
        'calls': sorted(_seen),
        'reached_fields': ['file', 'first_line', 'name', 'furthest_line'],
        'reached': sorted([*key, line] for key, line in _reached.items()),
    }
    with open(OUT, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)
