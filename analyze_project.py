"""
KnitCode 코드 구조 분석기 v2.3

사용법:  python analyze_project.py <분석할 프로젝트 폴더>
결과:    터미널 요약 + graph_<프로젝트명>.json

v1 대비 바뀐 점
 1) 해석 못 한 호출을 '왜 못 했는지'로 분류한다
    (내장 / 표준 라이브러리 / 외부 패키지 / 외부 부모에게 물려받음 / 타입추론 필요 / 진짜 놓친 것)
 2) 자식 클래스가 오버라이드한 메서드도 'calls_override' 간선으로 연결한다 (동적 디스패치)
 3) 부모 클래스 해석 보강 (as 별칭, Base[T] 제네릭, a.b.Base 점 표기)
 4) 타입 추적 (v2.1): 매개변수 타입힌트 / 외부 호출 결과를 담은 변수 / self.속성 대입으로 수신 객체의 타입을 알아낸다
    - 프로젝트 클래스로 확정되면 내부 연결(calls, via 표시), 외부 타입으로 확정되면 외부 분류(confidence=inferred)
    - 같은 이름이 다른 방식으로도 바인딩되면(루프 변수, 튜플 풀기, 재대입 등) 포기한다 (잘못된 연결 방지)
 5) v2.3: 구조 간선(모듈 imports / 클래스 extends / 소속 contains), 프로젝트 함수의 반환 타입힌트,
    모듈 수준 변수의 타입, 표준 라이브러리 함수의 반환 타입표
"""
import ast
import builtins
import importlib.util
import json
import os
import sys
import sysconfig
from collections import Counter, defaultdict, deque
from pathlib import Path

SCHEMA_VERSION = 1          # 출력 JSON 의 구조(키·간선 종류·노드 필드)가 바뀌면 올린다 (시각화 쪽과의 약속)
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "build", "dist", "node_modules", "site-packages"}
FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# =====================================================================
# 0. 외부 분류에 쓰는 기준표
# =====================================================================
BUILTIN_NAMES = set(dir(builtins))                     # print, len, ValueError, super ...


def _methods_of(*types):
    return {n for t in types for n in dir(t) if not n.startswith("__")}


# 변수 타입을 모를 때, 메서드 이름만 보고 '내장 타입 메서드일 것'이라고 추정하는 목록
BUILTIN_TYPE_METHODS = _methods_of(str, bytes, bytearray, list, dict, set, frozenset, tuple, int, float)


# 다른 라이브러리에도 흔한 이름이라, str/list/dict 메서드라고 단정하기 어려운 것들 (신뢰도를 'weak'로 표시)
AMBIGUOUS_NAMES = {"get", "join", "update", "copy", "find", "pop", "remove", "clear", "index", "count"}


def _make_stdlib_checker():
    """표준 라이브러리 여부 판별기. Python 3.9에도 3.10+에도 동작하도록 두 방식을 모두 준비."""
    names = getattr(sys, "stdlib_module_names", None)          # Python 3.10+ 에만 존재
    if names is not None:
        return lambda top: top in names or top in sys.builtin_module_names

    stdlib_dir = os.path.normcase(sysconfig.get_paths()["stdlib"])
    dll_dir = os.path.normcase(os.path.join(sys.base_prefix, "DLLs"))   # Windows의 math, select 등
    cache = {}

    def check(top):
        if top in cache:
            return cache[top]
        res = False
        if top in sys.builtin_module_names:
            res = True
        else:
            try:
                spec = importlib.util.find_spec(top)            # 최상위 이름은 import 없이 위치만 찾는다
            except (ImportError, ValueError, AttributeError):
                spec = None
            origin = getattr(spec, "origin", None)
            if origin in ("built-in", "frozen"):
                res = True
            elif origin:
                o = os.path.normcase(origin)
                in_std = o.startswith(stdlib_dir) or o.startswith(dll_dir)
                res = in_std and "site-packages" not in o and "dist-packages" not in o
        cache[top] = res
        return res

    return check


is_stdlib = _make_stdlib_checker()


# =====================================================================
# 1. 파일 찾기 + 파싱
# =====================================================================
def module_name(root, path):
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def parse_files(root, skipped=None):
    """skipped: 리스트를 넘기면 파싱하지 못해 건너뛴 파일을 {"file", "error"} 로 담아 준다
    (실행하는 Python 버전보다 새로운 문법을 쓴 파일은 여기서 빠진다)"""
    files = {}  # 모듈명 -> (상대경로, AST)
    for path in sorted(root.rglob("*.py")):
        if any(p in SKIP_DIRS for p in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            print(f"[건너뜀] {path}: {e}")
            if skipped is not None:
                skipped.append({"file": path.relative_to(root).as_posix(), "error": f"{type(e).__name__}: {e}"})
            continue
        tree.knit_total_lines = len(text.splitlines())             # 모듈 크기 (loc) 계산용
        files[module_name(root, path)] = (path.relative_to(root).as_posix(), tree)
    return files


# =====================================================================
# 2. 함수/클래스 정의 수집 (고유 ID 부여)
# =====================================================================
# 노드 메타데이터 : 시각화·Git 연동(커밋 변경 줄 -> 함수)에 필요한 정보
def _decorator_names(n):
    out = []
    for d in n.decorator_list:
        out.append(_short(ast.unparse(d), 60))
    return out


def _dec_base(d):
    """데코레이터 표현식의 마지막 이름: @a.b(x) -> 'b'"""
    if isinstance(d, ast.Call):
        d = d.func
    return d.id if isinstance(d, ast.Name) else (d.attr if isinstance(d, ast.Attribute) else None)


def _visibility(name):
    if name.startswith("__") and name.endswith("__"):
        return "dunder"
    return "private" if name.startswith("_") else "public"


def _doc_first_line(n):
    doc = ast.get_docstring(n, clean=True)
    if not doc:
        return None
    first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return _short(first, 120) or None


_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler)


def cyclomatic(fn, skip_ids=frozenset()):
    """순환 복잡도 = 1 + 분기 수 (if / for / while / except / 삼항 / and·or / 컴프리헨션의 for·if / match case).
    별도 노드로 수집된 중첩 함수의 본문은 세지 않는다"""
    c = 1
    for n in own_walk(fn, skip_ids):
        if isinstance(n, _BRANCH_NODES):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            c += 1 + len(n.ifs)
        elif type(n).__name__ == "match_case":
            c += 1
    return c


def own_walk(fn, skip_ids=frozenset()):
    """fn 자신의 코드만 너비 우선으로 훑는다. skip_ids 에 있는 함수 정의(= 별도 노드가 된 중첩 함수·메서드)는 들어가지 않는다"""
    todo = deque([fn])
    while todo:
        node = todo.popleft()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNC_TYPES) and id(child) in skip_ids:
                continue
            todo.append(child)


def _method_kind(n):
    bases = {_dec_base(d) for d in n.decorator_list}
    if "staticmethod" in bases:
        return "static"
    if "classmethod" in bases:
        return "class"
    if bases & {"property", "cached_property", "setter", "getter", "deleter"}:
        return "property"
    return "instance"


def _func_info(n, nid, rel, ntype, parent, nested, skip_ids=frozenset()):
    end = getattr(n, "end_lineno", None) or n.lineno
    info = {"id": nid, "type": ntype, "file": rel, "line": n.lineno, "end_lineno": end, "loc": end - n.lineno + 1,
            "name": n.name, "signature": f"({ast.unparse(n.args)})" + (f" -> {ast.unparse(n.returns)}" if n.returns else ""),
            "docstring": _doc_first_line(n), "decorators": _decorator_names(n),
            "is_async": isinstance(n, ast.AsyncFunctionDef), "visibility": _visibility(n.name),
            "parent": parent}
    if ntype == "method":
        info["kind"] = _method_kind(n)
    if nested:
        info["nested"] = True
    return info


def _class_info(n, cid, rel, parent, nested):
    end = getattr(n, "end_lineno", None) or n.lineno
    info = {"id": cid, "type": "class", "file": rel, "line": n.lineno, "end_lineno": end, "loc": end - n.lineno + 1,
            "name": n.name, "bases": [_short(ast.unparse(b), 60) for b in n.bases],
            "docstring": _doc_first_line(n), "decorators": _decorator_names(n),
            "visibility": _visibility(n.name), "parent": parent}
    if nested:
        info["nested"] = True
    return info


def _function_level_defs(fn):
    """함수 본문 안에 정의된 함수·클래스 (if/for/try 안쪽 포함, 그 정의의 안쪽은 제외).
    같은 이름이 둘 이상이면 어느 쪽인지 알 수 없어 모두 제외한다"""
    found, stack = [], list(reversed(fn.body))
    while stack:
        n = stack.pop()
        if isinstance(n, FUNC_TYPES + (ast.ClassDef,)):
            found.append(n)
            continue
        if isinstance(n, ast.Lambda):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(n))))
    counts = Counter(n.name for n in found)
    return [n for n in found if counts[n.name] == 1]


def collect_defs(files):
    """함수/클래스/메서드와 그 안에 중첩된 함수·클래스를 모두 수집한다.
    ID: `파일::함수`, `파일::클래스.메서드`, 중첩 함수는 `파일::바깥.<locals>.안쪽`, 중첩 클래스는 `파일::바깥클래스.안쪽클래스`
    반환: nodes, top, methods, nested(수집하지 못한 정의 수), classes, defs, class_nodes
      top      모듈 최상위 이름 -> 노드 ID  (이름 해석용이라 최상위만)
      methods  (모듈, 클래스 한정이름) -> {메서드명: ID}   (중첩 클래스도 포함)
      defs     분석 대상 함수 목록 (바깥 것이 안쪽 것보다 먼저)
      class_nodes (모듈, 클래스 한정이름) -> ClassDef"""
    nodes, top, methods, defs, class_nodes = {}, {}, {}, [], {}
    collected = set()

    def add_function(n, mod, rel, qual, parent, cls, is_method, nested):
        nid = f"{rel}::{qual}"
        nodes[nid] = _func_info(n, nid, rel, "method" if is_method else "function", parent, nested)
        defs.append({"node": n, "mod": mod, "rel": rel, "id": nid, "cls": cls, "parent": parent,
                     "parent_is_func": not is_method and parent in nodes and nodes[parent]["type"] in ("function", "method")})
        collected.add(id(n))
        for child in _function_level_defs(n):
            if isinstance(child, ast.ClassDef):
                add_class(child, mod, rel, f"{qual}.<locals>.{child.name}", nid, True)
            else:
                add_function(child, mod, rel, f"{qual}.<locals>.{child.name}", nid, cls, False, True)
        return nid

    def add_class(n, mod, rel, qual, parent, nested):
        cid = f"{rel}::{qual}"
        nodes[cid] = _class_info(n, cid, rel, parent, nested)
        class_nodes[(mod, qual)] = n
        methods[(mod, qual)] = {}
        collected.add(id(n))
        for m in n.body:
            if isinstance(m, FUNC_TYPES):
                mid = add_function(m, mod, rel, f"{qual}.{m.name}", cid, qual, True, nested)
                methods[(mod, qual)][m.name] = mid
            elif isinstance(m, ast.ClassDef):
                add_class(m, mod, rel, f"{qual}.{m.name}", cid, True)
        return cid

    for mod, (rel, tree) in files.items():
        top[mod] = {}
        nodes[rel] = {"id": rel, "type": "module", "file": rel, "line": 1, "module": mod,
                      "end_lineno": getattr(tree, "knit_total_lines", 0), "loc": getattr(tree, "knit_total_lines", 0),
                      "docstring": _doc_first_line(tree), "parent": None}
        for n in tree.body:
            if isinstance(n, FUNC_TYPES):
                top[mod][n.name] = add_function(n, mod, rel, n.name, rel, None, False, False)
            elif isinstance(n, ast.ClassDef):
                top[mod][n.name] = add_class(n, mod, rel, n.name, rel, False)
    nested = 0                                       # 수집하지 못한 정의 (if/try 안의 조건부 정의, 이름이 겹치는 중첩 정의 등)
    for mod, (rel, tree) in files.items():
        for n in ast.walk(tree):
            if isinstance(n, FUNC_TYPES + (ast.ClassDef,)) and id(n) not in collected:
                nested += 1
    skip_ids = {id(d["node"]) for d in defs}
    for d in defs:                                   # 복잡도는 수집이 끝난 뒤에 (별도 노드가 된 중첩 함수 본문은 제외)
        nodes[d["id"]]["complexity"] = cyclomatic(d["node"], skip_ids)
    classes = {k: k for k in methods}           # (모듈, 클래스명) -> 실제 정의 위치. 재수출 이름도 나중에 여기에 추가된다
    return nodes, top, methods, nested, classes, defs, class_nodes


# =====================================================================
# 3. import 해석
# =====================================================================
def resolve_from(mod, is_pkg, level, module):
    if level == 0:
        return module
    parts = mod.split(".")
    if not is_pkg:
        parts = parts[:-1]
    if level > 1:
        parts = parts[:len(parts) - (level - 1)]
    base = ".".join(parts)
    if module:
        base = f"{base}.{module}" if base else module
    return base


def collect_imports(mod, rel, tree, aliases=None, stars=None):
    """코드에서 쓰는 이름 -> ("module", 모듈경로, None, 0) 또는 ("name", 모듈경로, 원래이름, 상대import단계)
    aliases: 절대 import 에 쓰이는 패키지 이름을 분석기의 모듈 이름으로 바꾸는 표 (build_import_aliases)
    stars: 리스트를 넘기면 `from x import *` 를 (모듈경로, 상대import단계) 로 담아 준다 (이름 확장은 expand_star_imports)"""
    table = {}
    is_pkg = rel.endswith("__init__.py")
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                key = a.asname or a.name.split(".")[0]
                path = a.name if a.asname else a.name.split(".")[0]
                table[key] = ("module", canon_module(path, aliases), None, 0)
        elif isinstance(n, ast.ImportFrom):
            base = resolve_from(mod, is_pkg, n.level, n.module)
            if n.level == 0:
                base = canon_module(base, aliases)
            for a in n.names:
                if a.name == "*":
                    if stars is not None:
                        stars.append((base, n.level))
                    continue
                table[a.asname or a.name] = ("name", base, a.name, n.level)
    return table


# =====================================================================
# 3-2. import 루트 보정(src 레이아웃)과 재수출(re-export) 추적
# =====================================================================
def _join_mod(*parts):
    return ".".join(p for p in parts if p)


def build_import_aliases(root, files):
    """실제 import 에 쓰이는 패키지 이름 -> 분석기가 쓰는 모듈 이름.
    분석기는 폴더 기준으로 모듈 이름을 정하지만(src/requests/utils.py -> src.requests.utils),
    코드에서는 `import requests` 처럼 쓴다. __init__.py 를 거슬러 올라가 패키지 최상위를 찾아 둘을 연결한다.
    __init__.py 가 없는 폴더(네임스페이스 패키지)는 폴더 기준 이름이 이미 맞아서 건드리지 않는다."""
    aliases, ambiguous, seen = {}, set(), set()
    for mod, (rel, tree) in files.items():
        d, pkg_top = (root / rel).parent, None
        while (d / "__init__.py").exists():
            pkg_top = d
            if d.parent == d:
                break
            d = d.parent
        if pkg_top is None or pkg_top in seen:
            continue
        seen.add(pkg_top)
        if pkg_top == root:
            canon = ""                                      # 분석 폴더 자체가 패키지
        else:
            try:
                canon = ".".join(pkg_top.relative_to(root).parts)
            except ValueError:
                continue                                    # 분석 폴더가 패키지 안쪽이면 보정하지 않는다
        real = pkg_top.name
        if real == canon:
            continue
        if aliases.get(real, canon) != canon:
            ambiguous.add(real)                             # 같은 이름의 패키지가 둘 이상이면 믿지 않는다
        aliases[real] = canon
    for k in ambiguous:
        aliases.pop(k, None)
    return aliases


def canon_module(modpath, aliases):
    if not aliases or not modpath:
        return modpath
    for real, canon in aliases.items():
        if modpath == real:
            return canon
        if modpath.startswith(real + "."):
            return _join_mod(canon, modpath[len(real) + 1:])
    return modpath


def compute_reexports(module_imports, top, var_defs=None):
    """(모듈, 이름) -> (실제로 정의된 모듈, 이름).  프로젝트 안의 다른 모듈에서 가져온 이름을 끝까지 따라간다
    예) requests/__init__.py 의 `from .api import get`  ->  ("requests", "get") -> ("requests.api", "get")
    var_defs: 모듈 수준 변수도 같은 방식으로 따라간다 (`from .status_codes import codes`)"""
    var_defs = var_defs or {}

    def follow(mp, nm, seen):
        if nm in top.get(mp, {}) or nm in var_defs.get(mp, {}):
            return (mp, nm)
        if (mp, nm) in seen:
            return None
        seen.add((mp, nm))
        imp = module_imports.get(mp, {}).get(nm)
        if imp and imp[0] == "name":
            return follow(imp[1], imp[2], seen)
        return None

    exports = {}
    for mod, table in module_imports.items():
        for name, imp in table.items():
            if imp[0] != "name" or name in top.get(mod, {}) or name in var_defs.get(mod, {}):
                continue
            target = follow(imp[1], imp[2], {(mod, name)})
            if target:
                exports[(mod, name)] = target
    return exports


def canonicalize_imports(module_imports, exports):
    """`from requests import get` 처럼 재수출된 이름을 가져온 import 는 실제 정의 위치로 바꿔 둔다"""
    for table in module_imports.values():
        for name, imp in list(table.items()):
            if imp[0] == "name":
                t = exports.get((imp[1], imp[2]))
                if t and t != (imp[1], imp[2]):
                    table[name] = ("name", t[0], t[1], imp[3])


def inject_exports(exports, top, classes):
    """모듈이 가져온 이름은 `모듈.이름` 으로도 접근할 수 있으므로 그 모듈의 이름표에 추가한다"""
    for (mod, name), (dm, dn) in exports.items():
        if dn not in top.get(dm, {}):                       # 변수 재수출은 타입표 쪽(inject_var_exports)에서 처리
            continue
        top.setdefault(mod, {}).setdefault(name, top[dm][dn])
        if (dm, dn) in classes:
            classes.setdefault((mod, name), classes[(dm, dn)])


def resolve_dotted(func, imports, top, classes, methods, class_bases, modvar_types=None, trace=None):
    """import 한 모듈에서 시작하는 점 표기 호출을 모듈 트리를 따라가며 해석
    예) pkg.utils.helper(...), pkg.Class(...), pkg.Class.method(...), pkg.codes.get(...) (모듈 변수의 타입을 아는 경우)
    trace: 리스트를 넘기면 중간에 모듈 변수를 거쳤을 때 "var" 를 담아 준다"""
    modvar_types = modvar_types or {}
    parts = dotted_parts(func)
    if not parts or len(parts) < 2:
        return None
    imp = imports.get(parts[0])
    if not imp:
        return None
    if imp[0] == "module":
        cur = ("module", imp[1])
    else:
        sub = _join_mod(imp[1], imp[2])
        if sub in top:
            cur = ("module", sub)
        elif (imp[1], imp[2]) in classes:
            cur = ("class", classes[(imp[1], imp[2])])
        else:
            return None
    if cur[0] == "module" and cur[1] not in top:
        return None
    for part in parts[1:]:
        if cur[0] == "module":
            m = cur[1]
            if part in top.get(m, {}):
                cur = ("class", classes[(m, part)]) if (m, part) in classes else ("func", top[m][part])
            elif (m, part) in modvar_types:
                cur = ("var", modvar_types[(m, part)])
            elif _join_mod(m, part) in top:
                cur = ("module", _join_mod(m, part))
            else:
                return None
        elif cur[0] == "var":                                # 타입을 아는 모듈 변수의 메서드
            t = cur[1]
            found = find_method_up_chain(t[1][0], t[1][1], part, methods, class_bases) if t[0] == "project" else None
            if not found:
                return None
            if trace is not None:
                trace.append("var")
            cur = ("func", found)
        elif cur[0] == "class":
            found = find_method_up_chain(cur[1][0], cur[1][1], part, methods, class_bases)
            if not found:
                return None
            cur = ("func", found)
        else:
            return None
    if cur[0] == "func":
        return cur[1]
    if cur[0] == "class":
        return top[cur[1][0]][cur[1][1]]                    # 클래스 생성 -> 클래스 노드
    return None


# =====================================================================
# 4. 클래스 상속 관계
# =====================================================================
def dotted_parts(node):
    """a.b.C -> ['a','b','C'],  Name이 아닌 뿌리면 None"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return parts[::-1]
    return None


def resolve_class_ref(node, mod, imports, top, classes):
    """부모 클래스 표현식 -> (모듈, 클래스명). 프로젝트 안에 없으면 None"""
    if isinstance(node, ast.Subscript):          # Base[T] 형태(제네릭)는 Base만 본다
        node = node.value
    parts = dotted_parts(node)
    if not parts:
        return None
    if len(parts) == 1:
        n = parts[0]
        if (mod, n) in classes:
            return classes[(mod, n)]
        imp = imports.get(n)
        if imp and imp[0] == "name" and (imp[1], imp[2]) in classes:   # as 별칭이어도 원래 이름으로 찾는다
            return classes[(imp[1], imp[2])]
        return None
    root, mid, last = parts[0], parts[1:-1], parts[-1]
    imp = imports.get(root)
    if not imp:
        return None
    if imp[0] == "module":
        modpath = _join_mod(imp[1], *mid)
    else:
        modpath = _join_mod(imp[1], imp[2], *mid)
    if (modpath, last) in classes:
        return classes[(modpath, last)]
    return None


def collect_class_bases(class_nodes, top, classes, module_imports):
    """class_bases: (모듈,클래스) -> 프로젝트 안 부모들 / class_ext_bases: -> 프로젝트 밖 부모 이름들
    중첩 클래스는 같은 범위의 다른 클래스를 부모로 쓸 수 있는데 이름으로 찾을 수 없다.
    import 도, 내장 이름도 아닌 부모는 '프로젝트 밖'이라고 단정하지 않고 건너뛴다"""
    class_bases, class_ext_bases = {}, {}
    for (mod, qual), n in class_nodes.items():
        imports = module_imports[mod]
        inner, outer = [], []
        for b in n.bases:
            ref = resolve_class_ref(b, mod, imports, top, classes)
            if ref:
                inner.append(ref)
                continue
            name = ast.unparse(b.value if isinstance(b, ast.Subscript) else b)
            if name == "object":
                continue
            root = root_of(b.value if isinstance(b, ast.Subscript) else b)
            if "." in qual and not (isinstance(root, ast.Name) and (root.id in imports or root.id in BUILTIN_NAMES)):
                continue
            outer.append(name)
        class_bases[(mod, qual)] = inner
        class_ext_bases[(mod, qual)] = outer
    return class_bases, class_ext_bases


def _dfs_order(key, class_bases, seen=None):
    seen = seen if seen is not None else set()
    if key in seen:
        return []
    seen.add(key)
    out = [key]
    for b in class_bases.get(key, []):
        out += _dfs_order(b, class_bases, seen)
    return out


def class_mro(mod, cls, class_bases):
    """Python 의 메서드 해석 순서(C3 선형화).  프로젝트 안 부모만 다룬다.
    파이썬 자신도 거부하는 모순된 상속(TypeError)이면 깊이 우선 순서로 대신한다"""
    memo = {}

    def lin(k, stack):
        if k in memo:
            return memo[k]
        if k in stack:
            raise ValueError("cycle")
        bases = list(class_bases.get(k, []))
        seqs = [list(lin(b, stack + (k,))) for b in bases] + [bases]
        out = [k]
        while True:
            seqs = [q for q in seqs if q]
            if not seqs:
                break
            for q in seqs:
                cand = q[0]
                if not any(cand in other[1:] for other in seqs):
                    break
            else:
                raise ValueError("inconsistent hierarchy")
            out.append(cand)
            seqs = [q[1:] if q[0] == cand else q for q in seqs]
        memo[k] = out
        return out

    try:
        return lin((mod, cls), ())
    except ValueError:
        return _dfs_order((mod, cls), class_bases)


def find_method_up_chain(mod, cls, attr, methods, class_bases, seen=None):
    """메서드 해석 순서(MRO)대로 클래스를 따라가며 처음 나오는 메서드를 찾는다"""
    for k in class_mro(mod, cls, class_bases):
        found = methods.get(k, {}).get(attr)
        if found:
            return found
    return None


def chain_ext_bases(mod, cls, class_bases, class_ext_bases, seen=None):
    """상속 사슬 전체에서 프로젝트 밖 부모 이름들을 모은다"""
    seen = seen if seen is not None else set()
    if (mod, cls) in seen:
        return []
    seen.add((mod, cls))
    out = list(class_ext_bases.get((mod, cls), []))
    for bm, bc in class_bases.get((mod, cls), []):
        out += chain_ext_bases(bm, bc, class_bases, class_ext_bases, seen)
    return out


def build_descendants(class_bases):
    children = defaultdict(set)
    for child, bases in class_bases.items():
        for b in bases:
            children[b].add(child)

    def all_desc(k):
        seen, stack = set(), list(children.get(k, ()))
        while stack:
            c = stack.pop()
            if c not in seen:
                seen.add(c)
                stack.extend(children.get(c, ()))
        return seen

    return all_desc



def collect_module_vars(files, var_defs=None):
    """모듈 최상위의 'X = str', 'X = {...}' 같은 대입을 보고 내장 값/별칭인지 기록: (모듈, 이름) -> 'builtin'
    var_defs 가 주어지면 '한 번만 대입된 변수'만 인정한다 (나중에 다른 값으로 바뀌는 변수는 믿지 않는다)"""
    out = {}
    value_nodes = (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr,
                   ast.ListComp, ast.DictComp, ast.SetComp)
    for mod, (rel, tree) in files.items():
        for n in tree.body:
            targets, value = [], None
            if isinstance(n, ast.Assign):
                targets, value = n.targets, n.value
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                targets, value = [n.target], n.value
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if var_defs is not None and t.id not in var_defs.get(mod, {}):
                    continue
                if isinstance(value, value_nodes) or (isinstance(value, ast.Name) and value.id in BUILTIN_NAMES):
                    out[(mod, t.id)] = "builtin"
    return out

# =====================================================================
# 5. 지역 변수 타입 추적 (x = ClassName(...))
# =====================================================================
def collect_local_var_types(fn_node, mod, imports, top, classes):
    var_types = {}
    for stmt in ast.walk(fn_node):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and isinstance(stmt.value, ast.Call):
                ref = resolve_class_ref(stmt.value.func, mod, imports, top, classes)
                if ref:
                    var_types[target.id] = ref
    return var_types


# =====================================================================
# 5-2. 타입 추적 (타입힌트 / 생성자 / 반환 타입힌트 / 모듈 변수 / self.속성 / 표준 라이브러리 반환표)
# =====================================================================
# 타입 표현:  ("project", (모듈, 클래스명))              프로젝트 안 클래스의 인스턴스 (생성자로 만들어서 정확히 이 클래스)
#             ("project", (모듈, 클래스명), "declared")   선언(타입힌트)된 타입 - 실제로는 자식 클래스일 수 있다
#             ("ext", 출처, 패키지)                       출처: builtin | stdlib | third_party
#
# 원칙: '확실할 때만' 타입을 부여한다. 같은 이름이 다른 방식으로도 바인딩되면(루프 변수, 튜플 풀기,
#       중첩 함수 인자, 서로 다른 타입의 재대입 등) 그 이름은 포기한다. 잘못된 연결을 만드는 것보다
#       미확정으로 남기는 편이 영향도 분석에 안전하기 때문이다.
_VALUE_BUILTIN_NODES = (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr,
                        ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
_BUILTIN_T = ("ext", "builtin", "builtins")
_NEUTRAL = "NEUTRAL"        # `self.x = None`, `x += 1` 처럼 타입 판단에 영향을 주지 않는 대입
_TYPE_VIA = ("type_hint", "local_assign", "self_attr", "module_var", "return_type", "call_operator")

# 호출하면 내장 타입(str/list/dict ...)이 반환되는 내장 함수. getattr/next/max/iter 처럼 무엇이든 돌려줄 수 있는 것은 뺀다
_BUILTIN_RET = {"str", "int", "float", "bool", "bytes", "bytearray", "list", "dict", "set", "frozenset", "tuple",
                "complex", "len", "open", "sorted", "repr", "input", "format", "chr", "ord", "hex", "oct", "bin",
                "range", "enumerate", "zip", "map", "filter", "reversed", "slice", "isinstance", "issubclass",
                "hasattr", "id", "hash", "any", "all", "callable", "ascii", "memoryview", "dir"}

# typing 계열 이름: 구체 내장 타입으로 볼 수 있는 것만 builtin, 나머지(Any, Callable, Iterator ...)는 타입을 모르는 것으로 취급
_TYPING_MODS = {"typing", "typing_extensions", "collections.abc"}
_TYPING_BUILTIN = {"List", "Dict", "Set", "FrozenSet", "Tuple", "Text"}
_TYPING_WRAPPERS = {"Final", "ClassVar", "Annotated", "Required", "NotRequired", "ReadOnly"}

# 표준 라이브러리(와 대표적 외부 패키지) 함수의 반환 타입표.  이름이 대문자로 시작하는 클래스 생성자
# (threading.Thread() 등)는 따로 표가 필요 없고, 여기에는 소문자 함수/클래스만 넣는다.
_S = lambda pkg: ("ext", "stdlib", pkg)
_STD_OBJ = {
    "re.compile": _S("re"), "re.match": _S("re"), "re.search": _S("re"), "re.fullmatch": _S("re"),
    "socket.socket": _S("socket"), "socket.create_connection": _S("socket"), "socket.socketpair": None,
    "collections.defaultdict": _S("collections"), "collections.deque": _S("collections"),
    "logging.getLogger": _S("logging"),
    "datetime.datetime.now": _S("datetime"), "datetime.datetime.utcnow": _S("datetime"),
    "datetime.datetime.today": _S("datetime"), "datetime.datetime.fromtimestamp": _S("datetime"),
    "datetime.datetime.strptime": _S("datetime"), "datetime.date.today": _S("datetime"),
    "urllib.parse.urlparse": _S("urllib"), "urllib.parse.urlsplit": _S("urllib"),
    "urllib.request.urlopen": _S("urllib"),
    "subprocess.run": _S("subprocess"),
    "ssl.create_default_context": _S("ssl"),
    "sqlite3.connect": _S("sqlite3"),
    "hashlib.md5": _S("hashlib"), "hashlib.sha1": _S("hashlib"), "hashlib.sha256": _S("hashlib"),
    "hashlib.sha512": _S("hashlib"), "hashlib.new": _S("hashlib"),
    "csv.reader": _S("csv"), "csv.writer": _S("csv"),
    "pathlib.Path": _S("pathlib"),
    "requests.get": ("ext", "third_party", "requests"), "requests.post": ("ext", "third_party", "requests"),
    "requests.put": ("ext", "third_party", "requests"), "requests.delete": ("ext", "third_party", "requests"),
    "requests.head": ("ext", "third_party", "requests"), "requests.patch": ("ext", "third_party", "requests"),
    "requests.request": ("ext", "third_party", "requests"), "requests.session": ("ext", "third_party", "requests"),
}
# 문자열/바이트/리스트처럼 내장 타입을 돌려주는 표준 라이브러리 함수
for _q in ("os.getcwd os.getenv os.listdir os.path.join os.path.basename os.path.dirname os.path.abspath "
           "os.path.realpath os.path.normpath os.path.expanduser os.path.splitext os.path.relpath "
           "json.dumps time.strftime time.ctime platform.system platform.platform platform.machine "
           "tempfile.mkdtemp tempfile.gettempdir getpass.getpass socket.gethostname socket.gethostbyname "
           "base64.b64encode base64.b64decode base64.urlsafe_b64encode base64.urlsafe_b64decode "
           "subprocess.check_output shlex.split shlex.quote textwrap.dedent textwrap.fill "
           "urllib.parse.quote urllib.parse.unquote urllib.parse.urlencode urllib.parse.urljoin "
           "string.capwords binascii.hexlify binascii.unhexlify").split():
    _STD_OBJ[_q] = _BUILTIN_T
del _q


def make_env(mod, imports, G):
    """G: 프로젝트 전체에서 공유하는 표 (top, methods, classes, class_bases, ret_types, modvar_types ...)"""
    return {"mod": mod, "imports": imports, "G": G, "cls": None, "shadow": frozenset(), "blocked": frozenset(),
            "known": {}, "src": {}, "self_types": None, "nested_vis": {}}


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _is_declared(t):
    return bool(t) and t[0] == "project" and len(t) > 2


def _declared(t):
    return (t[0], t[1], "declared") if t and t[0] == "project" else t


def _ext_type_of_name(name, env):
    imp = env["imports"].get(name)
    G = env["G"]
    if imp:
        origin, pkg = origin_of_import(imp, G["top"], G["project_roots"], G["module_imports"], G["module_vars"])
        return None if origin == "project" else ("ext", origin, pkg)
    if name in BUILTIN_NAMES:
        return _BUILTIN_T
    return None


def _typing_name(ann, env):
    """typing / typing_extensions / collections.abc 에서 가져온 이름이면 원래 이름, 아니면 None"""
    parts = dotted_parts(ann)
    if not parts:
        return None
    imp = env["imports"].get(parts[0])
    if not imp:
        return None
    if imp[0] == "name" and imp[1] in _TYPING_MODS:
        return imp[2] if len(parts) == 1 else None
    if imp[0] == "module" and imp[1] in _TYPING_MODS and len(parts) == 2:
        return parts[1]
    return None


def ann_to_type(ann, env):
    """타입힌트 표현식 -> 타입. 모호하면 None (Union[A, B] 처럼 둘 이상이면 포기)
    프로젝트 클래스는 'declared' 표시를 붙인다 (실제 객체는 자식 클래스일 수 있으므로)"""
    if ann is None:
        return None
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):          # "Foo" 문자열 타입힌트
        try:
            ann = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):          # X | None
        sides = [s for s in (ann.left, ann.right) if not _is_none(s)]
        return ann_to_type(sides[0], env) if len(sides) == 1 else None
    if isinstance(ann, ast.Subscript):
        base = ann.value
        nm = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else None)
        sl = ann.slice
        elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        if nm in ("Optional", "Union"):
            elts = [e for e in elts if not _is_none(e)]
            return ann_to_type(elts[0], env) if len(elts) == 1 else None
        if nm in _TYPING_WRAPPERS and _typing_name(base, env) == nm:           # Final[X], ClassVar[X], Annotated[X, ...]
            return ann_to_type(elts[0], env) if elts else None
        ann = base                                                              # List[Foo] 등은 바깥 타입만 본다
    tn = _typing_name(ann, env)
    if tn is not None:
        if tn == "Self" and env.get("cls") and (env["mod"], env["cls"]) in env["G"]["classes"]:
            return ("project", env["G"]["classes"][(env["mod"], env["cls"])], "declared")
        return _BUILTIN_T if tn in _TYPING_BUILTIN else None                    # Any, Callable, Iterator ... 는 모른다
    ref = resolve_class_ref(ann, env["mod"], env["imports"], env["G"]["top"], env["G"]["classes"])
    if ref:
        return ("project", ref, "declared")
    root = root_of(ann)
    if isinstance(root, ast.Name):
        if root.id in ("object", "type") and root.id not in env["imports"]:
            return None                                                         # 무엇이든 될 수 있다
        return _ext_type_of_name(root.id, env)
    return None


def _ann_site_type(node, env):
    """`x: T = v` 의 타입. `x: Final = v` 처럼 타입이 비어 있으면 값에서 알아낸다"""
    t = ann_to_type(node.annotation, env)
    if t is None and node.value is not None:
        base = node.annotation.value if isinstance(node.annotation, ast.Subscript) else node.annotation
        if _typing_name(base, env) in ("Final", "ClassVar") and not isinstance(node.annotation, ast.Subscript):
            return value_to_type(node.value, env)
    return t


# ---------------------------------------------------------------------
# 식(expression)의 타입
# ---------------------------------------------------------------------
def var_type(name, env):
    """모듈 수준 변수(같은 모듈 것 또는 가져온 것)의 타입. 함수 안에서 같은 이름을 바인딩하면 쓰지 않는다"""
    if name in env["blocked"]:
        return None
    G = env["G"]
    mv = G["modvar_types"]
    if (env["mod"], name) in mv:
        return mv[(env["mod"], name)]
    imp = env["imports"].get(name)
    if imp and imp[0] == "name":
        return mv.get((imp[1], imp[2]))
    return None


def ext_qualname(func, env):
    """호출 대상이 프로젝트 밖 함수/클래스이면 'os.path.join' 처럼 정식 이름을 돌려준다"""
    parts = dotted_parts(func)
    if not parts or parts[0] in env["shadow"]:
        return None
    imp = env["imports"].get(parts[0])
    if not imp:
        return None
    G = env["G"]
    origin, _ = origin_of_import(imp, G["top"], G["project_roots"], G["module_imports"], G["module_vars"])
    if origin == "project":
        return None
    base = imp[1] if imp[0] == "module" else _join_mod(imp[1], imp[2])
    return _join_mod(base, *parts[1:])


def project_call_ret(call, env):
    """프로젝트 함수/메서드를 호출한 결과의 타입 = 그 함수의 반환 타입힌트 (async·데코레이터 함수는 제외됨)"""
    G, f = env["G"], call.func
    callee = None
    if isinstance(f, ast.Name) and f.id in env.get("nested_vis", {}):         # 바깥 함수 안에 정의된 중첩 함수 호출
        return G["ret_types"].get(env["nested_vis"][f.id])
    if isinstance(f, ast.Attribute):
        rt = expr_type(f.value, env)
        if rt and rt[0] == "project":                              # 수신 객체의 타입을 아는 경우: 그 클래스(와 부모)에서 찾는다
            callee = find_method_up_chain(rt[1][0], rt[1][1], f.attr, G["methods"], G["class_bases"])
            return G["ret_types"].get(callee) if callee else None
    root = dotted_parts(f)
    callee = resolve_call(f, env["mod"], env["cls"], env["imports"], G["top"], G["methods"], G["class_bases"],
                          {}, env["shadow"])
    if not callee and root and len(root) > 1 and root[0] not in env["shadow"]:
        callee = resolve_dotted(f, env["imports"], G["top"], G["classes"], G["methods"], G["class_bases"],
                                G["modvar_types"])
    return G["ret_types"].get(callee) if callee else None


def call_result_type(call, env):
    """호출 결과의 타입: 프로젝트 클래스 생성 / 내장 타입 생성 / 프로젝트 함수의 반환 타입힌트 / 표준 라이브러리 반환표"""
    G, f = env["G"], call.func
    parts = dotted_parts(f)
    shadowed = bool(parts) and parts[0] in env["shadow"]          # 지역 변수가 같은 이름의 클래스/함수를 가림
    if not shadowed:
        ref = resolve_class_ref(f, env["mod"], env["imports"], G["top"], G["classes"])
        if ref:
            return ("project", ref)
        if parts and len(parts) == 1 and parts[0] in BUILTIN_NAMES and parts[0] not in env["imports"]:
            return _BUILTIN_T if parts[0] in _BUILTIN_RET else None   # open(...), dict(...), len(...) 등
    t = project_call_ret(call, env)
    if t:
        return t
    if shadowed:
        return None
    q = ext_qualname(f, env)
    if q and q in _STD_OBJ:
        return _STD_OBJ[q]
    if parts and parts[-1][:1].isupper():                          # 대문자로 시작하면 클래스 생성자로 본다 (argparse.ArgumentParser())
        return _ext_type_of_name(parts[0], env)                    # 소문자 함수(os.getcwd 등)는 위 표에 없으면 포기
    return None


def expr_type(node, env):
    if isinstance(node, _VALUE_BUILTIN_NODES):
        return _BUILTIN_T
    if isinstance(node, ast.Name):
        return env["known"].get(node.id) or var_type(node.id, env)
    if isinstance(node, ast.Attribute):
        if env["cls"] and env["self_types"] and isinstance(node.value, ast.Name) and node.value.id == "self":
            return env["self_types"].get(node.attr)
        return None
    if isinstance(node, ast.Call):
        return call_result_type(node, env)
    return None


def typed_expr(node, env):
    """수신 객체 표현식의 (타입, 근거). 근거: type_hint | local_assign | self_attr | module_var | constructor | return_type"""
    if isinstance(node, ast.Name):
        t = env["known"].get(node.id)
        if t:
            return t, env["src"].get(node.id, "local_assign")
        t = var_type(node.id, env)
        return (t, "module_var") if t else (None, None)
    if isinstance(node, ast.Attribute):
        t = expr_type(node, env)
        return (t, "self_attr") if t else (None, None)
    if isinstance(node, ast.Call):
        G = env["G"]
        dp = dotted_parts(node.func)
        if not (dp and dp[0] in env["shadow"]):
            ref = resolve_class_ref(node.func, env["mod"], env["imports"], G["top"], G["classes"])
            if ref:
                return ("project", ref), "constructor"
        t = call_result_type(node, env)
        return (t, "return_type") if t else (None, None)
    return None, None


def value_to_type(val, env):
    """대입되는 값의 타입"""
    if isinstance(val, _VALUE_BUILTIN_NODES):
        return _BUILTIN_T
    if isinstance(val, (ast.Name, ast.Attribute, ast.Call)):
        return expr_type(val, env)
    return None


def _t_key(t):
    return t[:2] if t and t[0] == "project" else t


def _merge(types):
    """모든 대입이 같은 타입일 때만 그 타입. 하나라도 모르거나 다르면 None.
    같은 프로젝트 클래스인데 선언 여부만 다르면 '선언된 타입'(더 넓은 쪽)으로 본다"""
    ts = [t for t in types if t != _NEUTRAL]
    if not ts or any(t is None for t in ts):
        return None
    if all(_t_key(t) == _t_key(ts[0]) for t in ts):
        return _declared(ts[0]) if any(_is_declared(t) for t in ts) else ts[0]
    return None


def _all_args(fn):
    a = fn.args
    out = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
    if a.vararg:
        out.append(a.vararg)
    if a.kwarg:
        out.append(a.kwarg)
    return out


def _site_type(value, env):
    return _NEUTRAL if _is_none(value) else value_to_type(value, env)


def collect_name_types(fn, env, cls=None, self_types=None, inherited=None):
    """함수 안의 이름(매개변수·지역변수) -> 타입.  반환: (types, src, shadow, fenv)
    src: 'type_hint' | 'local_assign',  shadow: import 한 이름을 가릴 수 있는 지역 이름 전체
    fenv: 이 함수 안의 식을 해석할 때 쓰는 환경(확정된 타입, shadow 등을 담고 있다)
    inherited: 중첩 함수일 때 바깥 함수의 (타입, 근거, shadow). 바깥 함수의 변수를 안쪽에서 쓰는 경우(클로저)를 위해 물려받는다
               (안쪽에서 같은 이름을 따로 바인딩하면 물려받지 않는다)"""
    e = dict(env)
    e["cls"], e["self_types"] = cls, self_types
    a = fn.args
    stores, tainted = Counter(), set()
    imported_here = set()
    for node in ast.walk(fn):
        if node is fn:
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            stores[node.id] += 1
        elif isinstance(node, FUNC_TYPES + (ast.Lambda,)):                 # 중첩 함수의 인자와 이름이 겹치면 믿지 않는다
            tainted.update(x.arg for x in _all_args(node))
            if not isinstance(node, ast.Lambda):
                tainted.add(node.name)
        elif isinstance(node, ast.ClassDef):
            tainted.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            tainted.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names_ = [(al.asname or al.name).split(".")[0] for al in node.names]
            tainted.update(names_)
            imported_here.update(names_)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            tainted.update(node.names)
        elif type(node).__name__ in ("MatchAs", "MatchStar") and getattr(node, "name", None):
            tainted.add(node.name)
        elif type(node).__name__ == "MatchMapping" and getattr(node, "rest", None):
            tainted.add(node.rest)

    pnames = [x.arg for x in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)]
    if a.vararg:
        pnames.append(a.vararg.arg)
    if a.kwarg:
        pnames.append(a.kwarg.arg)
    shadow = set(pnames) | set(stores) | (tainted - imported_here)        # import 한 이름을 가리는 지역 이름들
    outer_shadow = set(inherited[2]) if inherited else set()
    e["shadow"] = frozenset(shadow | outer_shadow)
    e["blocked"] = frozenset(shadow | tainted | outer_shadow)
    inh_t = {k: v for k, v in (inherited[0] if inherited else {}).items() if k not in shadow and k not in tainted}
    inh_s = {k: inherited[1].get(k, "local_assign") for k in inh_t} if inherited else {}
    params = {}
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        params[arg.arg] = ann_to_type(arg.annotation, e)
    if a.vararg:
        params[a.vararg.arg] = _BUILTIN_T                  # *args 는 항상 tuple
    if a.kwarg:
        params[a.kwarg.arg] = _BUILTIN_T                   # **kwargs 는 항상 dict
    params_known = {k: v for k, v in params.items() if v}

    types, src = {}, {}
    for _ in range(3):                                      # `a = Foo(); b = a.make()` 처럼 앞에서 정한 타입으로 뒤를 정하기 위해 반복
        e["known"] = {**inh_t, **params_known, **types}
        e["src"] = {**inh_s, **src}
        sites = defaultdict(list)
        for node in ast.walk(fn):                                           # '타입이 보이는' 단순 대입 지점
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                sites[node.targets[0].id].append(_site_type(node.value, e))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                sites[node.target.id].append(_ann_site_type(node, e))
            elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
                sites[node.target.id].append(_NEUTRAL)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for it in node.items:
                    if isinstance(it.optional_vars, ast.Name):
                        t = value_to_type(it.context_expr, e)
                        sites[it.optional_vars.id].append(None if (t and t[0] == "project") else t)   # __enter__ 반환값은 모른다
        new_types, new_src = {}, {}
        for name in set(params) | set(sites):
            if name in tainted:
                continue
            s = sites.get(name, [])
            if stores[name] != len(s):                      # 단순 대입이 아닌 바인딩(루프 변수, 튜플 풀기 등)이 섞여 있음
                continue
            if name in params:
                if params[name] is None:
                    continue
                cand = [params[name]] + s
            else:
                cand = s
            t = _merge(cand)
            if t:
                new_types[name] = t
                new_src[name] = "type_hint" if name in params else "local_assign"
        if new_types == types:
            break
        types, src = new_types, new_src
    types, src = {**inh_t, **types}, {**inh_s, **src}
    e["known"], e["src"] = types, src
    return types, src, shadow, e


def _ancestors(key, class_bases):
    seen, stack = set(), list(class_bases.get(key, []))
    while stack:
        k = stack.pop()
        if k not in seen:
            seen.add(k)
            stack.extend(class_bases.get(k, []))
    return seen


def class_self_types(key, class_nodes, class_bases, all_desc, methods, envs):
    """self.속성 -> 타입.  같은 상속 가족(조상+자손)의 모든 대입이 일치할 때만 확정"""
    family = {key} | all_desc(key) | _ancestors(key, class_bases)
    stores, sites, method_names = Counter(), defaultdict(list), set()
    for member in family:
        node = class_nodes.get(member)
        if node is None:
            continue
        env = dict(envs[member[0]])
        env["cls"] = member[1]
        method_names |= set(methods.get(member, {}))
        for st in node.body:                                                # 클래스 본문의 `x = Foo()`, `x: Foo`
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                stores[st.targets[0].id] += 1
                sites[st.targets[0].id].append(_site_type(st.value, env))
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                stores[st.target.id] += 1
                sites[st.target.id].append(_ann_site_type(st, env))
        for fn in node.body:
            if not isinstance(fn, FUNC_TYPES):
                continue
            _, _, _, fenv = collect_name_types(fn, envs[member[0]], cls=member[1])      # self.속성 타입은 아직 모르는 상태로 시작
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self" \
                        and isinstance(n.ctx, (ast.Store, ast.Del)):
                    stores[n.attr] += 1
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Attribute) \
                        and isinstance(n.targets[0].value, ast.Name) and n.targets[0].value.id == "self":
                    sites[n.targets[0].attr].append(_site_type(n.value, fenv))
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute) \
                        and isinstance(n.target.value, ast.Name) and n.target.value.id == "self":
                    sites[n.target.attr].append(_ann_site_type(n, fenv))
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute) \
                        and isinstance(n.target.value, ast.Name) and n.target.value.id == "self":
                    sites[n.target.attr].append(_NEUTRAL)
    out = {}
    for attr, s in sites.items():
        if attr in method_names or stores[attr] != len(s):
            continue
        t = _merge(s)
        if t:
            out[attr] = t
    return out


def resolve_by_type(func, env):
    """v2 방식으로 못 푼 호출을, 추적한 타입으로 한 번 더 시도. 반환: (호출대상, 근거, 수신 클래스, 선언 타입 여부)
    수신 객체는 변수 / self.속성 / 모듈 변수 / 생성자 호출 / 프로젝트 함수 호출 결과(반환 타입힌트) 중 하나"""
    if not isinstance(func, ast.Attribute):
        return None, None, None, False
    t, via = typed_expr(func.value, env)
    if t and t[0] == "project":
        G = env["G"]
        found = find_method_up_chain(t[1][0], t[1][1], func.attr, G["methods"], G["class_bases"])
        if found:
            return found, via, t[1], _is_declared(t)
    return None, None, None, False


# =====================================================================
# 5-3. 반환 타입힌트 / 모듈 수준 변수
# =====================================================================
_SAFE_DECORATORS = {"staticmethod", "classmethod", "abstractmethod"}


def _decorators_safe(fn):
    for d in fn.decorator_list:
        nm = d.id if isinstance(d, ast.Name) else (d.attr if isinstance(d, ast.Attribute) else None)
        if nm not in _SAFE_DECORATORS:
            return False                                    # property / 임의 데코레이터는 반환값이 달라질 수 있어 믿지 않는다
    return True


def _local_bound_names(fn, skip_ids):
    """함수 안에서 바인딩되는 모든 이름 (매개변수, 대입, import, for, with, 중첩 정의 ...)"""
    names = set()
    a = fn.args
    for arg in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]:
        names.add(arg.arg)
    for x in own_walk(fn, skip_ids):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            names.add(x.id)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            names.update((al.asname or al.name).split(".")[0] for al in x.names)
        elif isinstance(x, (ast.Global, ast.Nonlocal)):
            names.update(x.names)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            names.add(x.name)
        for ch in ast.iter_child_nodes(x):
            if isinstance(ch, FUNC_TYPES + (ast.ClassDef,)):
                names.add(ch.name)
    return names


def _infer_body_return(d, env, skip_ids):
    """반환 타입힌트가 없는 함수의 타입을 return 문에서 알아낸다.  모든 return 이 같은 타입일 때만 (None 반환은 Optional 처럼 봄)
    허용하는 값: `Foo(...)`, `self`, `cls(...)`, 이미 타입을 아는 프로젝트 함수 호출, 리터럴.  지역 변수·알 수 없는 값이 하나라도 있으면 포기"""
    fn = d["node"]
    bound = _local_bound_names(fn, skip_ids)
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    kind = _method_kind(fn) if d["cls"] else None
    first = params[0] if params and kind in ("instance", "class") else None
    e = dict(env)
    e["cls"] = d["cls"]
    e["shadow"] = frozenset(bound - {first} if first else bound)
    e["blocked"] = frozenset(bound)
    cls_t = ("project", env["G"]["classes"][(d["mod"], d["cls"])], "declared") if d["cls"] and \
        (d["mod"], d["cls"]) in env["G"]["classes"] else None
    types, seen_return = [], False
    for x in own_walk(fn, skip_ids):
        if isinstance(x, (ast.Yield, ast.YieldFrom)):
            return None
        if not isinstance(x, ast.Return):
            continue
        v = x.value
        if v is None or (isinstance(v, ast.Constant) and v.value is None):
            types.append(_NEUTRAL)
            continue
        seen_return = True
        if isinstance(v, ast.Name) and first and v.id == first and kind == "instance":
            types.append(cls_t)                                         # return self
        elif isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and first and v.func.id == first \
                and kind == "class":
            types.append(cls_t)                                         # return cls(...)
        else:
            types.append(value_to_type(v, e))
    if not seen_return:
        return None
    t = _merge(types)
    return t if t and t != _NEUTRAL else None


def collect_return_types(defs, envs, G=None):
    """프로젝트 함수/메서드의 반환 타입: 노드 ID -> 타입.
    1) 반환 타입힌트  2) (G 를 넘기면) 힌트가 없는 함수는 본문의 return 문에서 추론 (G["inferred_returns"] 에 기록)
    async 함수(코루틴을 돌려줌), 데코레이터가 붙은 함수, 같은 이름이 둘 이상 정의된 함수, 제너레이터는 제외"""
    ret, dup = {}, Counter()
    for d in defs:
        fn, nid = d["node"], d["id"]
        dup[nid] += 1
        if not isinstance(fn, ast.FunctionDef) or fn.returns is None or not _decorators_safe(fn):
            continue
        if fn.name == "__init__":
            continue
        e = dict(envs[d["mod"]])
        e["cls"] = d["cls"]
        t = ann_to_type(fn.returns, e)
        if t:
            ret[nid] = t
    ret = {k: v for k, v in ret.items() if dup[k] == 1}
    if G is None:
        return ret
    G["inferred_returns"] = set()
    G["ret_types"] = ret
    skip_ids = {id(d["node"]) for d in defs}
    for _ in range(4):                                              # 다른 함수의 반환 타입에 기대는 경우를 위해 되풀이
        changed = False
        for d in defs:
            fn, nid = d["node"], d["id"]
            if nid in ret or dup[nid] != 1 or not isinstance(fn, ast.FunctionDef) or fn.returns is not None \
                    or fn.name == "__init__" or not _decorators_safe(fn):
                continue
            t = _infer_body_return(d, envs[d["mod"]], skip_ids)
            if t:
                ret[nid] = t
                G["inferred_returns"].add(nid)
                changed = True
        if not changed:
            break
    return ret


def collect_module_var_defs(files):
    """모듈 최상위에서 딱 한 번, 단순 대입으로만 정의된 변수: mod -> {이름: (값 노드, 타입힌트 또는 None)}
    그 외 방식으로도 바인딩되는 이름(재대입, import, for, with, def/class, global 수정 ...)은 제외"""
    out = {}
    for mod, (rel, tree) in files.items():
        stores, simple = Counter(), {}
        for node in _scope_nodes(tree.body):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                stores[node.id] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for al in node.names:
                    stores[(al.asname or al.name).split(".")[0]] += 2
            elif isinstance(node, FUNC_TYPES + (ast.ClassDef,)):
                stores[node.name] += 2
            elif isinstance(node, ast.ExceptHandler) and node.name:
                stores[node.name] += 2
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                simple[n.targets[0].id] = (n.value, None)
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value is not None:
                simple[n.target.id] = (n.value, n.annotation)
        global_written = {nm for n in ast.walk(tree) if isinstance(n, ast.Global) for nm in n.names}
        out[mod] = {k: v for k, v in simple.items() if stores[k] == 1 and k not in global_written}
    return out


def _scope_nodes(body):
    """한 스코프(모듈)에 속한 노드. 함수·클래스·람다는 이름만 보고 안으로 들어가지 않는다"""
    stack = list(body)
    while stack:
        n = stack.pop()
        yield n
        if isinstance(n, FUNC_TYPES + (ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def collect_module_var_types(var_defs, envs, G):
    """(모듈, 변수) -> 타입.  `codes = LookupDict(...)`, `_re = re.compile(...)`, `X: Foo = ...` 등"""
    G["modvar_types"] = {}
    for _ in range(2):                                      # 변수가 다른 변수/함수 호출 결과에 의존하는 경우를 위해 두 번
        for mod, defs in var_defs.items():
            e = dict(envs[mod])
            for name, (value, ann) in defs.items():
                if (mod, name) in G["modvar_types"]:
                    continue
                if ann is not None:
                    t = _ann_site_type(ast.AnnAssign(target=ast.Name(id=name), annotation=ann, value=value,
                                                     simple=1), e)
                else:
                    t = value_to_type(value, e)
                if t and t != _NEUTRAL:
                    G["modvar_types"][(mod, name)] = t
    return G["modvar_types"]


def inject_var_exports(exports, modvar_types):
    """`from .status_codes import codes` 처럼 다시 내보내진 변수도 같은 타입으로 접근할 수 있게 한다"""
    for (mod, name), target in exports.items():
        if target in modvar_types:
            modvar_types.setdefault((mod, name), modvar_types[target])


# =====================================================================
# 6. 호출 대상 해석 (프로젝트 안에서 찾을 수 있는 것)
# =====================================================================
def resolve_call(func, mod, cls, imports, top, methods, class_bases, local_vars, shadow=frozenset()):
    """shadow: 이 함수 안에서 따로 바인딩된 이름들. 같은 이름의 모듈/클래스/함수를 가리므로 그쪽으로 해석하지 않는다"""
    if isinstance(func, ast.Name):
        if func.id in shadow:
            return None
        if func.id in top[mod]:
            return top[mod][func.id]
        imp = imports.get(func.id)
        if imp and imp[0] == "name":
            return top.get(imp[1], {}).get(imp[2])

    elif isinstance(func, ast.Attribute):
        # super().method() : 현재 클래스가 아니라 부모부터 찾는다
        if isinstance(func.value, ast.Call) and isinstance(func.value.func, ast.Name) \
                and func.value.func.id == "super" and cls:
            for k in class_mro(mod, cls, class_bases)[1:]:                  # 자기 다음 순서부터 (MRO)
                found = methods.get(k, {}).get(func.attr)
                if found:
                    return found
            return None

        if not isinstance(func.value, ast.Name):
            return None
        obj, attr = func.value.id, func.attr

        if obj == "self" and cls:
            return find_method_up_chain(mod, cls, attr, methods, class_bases)
        if obj in local_vars:
            vm, vc = local_vars[obj]
            found = find_method_up_chain(vm, vc, attr, methods, class_bases)
            if found:
                return found
        if obj in shadow:
            return None
        if obj in top.get(mod, {}) and (mod, obj) in methods:          # ParentClass.__init__(self) 등
            return find_method_up_chain(mod, obj, attr, methods, class_bases)

        imp = imports.get(obj)
        if imp and imp[0] == "module":
            return top.get(imp[1], {}).get(attr)
        if imp and imp[0] == "name":
            sub = f"{imp[1]}.{imp[2]}" if imp[1] else imp[2]
            if sub in top:
                return top[sub].get(attr)
            return find_method_up_chain(imp[1], imp[2], attr, methods, class_bases)
    return None


# =====================================================================
# 7. 해석 못 한 호출을 '왜 못 했는지'로 분류
# =====================================================================
#  외부(확실):  builtin / stdlib / third_party / inherited_external
#  외부(추정):  builtin_type_method   (변수 타입은 모르지만 str·list·dict 메서드 이름이라 그렇게 추정)
#  미확정:      unknown_receiver      (변수 타입을 알아야 함 = 진짜 타입 추론 영역)
#               local_callable        (매개변수/중첩함수/변수에 담긴 함수를 호출)
#               project_unresolved    (프로젝트 안 이름인데 못 찾음 = 놓친 것일 가능성 높음)
EXTERNAL_CATS = ("builtin", "stdlib", "third_party", "inherited_external", "builtin_type_method")
CAT_LABEL = {
    "builtin": "내장 함수/예외 (print, len, ValueError ...)",
    "stdlib": "표준 라이브러리 (os, re, urllib ...)",
    "third_party": "외부 패키지 (urllib3, dns ...)",
    "inherited_external": "외부 부모 클래스에게 물려받은 메서드",
    "builtin_type_method": "str/list/dict 메서드로 추정 (변수 타입 모름)",
    "unknown_receiver": "변수 타입을 알아야 해석 가능 (타입 추론 영역)",
    "local_callable": "매개변수/중첩함수/변수에 담긴 함수 호출",
    "project_unresolved": "프로젝트 안 이름인데 못 찾음 (점검 필요)",
}


def root_of(expr):
    """a.b(c).d[e].f  ->  맨 뿌리 노드(a)"""
    while True:
        if isinstance(expr, ast.Attribute):
            expr = expr.value
        elif isinstance(expr, ast.Call):
            expr = expr.func
        elif isinstance(expr, ast.Subscript):
            expr = expr.value
        else:
            return expr


def origin_of_import(imp, top, project_roots, module_imports=None, module_vars=None):
    """import 항목 -> ('project' | 'stdlib' | 'third_party' | 'builtin', 최상위 패키지명)
    프로젝트 안 모듈이 다른 곳에서 가져온 이름을 다시 내보내는 경우(re-export)는 끝까지 따라간다."""
    module_imports = module_imports or {}
    module_vars = module_vars or {}
    for _ in range(6):                                   # 재수출 단계는 보통 1~2단계
        modpath, level = imp[1], (imp[3] if imp[0] == "name" else 0)
        first = modpath.split(".")[0] if modpath else ""
        is_project = level > 0 or modpath == "" or modpath in top or first in project_roots
        if not is_project:
            return ("stdlib", first) if is_stdlib(first) else ("third_party", first)
        if imp[0] == "name":
            nxt = module_imports.get(modpath, {}).get(imp[2])
            if nxt and nxt != imp:
                imp = nxt
                continue
            if module_vars.get((modpath, imp[2])) == "builtin":
                return "builtin", "builtins"
        return "project", first or "."
    return "project", "."


def _short(text, n=80):
    return text if len(text) <= n else text[:n - 3] + "..."


def classify_unresolved(func, ctx):
    """반환: (category, package, detail, confidence)"""
    mod, cls = ctx["mod"], ctx["cls"]
    imports, top = ctx["imports"], ctx["top"]
    expr = _short(ast.unparse(func))

    def from_import(imp):
        origin, pkg = origin_of_import(imp, top, ctx["project_roots"], ctx["module_imports"], ctx["module_vars"])
        cat = "project_unresolved" if origin == "project" else origin
        return cat, pkg

    def ext_parent(m, c, label):
        ext = chain_ext_bases(m, c, ctx["class_bases"], ctx["class_ext_bases"])
        if ext:
            return ("inherited_external", ext[0].split(".")[0], f"{label} <- 부모 {', '.join(ext)}", "certain")
        return ("project_unresolved", None, label, "unknown")

    def unknown_receiver(attr):
        if attr in BUILTIN_TYPE_METHODS and attr not in ctx["project_method_names"]:
            return ("builtin_type_method", "builtins", expr, "weak" if attr in AMBIGUOUS_NAMES else "heuristic")
        return ("unknown_receiver", None, expr, "unknown")

    # --- 이름만 있는 호출: foo(...)
    if isinstance(func, ast.Name):
        n = func.id
        if n in ctx["shadow"]:
            return ("local_callable", None, expr, "unknown")             # 매개변수·지역 변수가 같은 이름을 가림
        if n in imports:
            cat, pkg = from_import(imports[n])
            return (cat, pkg, expr, "certain")
        if n in BUILTIN_NAMES:
            return ("builtin", "builtins", expr, "certain")
        return ("local_callable", None, expr, "unknown")

    # --- 점으로 이어진 호출: a.b(...)
    if isinstance(func, ast.Attribute):
        attr, v = func.attr, func.value

        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "super" and cls:
            ext = chain_ext_bases(mod, cls, ctx["class_bases"], ctx["class_ext_bases"])
            if ext:
                return ("inherited_external", ext[0].split(".")[0], f"super().{attr} <- 부모 {', '.join(ext)}", "certain")
            return ("builtin", "builtins", f"object.{attr}", "certain")     # 부모가 없으면 object

        root = root_of(v)
        if isinstance(root, (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr,
                             ast.ListComp, ast.DictComp, ast.SetComp)):
            return ("builtin", "builtins", expr, "certain")                 # "abc".join(...), [].append(...)
        if not isinstance(root, ast.Name):
            return unknown_receiver(attr)

        name = root.id
        env = ctx["env"]

        def by_type(tinfo):
            if tinfo[0] == "ext":
                return (tinfo[1], tinfo[2], expr, "inferred")
            res = ext_parent(tinfo[1][0], tinfo[1][1], expr)
            return (res[0], res[1], res[2], "inferred" if res[0] == "inherited_external" else res[3])

        tinfo = None                                                         # 타입을 추적해 둔 변수/속성/모듈 변수이면 그 타입으로 분류
        if v is root:
            tinfo = typed_expr(v, env)[0]
        elif cls and isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
            tinfo = typed_expr(v, env)[0]
        if tinfo:
            return by_type(tinfo)

        if name == "self" and cls:
            if v is root:                                                    # self.method(...)
                ext = chain_ext_bases(mod, cls, ctx["class_bases"], ctx["class_ext_bases"])
                if ext:
                    return ("inherited_external", ext[0].split(".")[0], f"self.{attr} <- 부모 {', '.join(ext)}", "certain")
                return ("local_callable", None, expr, "unknown")             # self.콜백() 처럼 속성에 담긴 함수
            if not isinstance(v, ast.Attribute):                             # self.make().method(...) : 반환 타입힌트로 알 수 있는 경우
                tinfo = typed_expr(v, env)[0]
                if tinfo:
                    return by_type(tinfo)
            return unknown_receiver(attr)                                    # self.속성.method(...)
        if name in imports and name not in ctx["shadow"]:
            cat, pkg = from_import(imports[name])
            if cat == "project_unresolved" and isinstance(v, ast.Call):      # 프로젝트 함수 호출 결과에 붙은 메서드 : 반환 타입을 알아야 함
                tinfo = typed_expr(v, env)[0]
                return by_type(tinfo) if tinfo else unknown_receiver(attr)
            return (cat, pkg, expr, "certain")
        if v is not root:                                                    # a.b().c() 처럼 호출 결과에 붙은 메서드
            if isinstance(v, ast.Call):
                tinfo = typed_expr(v, env)[0]
                if tinfo:
                    return by_type(tinfo)
            return unknown_receiver(attr)
        if name in ctx["local_vars"]:
            vm, vc = ctx["local_vars"][name]
            return ext_parent(vm, vc, expr)
        if name in top[mod] and (mod, name) in ctx["methods"]:
            return ext_parent(mod, name, expr)
        if name in BUILTIN_NAMES:
            return ("builtin", "builtins", expr, "certain")
        return unknown_receiver(attr)

    return ("local_callable", None, expr, "unknown")                         # foo()(), handlers[k](x) 등


# =====================================================================
# 8. 모든 함수 안의 호출을 수집
# =====================================================================
_BINOPS = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "truediv", ast.FloorDiv: "floordiv",
           ast.Mod: "mod", ast.Pow: "pow", ast.MatMult: "matmul", ast.LShift: "lshift", ast.RShift: "rshift",
           ast.BitAnd: "and", ast.BitOr: "or", ast.BitXor: "xor"}
_CMPOPS = {ast.Eq: ("__eq__", "__eq__"), ast.NotEq: ("__ne__", "__ne__"), ast.Lt: ("__lt__", "__gt__"),
           ast.LtE: ("__le__", "__ge__"), ast.Gt: ("__gt__", "__lt__"), ast.GtE: ("__ge__", "__le__")}
_UNARY = {ast.USub: "__neg__", ast.UAdd: "__pos__", ast.Invert: "__invert__"}
_PROTOCOL_CALLS = {"len": ["__len__"], "str": ["__str__"], "repr": ["__repr__"], "iter": ["__iter__"],
                   "next": ["__next__"], "hash": ["__hash__"], "abs": ["__abs__"], "int": ["__int__"],
                   "float": ["__float__"], "bool": ["__bool__", "__len__"], "bytes": ["__bytes__"],
                   "format": ["__format__", "__str__"], "reversed": ["__reversed__"], "round": ["__round__"],
                   "print": ["__str__"]}
_PROPERTY_DECORATORS = {"property", "cached_property", "setter", "getter", "deleter"}


def _bind_counts(fn, skip_ids, nested_children):
    """fn 의 자기 범위에서 이름이 몇 번 바인딩되는지 (매개변수, 대입, import, def/class, except as)"""
    c = Counter()
    a = fn.args
    for x in _all_args(fn):
        c[x.arg] += 1
    for n in own_walk(fn, skip_ids):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            c[n.id] += 1
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                c[(al.asname or al.name).split(".")[0]] += 1
        elif isinstance(n, ast.ClassDef):
            c[n.name] += 1
        elif isinstance(n, ast.ExceptHandler) and n.name:
            c[n.name] += 1
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            for nm in n.names:
                c[nm] += 2
    for ch in nested_children:
        c[ch["node"].name] += 1
    return c


def collect_calls(files, top, methods, classes, class_bases, class_ext_bases, root_name, module_imports,
                  var_defs=None, exports=None, defs=None, class_nodes=None, nodes=None, stats=None):
    """모든 함수(중첩 함수·중첩 클래스의 메서드 포함) 안의 호출을 수집한다.
    반환: edges(호출 간선), 외부 호출, 미확정 호출, extra(호출이 아닌 참조·암묵 호출 간선)"""
    module_vars = collect_module_vars(files, var_defs or {})
    edges, unresolved, extra = [], [], {}
    ext_agg = {}
    all_desc = build_descendants(class_bases)
    project_roots = {m.split(".")[0] for m in files if m} | {root_name}
    project_method_names = Counter(name for ms in methods.values() for name in ms)
    G = {"top": top, "methods": methods, "classes": classes, "class_bases": class_bases,
         "project_roots": project_roots, "module_imports": module_imports, "module_vars": module_vars,
         "ret_types": {}, "modvar_types": {}}
    envs = {m: make_env(m, module_imports[m], G) for m in files}
    G["ret_types"] = collect_return_types(defs, envs, G)                     # 반환 타입힌트 + 본문 return 에서 추론
    if stats is not None:
        stats["return_types_inferred"] = len(G["inferred_returns"])
    collect_module_var_types(var_defs or {}, envs, G)                        # 모듈 수준 변수 타입
    inject_var_exports(exports or {}, G["modvar_types"])
    self_type_cache = {}

    def self_types_of(mod, cls):
        key = (mod, cls)
        if key not in self_type_cache:
            self_type_cache[key] = class_self_types(key, class_nodes, class_bases, all_desc, methods, envs)
        return self_type_cache[key]

    skip_ids = {id(d["node"]) for d in defs}
    children = defaultdict(list)                                             # 함수 -> 그 안에 정의된 중첩 함수들
    for d in defs:
        if d["parent_is_func"]:
            children[d["parent"]].append(d)
    prop_ids = {d["id"] for d in defs if {_dec_base(x) for x in d["node"].decorator_list} & _PROPERTY_DECORATORS}
    class_key = {f"{files[m][0]}::{q}": (m, q) for (m, q) in class_nodes}
    attr_names = set(project_method_names) | {n for t in top.values() for n in t}
    state = {}

    def add_extra(frm, to, etype, via, line):
        k = (frm, to, etype, via)
        if k not in extra:
            extra[k] = {"from": frm, "to": to, "type": etype, "via": via, "count": 0, "line": line}
        extra[k]["count"] += 1

    for d in defs:
        mod, fn, cls, caller = d["mod"], d["node"], d["cls"], d["id"]
        imports = module_imports[mod]
        parent = state.get(d["parent"]) if d["parent_is_func"] else None
        local_vars = collect_local_var_types(fn, mod, imports, top, classes)
        self_types = self_types_of(mod, cls) if cls else {}
        inherited = (parent["types"], parent["src"], parent["shadow"]) if parent else None
        name_types, name_src, own_shadow, fenv = collect_name_types(fn, envs[mod], cls, self_types, inherited)
        if parent:                                                           # 바깥 함수의 `x = Foo()` 는 안쪽에서 같은 이름을 따로 바인딩하지 않을 때만 물려받는다
            local_vars = {**{k: v for k, v in parent["local_vars"].items() if k not in own_shadow}, **local_vars}
        shadow = fenv["shadow"]
        vis = {k: v for k, v in parent["vis"].items() if k not in own_shadow} if parent else {}
        if children.get(caller):                                             # 한 번만 바인딩된 중첩 함수 이름은 호출을 풀 수 있다
            binds = _bind_counts(fn, skip_ids, children[caller])
            for ch in children[caller]:
                if binds[ch["node"].name] == 1:
                    vis[ch["node"].name] = ch["id"]
        fenv["nested_vis"] = vis
        state[caller] = {"local_vars": local_vars, "types": name_types, "src": name_src, "shadow": shadow, "vis": vis}
        ctx = {"mod": mod, "cls": cls, "imports": imports, "top": top, "methods": methods,
               "class_bases": class_bases, "class_ext_bases": class_ext_bases,
               "local_vars": local_vars, "project_roots": project_roots,
               "project_method_names": project_method_names,
               "module_imports": module_imports, "module_vars": module_vars,
               "name_types": name_types, "self_types": self_types, "shadow": shadow, "env": fenv}

        def resolve_ref(x):
            """함수 이름·속성이 가리키는 프로젝트 노드 (호출이 아니라 '값'으로 쓰일 때)"""
            if isinstance(x, ast.Name) and x.id in vis:
                return vis[x.id]
            t = resolve_call(x, mod, cls, imports, top, methods, class_bases, local_vars, shadow)
            if not t and isinstance(x, ast.Attribute):
                t = resolve_by_type(x, fenv)[0]
                if not t:
                    parts = dotted_parts(x)
                    if parts and len(parts) > 1 and parts[0] not in shadow:
                        t = resolve_dotted(x, imports, top, classes, methods, class_bases, G["modvar_types"])
            return t if t in nodes else None

        body = list(own_walk(fn, skip_ids))
        calls = [c for c in body if isinstance(c, ast.Call)]
        for c in calls:
            callee = via = recv = None
            declared = False
            if isinstance(c.func, ast.Name) and c.func.id in vis:             # 바깥 함수 안에 정의된 중첩 함수 호출
                callee, via = vis[c.func.id], "closure"
            if not callee:
                callee = resolve_call(c.func, mod, cls, imports, top, methods, class_bases, local_vars, shadow)
            if not callee:                                               # v2 방식으로 못 풀면 추적한 타입으로 재시도
                callee, via, recv, declared = resolve_by_type(c.func, fenv)
            if not callee and isinstance(c.func, (ast.Name, ast.Attribute, ast.Call)):   # obj(...) : 타입을 아는 객체의 __call__
                ct, _src = typed_expr(c.func, fenv)
                if ct and ct[0] == "project":
                    m = find_method_up_chain(ct[1][0], ct[1][1], "__call__", methods, class_bases)
                    if m:
                        callee, via, recv, declared = m, "call_operator", ct[1], _is_declared(ct)
            if not callee:                                               # 모듈 트리를 따라가는 점 표기 (pkg.sub.func)
                parts = dotted_parts(c.func)
                if parts and len(parts) > 1 and parts[0] not in shadow:
                    trace = []
                    callee = resolve_dotted(c.func, imports, top, classes, methods, class_bases,
                                            G["modvar_types"], trace)
                    via = ("module_var" if trace else "module_path") if callee else None
            if callee:
                edge = {"from": caller, "to": callee, "type": "calls", "line": c.lineno}
                if via:
                    edge["via"] = via
                edges.append(edge)
                if callee in class_key:                                  # Foo() 는 실제로 Foo.__init__ 을 실행한다
                    init = find_method_up_chain(class_key[callee][0], class_key[callee][1], "__init__",
                                                methods, class_bases)
                    if init:
                        add_extra(caller, init, "calls_implicit", "init", c.lineno)
                if declared and recv:                                    # 선언된 타입의 자식이 오버라이드했을 수 있다
                    oname = "__call__" if via == "call_operator" else c.func.attr
                    for dd in all_desc(recv):
                        target = methods.get(dd, {}).get(oname)
                        if target and target != callee:
                            edges.append({"from": caller, "to": target, "type": "calls_override",
                                          "line": c.lineno, "via": via})

            # self.method() 은 자식 클래스가 오버라이드한 버전이 실제로 실행될 수 있다
            overridden = False
            if cls and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) \
                    and c.func.value.id == "self":
                for dd in all_desc((mod, cls)):
                    target = methods.get(dd, {}).get(c.func.attr)
                    if target and target != callee:
                        edges.append({"from": caller, "to": target, "type": "calls_override", "line": c.lineno})
                        overridden = True
            if callee or overridden:
                continue

            cat, pkg, detail, conf = classify_unresolved(c.func, ctx)
            if cat in EXTERNAL_CATS:
                key = (caller, cat, pkg, detail, conf)
                if key not in ext_agg:
                    ext_agg[key] = {"from": caller, "to": f"EXTERNAL::{cat}", "type": "calls_external",
                                    "category": cat, "package": pkg, "detail": detail,
                                    "confidence": conf, "count": 0, "line": c.lineno}
                ext_agg[key]["count"] += 1
            else:
                unresolved.append({"from": caller, "expr": detail, "category": cat,
                                   "line": c.lineno})

        # ---- 연산자·컨테이너·변환 프로토콜: a + b -> __add__, v[0] -> __getitem__, len(v) -> __len__ ...
        def proj(node):
            if not isinstance(node, (ast.Name, ast.Attribute, ast.Call)):
                return None
            t, _ = typed_expr(node, fenv)
            return t[1] if t and t[0] == "project" else None

        def is_builtin_val(node):
            if isinstance(node, _VALUE_BUILTIN_NODES):
                return True
            if isinstance(node, (ast.Name, ast.Attribute, ast.Call)):
                t, _ = typed_expr(node, fenv)
                return t == _BUILTIN_T
            return False

        def link(key, dunder, via, line):
            m = find_method_up_chain(key[0], key[1], dunder, methods, class_bases) if key else None
            if m and m != caller and m in nodes:
                add_extra(caller, m, "calls_implicit", via, line)
                return True
            return False

        def binary(left, right, name, line, reflected=None):
            lk, rk = proj(left), proj(right)
            if lk and link(lk, f"__{name}__", "operator", line):
                return
            if rk and rk != lk and (lk or is_builtin_val(left)):
                if name == "mod" and isinstance(left, ast.Constant) and isinstance(left.value, str):
                    return                                                # "%s" % obj 는 str 이 처리한다
                link(rk, reflected or f"__r{name}__", "operator", line)

        for x in body:
            if isinstance(x, ast.BinOp) and type(x.op) in _BINOPS:
                binary(x.left, x.right, _BINOPS[type(x.op)], x.lineno)
            elif isinstance(x, ast.AugAssign) and type(x.op) in _BINOPS:
                nm = _BINOPS[type(x.op)]
                tk = proj(x.target)
                if not (tk and link(tk, f"__i{nm}__", "operator", x.lineno)):       # __iadd__ 가 없으면 a = a + b 와 같다
                    binary(x.target, x.value, nm, x.lineno)
            elif isinstance(x, ast.Compare):
                left = x.left
                for op, right in zip(x.ops, x.comparators):
                    if isinstance(op, (ast.In, ast.NotIn)):
                        link(proj(right), "__contains__", "protocol", x.lineno)
                    elif type(op) in _CMPOPS:
                        nm, refl = _CMPOPS[type(op)]
                        lk, rk = proj(left), proj(right)
                        if not (lk and (link(lk, nm, "operator", x.lineno)
                                        or (nm == "__ne__" and link(lk, "__eq__", "operator", x.lineno)))):
                            if rk and rk != lk and (lk or is_builtin_val(left)):
                                link(rk, refl, "operator", x.lineno)
                    left = right
            elif isinstance(x, ast.UnaryOp) and type(x.op) in _UNARY:
                link(proj(x.operand), _UNARY[type(x.op)], "operator", x.lineno)
            elif isinstance(x, ast.Subscript):
                dn = {ast.Load: "__getitem__", ast.Store: "__setitem__", ast.Del: "__delitem__"}.get(type(x.ctx))
                if dn:
                    link(proj(x.value), dn, "protocol", x.lineno)
            elif isinstance(x, (ast.For, ast.comprehension)):
                link(proj(x.iter), "__aiter__" if getattr(x, "is_async", False) else "__iter__", "protocol",
                     getattr(x, "lineno", None) or x.iter.lineno)
            elif isinstance(x, ast.AsyncFor):
                link(proj(x.iter), "__aiter__", "protocol", x.lineno)
            elif isinstance(x, ast.FormattedValue):
                k = proj(x.value)
                if k:
                    order = {114: ["__repr__"], 115: ["__str__"]}.get(x.conversion, ["__format__", "__str__"])
                    for dn in order:
                        if link(k, dn, "protocol", x.value.lineno):
                            break
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id in _PROTOCOL_CALLS \
                    and x.args and x.func.id not in shadow and x.func.id not in top[mod] and x.func.id not in imports:
                for a in (x.args if x.func.id == "print" else x.args[:1]):
                    k = proj(a)
                    for dn in _PROTOCOL_CALLS[x.func.id]:
                        if link(k, dn, "protocol", x.lineno):
                            break

        # ---- 호출이 아닌 연결: 값으로 넘기는 함수(콜백), 데코레이터, property, with 문
        call_func_ids = {id(c.func) for c in calls}
        arg_ids = {id(a) for c in calls for a in c.args} | {id(k.value) for c in calls for k in c.keywords}
        deco_ids = {id(x) for dd in fn.decorator_list for x in ast.walk(dd)}
        for dd in fn.decorator_list:                                     # @decorator, @decorator(args)
            tgt = resolve_ref(dd.func if isinstance(dd, ast.Call) else dd)
            if tgt and tgt != caller:
                add_extra(caller, tgt, "references", "decorator", dd.lineno)
        for x in body:
            if isinstance(x, ast.Name):
                if not isinstance(x.ctx, ast.Load) or not (x.id in vis or x.id in top[mod] or x.id in imports):
                    continue
            elif isinstance(x, ast.Attribute):
                if x.attr not in attr_names:
                    continue
            elif isinstance(x, (ast.With, ast.AsyncWith)):
                en, ex = ("__aenter__", "__aexit__") if isinstance(x, ast.AsyncWith) else ("__enter__", "__exit__")
                for it in x.items:
                    t, _ = typed_expr(it.context_expr, fenv)
                    if t and t[0] == "project":
                        for nm in (en, ex):
                            m = find_method_up_chain(t[1][0], t[1][1], nm, methods, class_bases)
                            if m:
                                add_extra(caller, m, "calls_implicit", "context_manager", x.lineno)
                continue
            else:
                continue
            if id(x) in call_func_ids or id(x) in deco_ids:
                continue
            tgt = resolve_ref(x)
            if not tgt or tgt == caller or nodes[tgt]["type"] == "class":
                continue
            if tgt in prop_ids:                                          # obj.prop : getter/setter 가 실행된다
                add_extra(caller, tgt, "calls_implicit", "property", x.lineno)
            elif not isinstance(getattr(x, "ctx", None), ast.Store):
                add_extra(caller, tgt, "references", "callback" if id(x) in arg_ids else "value", x.lineno)

    for (mod, qual), cn in class_nodes.items():                          # 클래스 데코레이터 (@dataclass 같은 외부 것은 무시)
        cid = f"{files[mod][0]}::{qual}"
        for dd in cn.decorator_list:
            ex = dd.func if isinstance(dd, ast.Call) else dd
            tgt = resolve_call(ex, mod, None, module_imports[mod], top, methods, class_bases, {}, frozenset())
            if not tgt and isinstance(ex, ast.Attribute):
                tgt = resolve_dotted(ex, module_imports[mod], top, classes, methods, class_bases, G["modvar_types"])
            if tgt and tgt in nodes and tgt != cid:
                add_extra(cid, tgt, "references", "decorator", dd.lineno)
    extra_edges = sorted(extra.values(), key=lambda e: (e["from"], e["to"], e["type"], e["via"]))
    return edges, list(ext_agg.values()), unresolved, extra_edges


# =====================================================================
# 8-2. 구조 간선: 모듈 import / 클래스 상속 / 포함 관계
# =====================================================================
#  imports   모듈 -> 모듈      (프로젝트 안 파일끼리의 import. count = 같은 대상을 import 한 문장 수)
#  extends   클래스 -> 부모 클래스   (프로젝트 안 부모. 프로젝트 밖 부모는 클래스 노드의 external_bases 에 이름으로 기록)
#  contains  모듈 -> 함수/클래스,  클래스 -> 메서드   (노드 ID 의 parent 와 같은 정보를 간선으로도 제공)
def collect_structure(files, nodes, top, methods, class_bases, class_ext_bases, aliases):
    edges = []
    for nid, n in nodes.items():                                  # 소속: 모듈 -> 함수·클래스, 클래스 -> 메서드·중첩 클래스, 함수 -> 중첩 함수
        if n.get("parent"):
            edges.append({"from": n["parent"], "to": nid, "type": "contains"})
    for (cm, cn), parents in class_bases.items():
        cid = f"{files[cm][0]}::{cn}"
        for pm, pc in parents:
            edges.append({"from": cid, "to": f"{files[pm][0]}::{pc}", "type": "extends"})
        if class_ext_bases.get((cm, cn)):
            nodes[cid]["external_bases"] = list(class_ext_bases[(cm, cn)])

    imp_agg = {}
    for mod, (rel, tree) in files.items():
        is_pkg = rel.endswith("__init__.py")
        scope_of = classify_imports(tree)
        for n in ast.walk(tree):
            targets = []
            if isinstance(n, ast.Import):
                for a in n.names:
                    path = canon_module(a.name, aliases)
                    while path and path not in files:             # import a.b.c 에서 a.b.c 가 없으면 가장 가까운 상위 패키지
                        path = path.rpartition(".")[0]
                    if path:
                        targets.append(path)
            elif isinstance(n, ast.ImportFrom):
                base = resolve_from(mod, is_pkg, n.level, n.module)
                if n.level == 0:
                    base = canon_module(base, aliases)
                for a in n.names:
                    sub = _join_mod(base, a.name)
                    if a.name != "*" and sub in files:            # from pkg import submodule
                        targets.append(sub)
                    elif base in files:                           # from module import name
                        targets.append(base)
            is_star = isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
            scope = scope_of.get(id(n), "top")
            for t in targets:
                if t == mod:
                    continue
                key = (rel, files[t][0])
                if key not in imp_agg:
                    imp_agg[key] = {"from": key[0], "to": key[1], "type": "imports", "count": 0,
                                    "_scopes": Counter(), "_lines": {}, "_star": 0}
                rec = imp_agg[key]
                rec["count"] += 1
                rec["_scopes"][scope] += 1
                rec["_lines"].setdefault(scope, n.lineno)
                rec["_lines"][scope] = min(rec["_lines"][scope], n.lineno)
                rec["_star"] += is_star
    for rec in imp_agg.values():                                  # 대표 scope: 최상단 > 조건부 > 함수 안 > TYPE_CHECKING
        sc = rec.pop("_scopes")
        lines = rec.pop("_lines")
        star = rec.pop("_star")
        primary = next(x for x in IMPORT_SCOPES if sc[x])
        rec["scope"] = primary
        rec["scopes"] = {x: sc[x] for x in IMPORT_SCOPES if sc[x]}
        rec["import_time"] = bool(set(rec["scopes"]) & _IMPORT_TIME_SCOPES)
        rec["line"] = lines[primary]
        if star:
            rec["star"] = star
    edges += sorted(imp_agg.values(), key=lambda e: (e["from"], e["to"]))
    return edges


# =====================================================================
# 8-3. 그래프용 정보 ①  import 종류 · 순환 import · `from x import *` 확장
# =====================================================================
#  import 문이 놓인 자리(scope)
#    top           모듈 최상단 (import 하는 순간 실행된다)
#    conditional   최상단의 if / try / for / while / match 안 (try-except ImportError 폴백 등)
#    function      함수 안 (함수를 호출해야 실행된다 = 순환을 피하려는 지연 import 인 경우가 많다)
#    type_checking `if TYPE_CHECKING:` 안 (타입 검사기만 읽고 실행 중에는 일어나지 않는다)
IMPORT_SCOPES = ("top", "conditional", "function", "type_checking")           # 대표 scope 를 고르는 우선순위
_IMPORT_TIME_SCOPES = {"top", "conditional"}
_COND_TYPES = tuple(t for t in (ast.If, ast.Try, ast.For, ast.AsyncFor, ast.While,
                                getattr(ast, "TryStar", None), getattr(ast, "Match", None)) if t)


def _is_type_checking(test):
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or \
           (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def classify_imports(tree):
    """id(import 노드) -> scope.  클래스 본문은 정의되는 순간 실행되므로 바깥 scope 를 그대로 따른다"""
    out = {}

    def visit(n, scope):
        for ch in ast.iter_child_nodes(n):
            visit_node(ch, scope)

    def visit_node(n, scope):
        if isinstance(n, ast.expr):                                   # import 는 식 안에 올 수 없다
            return
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out[id(n)] = scope
            return
        if isinstance(n, FUNC_TYPES):
            visit(n, "function")
            return
        if isinstance(n, ast.If) and _is_type_checking(n.test):
            inner = "type_checking" if scope in _IMPORT_TIME_SCOPES else scope
            for s in n.body:
                visit_node(s, inner)
            for s in n.orelse:
                visit_node(s, "conditional" if scope == "top" else scope)
            return
        if isinstance(n, _COND_TYPES):
            visit(n, "conditional" if scope == "top" else scope)
            return
        visit(n, scope)

    visit(tree, "top")
    return out


def _literal_strs(node):
    if isinstance(node, (ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                                                         for e in node.elts):
        return [e.value for e in node.elts]
    return None


def collect_all_lists(files):
    """모듈 -> `__all__` 이름 목록.  `__all__` 이 없으면 항목 자체가 없고,
    리스트 리터럴로 확정할 수 없으면(다른 모듈 것을 더하거나 조건부로 바꾸는 경우) None"""
    out = {}
    for mod, (rel, tree) in files.items():
        names, found, ok, handled = [], False, True, 0
        for n in tree.body:
            val = mode = None
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                    and n.targets[0].id == "__all__":
                val, mode = n.value, "set"
            elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "__all__" \
                    and n.value is not None:
                val, mode = n.value, "set"
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and n.target.id == "__all__" \
                    and isinstance(n.op, ast.Add):
                val, mode = n.value, "add"
            elif isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) \
                    and isinstance(n.value.func, ast.Attribute) and isinstance(n.value.func.value, ast.Name) \
                    and n.value.func.value.id == "__all__" and n.value.func.attr in ("extend", "append") \
                    and len(n.value.args) == 1:
                arg = n.value.args[0]
                got = [arg.value] if (n.value.func.attr == "append" and isinstance(arg, ast.Constant)
                                      and isinstance(arg.value, str)) else \
                    (_literal_strs(arg) if n.value.func.attr == "extend" else None)
                found = True
                if got is None:
                    ok = False
                else:
                    names += got
                continue
            else:
                continue
            found = True
            handled += 1
            got = _literal_strs(val)
            if got is None:
                ok = False
            elif mode == "set":
                names = list(got)
            else:
                names += got
        stores = sum(1 for x in ast.walk(tree) if isinstance(x, ast.Name) and x.id == "__all__"
                     and isinstance(x.ctx, ast.Store))
        if stores != handled:                                         # 함수·조건문 안에서 바꾸는 경우
            ok, found = False, True
        if found:
            out[mod] = list(dict.fromkeys(names)) if ok else None
    return out


def _module_bound_names(tree):
    """모듈 최상위 스코프에서 바인딩되는 모든 이름 (star import 가 덮어쓰면 안 되는 이름을 가려낸다)"""
    names = set()
    for n in _scope_nodes(tree.body):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                if al.name != "*":
                    names.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, FUNC_TYPES + (ast.ClassDef,)):
            names.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
    return names


def expand_star_imports(files, module_imports, top, var_defs, all_lists, stars):
    """`from lib import *` 가 가져오는 이름을 import 표에 추가한다 (프로젝트 안 모듈만).
    이름의 범위: `__all__` 이 있으면 그것, 없으면 밑줄로 시작하지 않는 모듈 수준 이름.
    `__all__` 을 확정할 수 없거나, 가져오는 쪽 모듈이 이미 같은 이름을 스스로 바인딩하면 건드리지 않는다.
    star 가 star 를 다시 가져오는 경우를 위해 몇 번 되풀이한다."""
    bound = {}
    summary = {"statements": sum(len(v) for v in stars.values()), "project_statements": 0, "names_bound": 0}
    summary["project_statements"] = sum(1 for items in stars.values() for base, _ in items if base in top)
    for _ in range(6):
        changed = False
        for mod, items in stars.items():
            if mod not in bound:
                bound[mod] = _module_bound_names(files[mod][1])
            table = module_imports[mod]
            for base, _level in items:
                if base not in top or base == mod:
                    continue
                if base in all_lists:
                    names = all_lists[base]
                    if names is None:
                        continue
                else:
                    pool = set(top[base]) | set(var_defs.get(base, {})) | set(module_imports.get(base, {}))
                    names = sorted(n for n in pool if not n.startswith("_"))
                for nm in names:
                    if nm in table or nm in bound[mod] or nm in top.get(mod, {}):
                        continue
                    in_base = nm in top[base] or nm in var_defs.get(base, {}) or nm in module_imports.get(base, {})
                    if not in_base:
                        continue                                      # __all__ 에 있지만 정의를 못 찾은 이름
                    table[nm] = ("name", base, nm, 0)
                    summary["names_bound"] += 1
                    changed = True
        if not changed:
            break
    return summary


# =====================================================================
# 8-3b. 동적 호출 표시 : 정적 분석으로 따라갈 수 없는 곳을 숨기지 않고 알려 준다
# =====================================================================
#  kind: getattr / setattr / delattr (이름이 문자열 변수)  eval / exec / compile  import (importlib.import_module, __import__)
#        namespace_lookup (globals()[이름], vars(x)[이름], x.__dict__[이름])  type_new (type(이름, 부모, 속성))
#        methodcaller / attrgetter (operator, 이름이 변수)  getattr_hook (클래스가 __getattr__ / __getattribute__ 정의)
def _dynamic_kind(n, mod, imports, top):
    def builtin(f):
        return isinstance(f, ast.Name) and f.id in BUILTIN_NAMES and f.id not in imports and f.id not in top.get(mod, {})

    def nonconst(a):
        return not (isinstance(a, ast.Constant) and isinstance(a.value, str))

    if isinstance(n, ast.Call):
        f = n.func
        if builtin(f):
            if f.id in ("getattr", "setattr", "delattr") and len(n.args) >= 2 and nonconst(n.args[1]):
                return f.id
            if f.id in ("eval", "exec", "compile", "__import__"):
                return "import" if f.id == "__import__" else f.id
            if f.id == "type" and len(n.args) == 3:
                return "type_new"
            return None
        pkg, last = _origin(f, imports)
        if pkg == "importlib" and last in ("import_module", "__import__"):
            return "import"
        if pkg == "operator" and last in ("methodcaller", "attrgetter") and n.args and nonconst(n.args[0]):
            return last
    elif isinstance(n, ast.Subscript):
        v = n.value
        if isinstance(v, ast.Call) and builtin(v.func) and v.func.id in ("globals", "locals", "vars"):
            return "namespace_lookup"
        if isinstance(v, ast.Attribute) and v.attr == "__dict__" and nonconst(n.slice):
            return "namespace_lookup"
    return None


def collect_dynamic(files, nodes, defs, methods, module_imports, top):
    """반환: [{"from", "kind", "expr", "line"}].  노드에는 `dynamic`(종류 목록)을 기록한다"""
    out = []
    skip_ids = {id(d["node"]) for d in defs}

    def scan(frm, mod, it):
        for n in it:
            k = _dynamic_kind(n, mod, module_imports[mod], top)
            if k:
                out.append({"from": frm, "kind": k, "expr": _short(ast.unparse(n), 80), "line": n.lineno})

    for d in defs:
        scan(d["id"], d["mod"], own_walk(d["node"], skip_ids))
    for mod, (rel, tree) in files.items():
        scan(rel, mod, _scope_nodes(tree.body))
    for (mod, qual), ms in methods.items():
        for hook in ("__getattr__", "__getattribute__"):
            if hook in ms:
                cid = f"{files[mod][0]}::{qual}"
                out.append({"from": cid, "kind": "getattr_hook", "expr": hook, "line": nodes[ms[hook]]["line"]})
    out.sort(key=lambda r: (r["from"], r["line"], r["kind"], r["expr"]))
    kinds = defaultdict(set)
    for r in out:
        kinds[r["from"]].add(r["kind"])
    for nid, ks in kinds.items():
        if nid in nodes:
            nodes[nid]["dynamic"] = sorted(ks)
    return out


def _tarjan_sccs(graph):
    """강연결 요소 (반복형 Tarjan).  graph: 노드 -> 정렬된 이웃 리스트.  크기 2 이상인 것만 돌려준다"""
    index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
    for start in sorted(graph):
        if start in index:
            continue
        work = [(start, 0)]
        index[start] = low[start] = counter[0]
        counter[0] += 1
        stack.append(start)
        on.add(start)
        while work:
            v, i = work[-1]
            nbrs = graph.get(v, [])
            if i < len(nbrs):
                work[-1] = (v, i + 1)
                w = nbrs[i]
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, 0))
                elif w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        out.append(sorted(comp))
    return out


def _shortest_cycle(start, graph, members):
    """start 에서 출발해 start 로 돌아오는 가장 짧은 경로 [start, ..., start]"""
    prev, dq = {start: None}, deque([start])
    while dq:
        v = dq.popleft()
        for w in graph.get(v, []):
            if w not in members:
                continue
            if w == start:
                path = []
                while v is not None:
                    path.append(v)
                    v = prev[v]
                return path[::-1] + [start]
            if w not in prev:
                prev[w] = v
                dq.append(w)
    return [start]


# 순환의 종류: 어느 scope 의 import 까지 포함해야 고리가 생기는가
CYCLE_LEVELS = (("import_time", _IMPORT_TIME_SCOPES),                     # 실제 import 순간에 서로를 요구 (ImportError 위험)
                ("deferred", _IMPORT_TIME_SCOPES | {"function"}),         # 함수 안 지연 import 까지 쳐야 고리가 생김 (순환을 회피해 둔 상태)
                ("type_only", set(IMPORT_SCOPES)))                        # TYPE_CHECKING 까지 쳐야 고리가 생김 (실행에는 영향 없음)


def find_import_cycles(import_edges):
    """모듈 import 간선에서 순환(강연결 요소)을 찾는다.  더 약한 조건에서 이미 보고된 같은 모듈 묶음은 다시 보고하지 않는다"""
    reported, cycles = set(), []
    for kind, allowed in CYCLE_LEVELS:
        use = [e for e in import_edges if set(e["scopes"]) & allowed]
        graph = defaultdict(set)
        for e in use:
            graph[e["from"]].add(e["to"])
        graph = {k: sorted(v) for k, v in graph.items()}
        for comp in _tarjan_sccs(graph):
            key = frozenset(comp)
            if key in reported:
                continue
            reported.add(key)
            members = set(comp)
            cycles.append({"kind": kind, "modules": comp, "size": len(comp),
                           "path": _shortest_cycle(comp[0], graph, members),
                           "edges": [{"from": e["from"], "to": e["to"], "scope": e["scope"], "line": e["line"]}
                                     for e in sorted(use, key=lambda e: (e["from"], e["to"]))
                                     if e["from"] in members and e["to"] in members]})
    order = {k: i for i, (k, _) in enumerate(CYCLE_LEVELS)}
    cycles.sort(key=lambda c: (order[c["kind"]], c["size"], c["modules"]))
    for i, c in enumerate(cycles, 1):
        c["id"] = f"cycle:{i}"
    return [{"id": c["id"], **{k: v for k, v in c.items() if k != "id"}} for c in cycles]


# =====================================================================
# 8-4. 그래프용 정보 ②  진입점(entry point) 탐지
# =====================================================================
#  "코드가 어디서 시작되는가"를 알려 주는 표시.  시각화에서 '여기서부터 읽으세요' 와 호출 깊이(entry_depth)의 출발점이 된다.
#   category  runtime  프로그램을 실행하면 시작되는 곳      api  바깥(웹 요청·라이브러리 사용자)에서 들어오는 곳
#             test     테스트 실행기가 부르는 곳
#   kind      main_guard / main_module / main_call / console_script / cli_command / task / route / web_hook /
#             public_api / test / fixture
#   confidence  certain(코드에 그대로 적혀 있음)  inferred(import 출처까지 확인한 규칙)  heuristic(이름·폴더 관례)
_FRAMEWORKS = {                       # 패키지 -> {생성자 이름: 종류}   (import 출처까지 확인해서 이름만 같은 객체를 거른다)
    "flask": {"Flask": "web", "Blueprint": "web"},
    "fastapi": {"FastAPI": "web", "APIRouter": "web"},
    "sanic": {"Sanic": "web", "Blueprint": "web"},
    "quart": {"Quart": "web", "Blueprint": "web"},
    "starlette": {"Starlette": "web"},
    "bottle": {"Bottle": "web"},
    "flask_restx": {"Api": "web", "Namespace": "web"},
    "flask_restful": {"Api": "web"},
    "typer": {"Typer": "cli"},
    "celery": {"Celery": "task"},
}
_HTTP_ATTRS = {"get", "post", "put", "delete", "patch", "options", "head", "trace", "websocket"}
_WEB_HOOK_ATTRS = {"before_request", "after_request", "teardown_request", "before_first_request", "errorhandler",
                   "exception_handler", "on_event", "middleware", "context_processor", "template_filter", "listener",
                   "before_app_request", "after_app_request"}
_CATEGORY_ORDER = {"runtime": 0, "api": 1, "test": 2}


def _origin(expr, imports):
    """식 -> (import 한 패키지의 최상위 이름, 마지막 이름).  import 해서 쓴 것이 아니면 (None, 마지막 이름)"""
    parts = dotted_parts(expr)
    if not parts:
        return None, None
    imp = imports.get(parts[0])
    if imp and imp[1]:
        pkg = imp[1].split(".")[0]
        if len(parts) == 1 and imp[0] == "name":
            return pkg, imp[2]
        return pkg, parts[-1]
    return None, parts[-1]


def _split_target(target):
    target = target.split("[")[0].strip().strip("'\"")
    if ":" not in target:
        return None
    m, _, f = target.partition(":")
    m, f = m.strip(), f.strip()
    return (m, f) if m and f else None


def find_console_scripts(root):
    """setup.py / setup.cfg / pyproject.toml 에 적힌 실행 명령 -> [(명령 이름, 모듈, 함수, 출처 파일)]
    분석 폴더에서 시작해 설정 파일이 있는 폴더(또는 .git 이 있는 폴더)까지 최대 3단계 위로 찾는다"""
    import configparser
    import re
    dirs, d = [], Path(root)
    for _ in range(4):
        dirs.append(d)
        if (d / ".git").exists() or any((d / f).exists() for f in ("setup.py", "setup.cfg", "pyproject.toml")) \
                or d.parent == d:
            break
        d = d.parent
    out = []

    def add(name, target, src):
        tm = _split_target(target)
        if tm:
            out.append((name, tm[0], tm[1], src))

    for d in dirs:
        sp = d / "setup.py"
        if sp.exists():
            try:
                tree = ast.parse(sp.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, ValueError, OSError):
                tree = None
            for c in (n for n in ast.walk(tree) if isinstance(n, ast.Call)) if tree else ():
                fname = c.func.id if isinstance(c.func, ast.Name) else (c.func.attr if isinstance(c.func, ast.Attribute) else None)
                if fname != "setup":
                    continue
                for kw in c.keywords:
                    if kw.arg == "entry_points" and isinstance(kw.value, ast.Dict):
                        for k, v in zip(kw.value.keys, kw.value.values):
                            if isinstance(k, ast.Constant) and k.value in ("console_scripts", "gui_scripts"):
                                for spec in _literal_strs(v) or []:
                                    name, _, tgt = spec.partition("=")
                                    add(name.strip(), tgt or name, "setup.py")
        cfg = d / "setup.cfg"
        if cfg.exists():
            cp = configparser.RawConfigParser(strict=False)
            try:
                cp.read(cfg, encoding="utf-8")
            except (configparser.Error, UnicodeDecodeError, OSError):
                pass
            if cp.has_section("options.entry_points"):
                for key in ("console_scripts", "gui_scripts"):
                    for line in cp.get("options.entry_points", key, fallback="").splitlines():
                        if "=" in line:
                            name, _, tgt = line.partition("=")
                            add(name.strip(), tgt, "setup.cfg")
        pp = d / "pyproject.toml"
        if pp.exists():
            try:
                text = pp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                text = ""
            section = None
            for line in text.splitlines():
                s = line.strip()
                m = re.match(r"^\[([^\[\]]+)\]\s*(#.*)?$", s)
                if m:
                    section = m.group(1).strip()
                elif section in ("project.scripts", "project.gui-scripts", "tool.poetry.scripts"):
                    m2 = re.match(r"""^["']?([\w.\-]+)["']?\s*=\s*["']([^"']+)["']""", s)
                    if m2:
                        add(m2.group(1), m2.group(2), "pyproject.toml")
    return out


def _is_test_file(rel):
    parts = rel.split("/")
    return parts[-1].startswith("test_") or parts[-1].endswith("_test.py") or any(p in ("tests", "test") for p in parts[:-1])


# 클래스 기반 뷰: (패키지, 부모 클래스) -> (프레임워크, 요청을 받는 메서드 이름 -> 표시할 HTTP 메서드)
_HTTP_NAMES = {m: m.upper() for m in ("get", "post", "put", "delete", "patch", "head", "options", "trace")}
_ACTIONS = {"list": "GET", "retrieve": "GET", "create": "POST", "update": "PUT", "partial_update": "PATCH",
            "destroy": "DELETE"}
_VIEW_BASES = {}
for _pkg, _fw, _names, _bases in (
        ("flask", "flask", _HTTP_NAMES, ("MethodView",)),
        ("flask", "flask", {"dispatch_request": "ANY"}, ("View",)),
        ("flask_restful", "flask_restful", _HTTP_NAMES, ("Resource",)),
        ("flask_restx", "flask_restx", _HTTP_NAMES, ("Resource",)),
        ("django", "django", {**_HTTP_NAMES}, ("View", "TemplateView", "RedirectView")),
        ("rest_framework", "django-rest-framework", _HTTP_NAMES, ("APIView", "GenericAPIView")),
        ("rest_framework", "django-rest-framework", {**_ACTIONS},
         ("ViewSet", "GenericViewSet", "ModelViewSet", "ReadOnlyModelViewSet")),
        ("starlette", "starlette", _HTTP_NAMES, ("HTTPEndpoint",)),
        ("tornado", "tornado", _HTTP_NAMES, ("RequestHandler",))):
    for _b in _bases:
        _VIEW_BASES[(_pkg, _b)] = (_fw, _names)
_REGISTER_ATTRS = {"add_url_rule", "add_api_route", "add_route", "add_resource"}


def detect_entry_points(root, files, nodes, defs, top, methods, classes, class_nodes, class_bases, module_imports,
                        var_defs, exports, all_lists, aliases):
    entries, seen = [], set()

    def add(nid, kind, category, detail, line, conf, **extra):
        key = (nid, kind, detail)
        if nid not in nodes or key in seen:
            return
        seen.add(key)
        e = {"id": nid, "kind": kind, "category": category, "detail": detail, "file": nodes[nid]["file"],
             "line": line, "confidence": conf}
        e.update(extra)
        entries.append(e)

    # ---- 1. `if __name__ == "__main__":` 와 __main__.py
    def calls_in(body, mod):
        found = {}
        imports = module_imports[mod]
        for n in _scope_nodes(body):
            if isinstance(n, ast.Call):
                t = resolve_call(n.func, mod, None, imports, top, methods, class_bases, {}, frozenset())
                if not t and len(dotted_parts(n.func) or []) > 1:
                    t = resolve_dotted(n.func, imports, top, classes, methods, class_bases, None)
                if t and t in nodes:
                    found.setdefault(t, n.lineno)
        return found

    for mod, (rel, tree) in files.items():
        for n in tree.body:
            if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and len(n.test.ops) == 1 \
                    and isinstance(n.test.ops[0], ast.Eq):
                sides = [n.test.left] + list(n.test.comparators)
                if any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) \
                        and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides):
                    add(rel, "main_guard", "runtime", 'if __name__ == "__main__"', n.lineno, "certain")
                    for t, ln in calls_in(n.body, mod).items():
                        add(t, "main_call", "runtime", f"{rel} 의 __main__ 블록에서 호출", ln, "certain")
        if rel.endswith("__main__.py"):
            add(rel, "main_module", "runtime", "python -m 으로 실행되는 파일", 1, "certain")
            for t, ln in calls_in(tree.body, mod).items():
                add(t, "main_call", "runtime", f"{rel} 에서 호출", ln, "certain")

    # ---- 2. setup.py / setup.cfg / pyproject.toml 의 실행 명령
    for name, m, f, src in find_console_scripts(root):
        modpath = canon_module(m, aliases)
        if modpath not in top:
            continue
        if "." in f:
            cls, _, meth = f.partition(".")
            target = methods.get((modpath, cls), {}).get(meth)
        else:
            target = top[modpath].get(f)
        if target:
            add(target, "console_script", "runtime", f"{name} ({m}:{f})", nodes[target]["line"], "certain", source=src)

    # ---- 3. 프레임워크 데코레이터 (Flask·FastAPI·click·Typer·Celery·pytest)
    fw = {}                                                       # (모듈, 변수) -> (패키지, 종류)
    for mod, vd in var_defs.items():
        for name, (val, _ann) in vd.items():
            if isinstance(val, ast.Call):
                pkg, last = _origin(val.func, module_imports[mod])
                if pkg in _FRAMEWORKS and last in _FRAMEWORKS[pkg]:
                    fw[(mod, name)] = (pkg, _FRAMEWORKS[pkg][last])

    def fw_of(mod, name):
        if (mod, name) in fw:
            return fw[(mod, name)]
        imp = module_imports[mod].get(name)
        if imp and imp[0] == "name":
            tgt = exports.get((imp[1], imp[2]), (imp[1], imp[2]))
            return fw.get(tgt)
        return None

    # 데코레이터를 볼 함수: 모듈 최상위 함수 + 함수 안에 정의된 함수 (앱 팩토리: def create_app(): app = Flask(...); @app.route ...)
    top_funcs = [d for d in defs if d["parent"] == d["rel"] or d["parent_is_func"]]
    defs_by_id = {d["id"]: d for d in defs}
    skip_ids = {id(d["node"]) for d in defs}
    children_names = defaultdict(set)
    for d in defs:
        if d["parent_is_func"]:
            children_names[d["parent"]].add(d["node"].name)
    _local = {}

    def local_fw(d):
        """함수 본문에서 딱 한 번 `app = Flask(...)` 처럼 만든 프레임워크 객체"""
        if d["id"] in _local:
            return _local[d["id"]]
        fn, stores, assigns = d["node"], Counter(), {}
        a = fn.args
        for arg in a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]:
            stores[arg.arg] += 2
        for nm in children_names[d["id"]]:
            stores[nm] += 2
        for x in own_walk(fn, skip_ids):
            if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                stores[x.id] += 1
            elif isinstance(x, ast.Assign) and len(x.targets) == 1 and isinstance(x.targets[0], ast.Name) \
                    and isinstance(x.value, ast.Call):
                assigns[x.targets[0].id] = x.value
            elif isinstance(x, (ast.Global, ast.Nonlocal)):
                for nm in x.names:
                    stores[nm] += 2
        res = {}
        for nm, call in assigns.items():
            if stores[nm] == 1:
                pkg, last = _origin(call.func, module_imports[d["mod"]])
                if pkg in _FRAMEWORKS and last in _FRAMEWORKS[pkg]:
                    res[nm] = (pkg, _FRAMEWORKS[pkg][last])
        _local[d["id"]] = res
        return res

    def fwl(d, name):
        cur = d
        while cur is not None:
            r = local_fw(cur).get(name)
            if r:
                return r
            cur = defs_by_id.get(cur["parent"]) if cur["parent_is_func"] else None
        return fw_of(d["mod"], name)

    def click_kind(d, mod):
        pkg, last = _origin(d.func if isinstance(d, ast.Call) else d, module_imports[mod])
        return last if pkg == "click" and last in ("command", "group") else None

    click_objs = set()
    for d in top_funcs:
        if any(click_kind(x, d["mod"]) for x in d["node"].decorator_list):
            click_objs.add((d["mod"], d["node"].name))

    def route_detail(dec, attr):
        args = dec.args if isinstance(dec, ast.Call) else []
        kws = {k.arg: k.value for k in dec.keywords} if isinstance(dec, ast.Call) else {}
        path = None
        first = args[0] if args else kws.get("path", kws.get("rule"))
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            path = first.value
        if attr in _HTTP_ATTRS:
            methods_ = [attr.upper()]
        else:
            methods_ = [m.upper() for m in (_literal_strs(kws.get("methods")) or [])] if "methods" in kws else ["GET"]
        return (" ".join(["|".join(methods_)] + ([path] if path else []))).strip()

    for d in top_funcs:
        mod, node, nid = d["mod"], d["node"], d["id"]
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                obj, attr = target.value.id, target.attr
                info = fwl(d, obj)
                if info:
                    pkg, cat = info
                    ex = {"framework": pkg, **({"in_function": True} if d["parent_is_func"] else {})}
                    if cat == "web" and (attr == "route" or attr in _HTTP_ATTRS):
                        add(nid, "route", "api", route_detail(dec, attr), dec.lineno, "inferred", **ex)
                    elif cat == "web" and attr in _WEB_HOOK_ATTRS:
                        add(nid, "web_hook", "api", attr, dec.lineno, "inferred", **ex)
                    elif cat == "cli" and attr in ("command", "callback"):
                        add(nid, "cli_command", "runtime", f"{pkg} {attr}", dec.lineno, "inferred", **ex)
                    elif cat == "task" and attr == "task":
                        add(nid, "task", "runtime", f"{pkg} task", dec.lineno, "inferred", **ex)
                    continue
                if attr in ("command", "group"):                   # @cli.command()  (cli 가 click 그룹 함수일 때)
                    imp = module_imports[mod].get(obj)
                    ref = (mod, obj) if (mod, obj) in click_objs else (
                        exports.get((imp[1], imp[2]), (imp[1], imp[2])) if imp and imp[0] == "name" else None)
                    if ref in click_objs:
                        add(nid, "cli_command", "runtime", f"click {attr}", dec.lineno, "inferred", framework="click")
                        continue
            kind = click_kind(dec, mod)
            if kind:
                add(nid, "cli_command", "runtime", f"click {kind}", dec.lineno, "inferred", framework="click")
                continue
            pkg, last = _origin(target, module_imports[mod])
            if pkg == "celery" and last == "shared_task":
                add(nid, "task", "runtime", "celery shared_task", dec.lineno, "inferred", framework="celery")
            elif pkg == "pytest" and last == "fixture":
                add(nid, "fixture", "test", "pytest fixture", dec.lineno, "inferred", framework="pytest")

    # ---- 3-2. 클래스 기반 뷰와 URL 등록 호출 (add_url_rule · add_resource · add_api_route · django path)
    class_id = {key: f"{files[key[0]][0]}::{key[1]}" for key in class_nodes}
    key_of_id = {v: k for k, v in class_id.items()}

    def resolve_expr(x, mod):
        imports = module_imports[mod]
        t = resolve_call(x, mod, None, imports, top, methods, class_bases, {}, frozenset())
        if not t and len(dotted_parts(x) or []) > 1:
            t = resolve_dotted(x, imports, top, classes, methods, class_bases, None)
        return t if t in nodes else None

    def view_target(x, mod):
        if isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "as_view":
            return resolve_expr(x.func.value, mod)
        return resolve_expr(x, mod)

    def str_arg(x):
        return x.value if isinstance(x, ast.Constant) and isinstance(x.value, str) else None

    class_paths, class_verified = defaultdict(list), set()          # 클래스 id -> 등록된 URL 들 / 근거가 확인된 클래스
    local_names = defaultdict(set)
    for d in defs:
        local_names[d["mod"]] |= set(local_fw(d))
    for mod, (rel, tree) in files.items():
        imports = module_imports[mod]
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):                         # @ns.route("/p") 가 붙은 클래스
                cid = f"{rel}::{n.name}"
                for dec in n.decorator_list:
                    t = dec.func if isinstance(dec, ast.Call) else dec
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.attr == "route":
                        info = fw_of(mod, t.value.id)
                        if info and info[1] == "web" and cid in nodes:
                            class_verified.add(cid)
                            p = str_arg(dec.args[0]) if isinstance(dec, ast.Call) and dec.args else None
                            if p:
                                class_paths[cid].append(p)
                continue
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            reg_path = reg_view = None
            kws = {k.arg: k.value for k in n.keywords}
            if isinstance(f, ast.Attribute) and f.attr in _REGISTER_ATTRS and isinstance(f.value, ast.Name) and (
                    (fw_of(mod, f.value.id) or (None, None))[1] == "web" or f.value.id in local_names[mod]):
                if f.attr == "add_resource" and n.args:
                    tgt = view_target(n.args[0], mod)
                    if tgt in key_of_id:
                        class_verified.add(tgt)
                        class_paths[tgt] += [str_arg(pa) for pa in n.args[1:] if str_arg(pa)]
                    continue
                reg_path = str_arg(n.args[0]) if n.args else None
                reg_view = kws.get("view_func") or kws.get("endpoint") or (
                    n.args[2] if f.attr == "add_url_rule" and len(n.args) > 2 else (
                        n.args[1] if f.attr != "add_url_rule" and len(n.args) > 1 else None))
            else:
                pkg, last = _origin(f, imports)
                if pkg == "django" and last in ("path", "re_path", "url") and len(n.args) > 1:
                    reg_path, reg_view = str_arg(n.args[0]), n.args[1]
            if reg_view is None:
                continue
            tgt = view_target(reg_view, mod)
            if not tgt:
                continue
            if nodes[tgt]["type"] == "class":
                class_verified.add(tgt)
                if reg_path is not None:
                    class_paths[tgt].append(reg_path)
            elif nodes[tgt]["type"] in ("function", "method"):
                meths = [m.upper() for m in (_literal_strs(kws.get("methods")) or [])] or ["GET"]
                add(tgt, "route", "api", " ".join(["|".join(meths)] + ([reg_path] if reg_path else [])),
                    n.lineno, "inferred", framework="registered")

    def view_spec(key):
        """클래스(와 프로젝트 안 부모)가 프레임워크 뷰 클래스를 상속하는가 -> (프레임워크, 메서드 표)"""
        for k in [key] + sorted(_ancestors(key, class_bases)):
            for b in class_nodes[k].bases:
                pkg, last = _origin(b.value if isinstance(b, ast.Subscript) else b, module_imports[k[0]])
                if (pkg, last) in _VIEW_BASES:
                    return _VIEW_BASES[(pkg, last)]
        return None

    for key in sorted(class_nodes):
        cid = class_id[key]
        if cid not in nodes or "." in key[1]:
            continue
        spec = view_spec(key)
        if not spec and cid not in class_verified:
            continue
        fw_name, names = spec if spec else ("registered", _HTTP_NAMES)
        paths = ", ".join(dict.fromkeys(class_paths.get(cid, [])))
        for mname, mid in sorted(methods.get(key, {}).items()):
            if mname in names:
                label = names[mname]
                add(mid, "route", "api", " ".join([label] + ([paths] if paths else [])) + " (클래스 기반 뷰)",
                    nodes[mid]["line"], "inferred" if spec or cid in class_verified else "heuristic", framework=fw_name)

    # ---- 4. 테스트
    for nid, n in nodes.items():
        if n["type"] not in ("function", "method") or n.get("nested") or not _is_test_file(n["file"]):
            continue
        if not n["name"].startswith("test"):
            continue
        parent = nodes.get(n["parent"])
        if n["type"] == "function" and parent and parent["type"] == "module":
            add(nid, "test", "test", "test_* 함수", n["line"], "heuristic")
        elif n["type"] == "method" and parent and parent["type"] == "class" and (
                parent["name"].startswith("Test") or any(b.split(".")[-1] == "TestCase" for b in parent.get("bases", []))):
            add(nid, "test", "test", f"{parent['name']} 의 테스트 메서드", n["line"], "heuristic")

    # ---- 5. 공개 API:  `__all__`  또는  패키지 __init__.py 가 다시 내보내는 이름
    def exported(mod, name):
        t = top.get(mod, {}).get(name)
        if t:
            return t
        imp = module_imports.get(mod, {}).get(name)
        if imp and imp[0] == "name":
            return top.get(imp[1], {}).get(imp[2])
        return None

    for mod, (rel, tree) in files.items():
        if all_lists.get(mod):
            for name in all_lists[mod]:
                t = exported(mod, name)
                if t and nodes[t]["type"] != "module":
                    add(t, "public_api", "api", f"{rel} 의 __all__", nodes[t]["line"], "certain")
        elif mod in all_lists:
            continue                                               # __all__ 이 있지만 확정 못 함 -> 추측하지 않는다
        elif rel.endswith("__init__.py"):
            scopes = classify_imports(tree)
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and scopes.get(id(n)) in _IMPORT_TIME_SCOPES:
                    for a in n.names:
                        name = a.asname or a.name
                        if a.name == "*" or name.startswith("_"):
                            continue
                        t = exported(mod, name)
                        if t and nodes[t]["type"] != "module":
                            add(t, "public_api", "api", f"{rel} 가 다시 내보냄", nodes[t]["line"], "inferred")

    entries.sort(key=lambda e: (_CATEGORY_ORDER[e["category"]], e["kind"], e["id"], e["detail"]))
    return entries


CALL_LIKE = ("calls", "calls_override", "calls_implicit", "references")


def annotate_entry_depth(nodes, entries, edges):
    """노드에 entry(어떤 진입점인지)와 entry_depth(가장 가까운 진입점에서 호출 간선으로 몇 걸음인지)를 기록한다.
    테스트 진입점은 출발점에서 뺀다 (테스트가 부르는 코드와 실제 실행 경로를 섞지 않기 위해).
    도달할 수 없으면 None, 진입점이 아예 없는 프로젝트는 모두 None"""
    kinds = defaultdict(list)
    for e in entries:
        if e["kind"] not in kinds[e["id"]]:
            kinds[e["id"]].append(e["kind"])
    adj = defaultdict(set)
    for e in edges:
        if e["type"] in CALL_LIKE:
            adj[e["from"]].add(e["to"])
    depth = {}
    dq = deque()
    for e in entries:
        if e["category"] != "test" and e["id"] not in depth:
            depth[e["id"]] = 0
            dq.append(e["id"])
    while dq:
        v = dq.popleft()
        for w in sorted(adj.get(v, ())):
            if w not in depth:
                depth[w] = depth[v] + 1
                dq.append(w)
    for nid, n in nodes.items():
        if nid in kinds:
            n["entry"] = kinds[nid]
        if n["type"] != "module":
            n["entry_depth"] = depth.get(nid)
    return sum(1 for nid, n in nodes.items() if n["type"] != "module" and n["entry_depth"] is not None)


# =====================================================================
# 8-4b. 중요도(importance)와 읽는 순서(reading_order)
# =====================================================================
#  importance   호출·참조·상속 간선 위에서 계산한 PageRank (가장 높은 것이 1).  "많은 코드가 기대는 코드"일수록 높다.
#               테스트 파일에서 나가는 간선은 뺀다 (테스트는 모든 것을 호출해서 중요도를 부풀리므로).
#               모듈 노드는 그 파일 안 노드들의 합.
#  reading_order  처음 보는 사람이 읽을 순서.  진입점에서 가까운 것부터, 같은 거리에서는 중요한 것부터.
READING_NODES = 40                     # reading_order.nodes 에 담을 최대 개수
_ENTRY_RANK = {"runtime": 0, "api": 1, "test": 2}


def compute_importance(nodes, edges, damping=0.85, iters=100):
    ids = sorted(nid for nid, n in nodes.items() if n["type"] != "module")
    idx = {nid: i for i, nid in enumerate(ids)}
    out = defaultdict(lambda: defaultdict(float))
    for e in edges:
        if e["type"] not in CALL_LIKE and e["type"] != "extends":
            continue
        a, b = e["from"], e["to"]
        if a not in idx or b not in idx or a == b or _is_test_file(nodes[a]["file"]):
            continue
        out[a][b] += e.get("count", 1)
    n = len(ids)
    if not n:
        return 0
    rank = [1.0 / n] * n
    for _ in range(iters):
        nxt = [(1 - damping) / n] * n
        dangling = 0.0
        for nid in ids:
            i = idx[nid]
            tot = sum(out[nid].values()) if nid in out else 0
            if not tot:
                dangling += rank[i]
                continue
            for b, w in out[nid].items():
                nxt[idx[b]] += damping * rank[i] * w / tot
        share = damping * dangling / n
        nxt = [x + share for x in nxt]
        done = sum(abs(x - y) for x, y in zip(nxt, rank)) < 1e-10
        rank = nxt
        if done:
            break
    top = max(rank) or 1.0
    per_file = Counter()
    for nid in ids:
        nodes[nid]["importance"] = round(rank[idx[nid]] / top, 4)
        per_file[nodes[nid]["file"]] += rank[idx[nid]]
    ftop = max(per_file.values()) or 1.0
    for nid, nd in nodes.items():
        if nd["type"] == "module":
            nd["importance"] = round(per_file[nd["file"]] / ftop, 4)
    return n


def build_reading_order(nodes, entries, limit=READING_NODES):
    """처음 읽을 파일(modules)과 함수·클래스(nodes)를 순서대로 돌려준다.  테스트 파일은 뺀다"""
    best_entry = {}                                                   # 노드 -> (가장 앞선 category 순위, 종류들)
    for e in entries:
        if e["category"] == "test":
            continue
        rank, kinds = best_entry.get(e["id"], (9, []))
        best_entry[e["id"]] = (min(rank, _ENTRY_RANK[e["category"]]), kinds + ([e["kind"]] if e["kind"] not in kinds else []))
    INF = 10 ** 6
    syms = [n for n in nodes.values() if n["type"] != "module" and not _is_test_file(n["file"])]

    def depth(n):
        return n["entry_depth"] if n.get("entry_depth") is not None else INF

    def why(n):
        out = []
        if n["id"] in best_entry:
            out.append("진입점: " + ", ".join(best_entry[n["id"]][1]))
        elif n.get("entry_depth") is not None:
            out.append(f"진입점에서 {n['entry_depth']}걸음")
        else:
            out.append("진입점에서 호출로 닿지 않음")
        if n.get("importance", 0) >= 0.5:
            out.append("많은 코드가 기대는 핵심")
        return out

    by_file = defaultdict(list)
    for n in syms:
        by_file[n["file"]].append(n)
    mods = []
    for m in nodes.values():
        if m["type"] != "module":
            continue
        inside = by_file.get(m["file"], [])
        mod_entry = m["id"] in best_entry
        if not inside and not mod_entry:
            continue                                                  # 내용 없는 파일(빈 __init__.py 등)
        d = min([depth(n) for n in inside] + ([0] if mod_entry else [INF]))
        first = sorted(inside, key=lambda n: (depth(n), best_entry.get(n["id"], (9,))[0], -n.get("importance", 0), n["id"]))[:3]
        reasons = []
        kinds = sorted({k for n in inside for k in best_entry.get(n["id"], (9, []))[1]} |
                       set(best_entry.get(m["id"], (9, []))[1]))
        if kinds:
            reasons.append("진입점: " + ", ".join(kinds))
        elif d < INF:
            reasons.append(f"진입점에서 {d}걸음")
        else:
            reasons.append("진입점에서 호출로 닿지 않음")
        if m.get("importance", 0) >= 0.5:
            reasons.append("많은 코드가 기대는 핵심 파일")
        if m.get("import_cycles"):
            reasons.append("순환 import 에 포함")
        mods.append({"id": m["id"], "min_depth": None if d >= INF else d, "importance": m.get("importance", 0),
                     "entry": kinds, "read_first": [n["id"] for n in first], "reasons": reasons})
    mods.sort(key=lambda x: (x["min_depth"] is None, x["min_depth"] or 0, -x["importance"], x["id"]))
    for i, x in enumerate(mods, 1):
        x["rank"] = i
    ordered = sorted(syms, key=lambda n: (depth(n), best_entry.get(n["id"], (9,))[0], -n.get("importance", 0), n["id"]))
    picked = [{"rank": i, "id": n["id"], "depth": n.get("entry_depth"), "importance": n.get("importance", 0),
               "entry": best_entry.get(n["id"], (9, []))[1], "reasons": why(n)} for i, n in enumerate(ordered[:limit], 1)]
    return {"strategy": "진입점에서 가까운 순서, 같은 거리에서는 중요도(PageRank) 순서. 테스트 파일은 제외",
            "modules": mods, "nodes": picked}


# =====================================================================
# 8-5. 그래프용 정보 ③  패키지 → 모듈 → 클래스 → 함수 계층 집계
# =====================================================================
#  같은 호출 관계를 네 가지 굵기로 모아 둔다.  화면에서는 폴더 → 파일 → 클래스 → 함수 순으로 펼쳐 볼 수 있다.
#   package   폴더 (모듈이 직접 들어 있는 폴더 단위.  더 굵게 보려면 rollup_packages(hierarchy, depth))
#   module    파일
#   class     최상위 클래스 + 모듈 수준 함수  (중첩 클래스·메서드는 바깥 클래스에 합산)
#   function  모든 함수·메서드 (중첩 함수 포함).  모듈 수준 함수는 class 층에도 같은 id 로 한 번 더 나온다
#  간선:  weight = 호출 지점 수(calls·calls_override·calls_implicit·references).
#         extends = 상속 관계 수 (package·module·class 층),  import_weight = import 문 수 (package·module 층)
#  uid 는 "층:id".  같은 단위 안의 호출은 간선이 아니라 그 단위의 internal_weight 에 모은다.
HIERARCHY_LEVELS = ("package", "module", "class", "function")


def _pkg_of(rel):
    d = rel.rpartition("/")[0]
    return d + "/" if d else "./"


def _pkg_parent(pid):
    if pid == "./":
        return None
    return _pkg_of(pid.rstrip("/"))


def _graph_metrics(unit_ids, edges, internal):
    """단위별 결합도 지표를 계산해 {uid: {...}} 로 돌려준다"""
    w_in, w_out, n_in, n_out = Counter(), Counter(), defaultdict(set), defaultdict(set)
    for e in edges:
        w_out[e["from"]] += e["weight"]
        w_in[e["to"]] += e["weight"]
        n_out[e["from"]].add(e["to"])
        n_in[e["to"]].add(e["from"])
    out = {}
    for u in unit_ids:
        fi, fo = len(n_in[u]), len(n_out[u])
        out[u] = {"in_weight": w_in[u], "out_weight": w_out[u], "internal_weight": internal.get(u, 0),
                  "fan_in": fi, "fan_out": fo, "instability": round(fo / (fi + fo), 3) if fi + fo else None}
    return out


def _edge_list(acc):
    out = []
    for (a, b), v in acc.items():
        e = {"from": a, "to": b, "weight": v["weight"], "by_type": dict(sorted(v["by_type"].items()))}
        if v["extends"]:
            e["extends"] = v["extends"]
        if v["import_weight"]:
            e["import_weight"] = v["import_weight"]
        out.append(e)
    out.sort(key=lambda e: (-e["weight"], -e.get("import_weight", 0), e["from"], e["to"]))
    return out


def build_hierarchy(files, nodes, edges):
    """edges: calls·extra·structure 간선 전부.  반환: {"levels", "units", "edges": {층: [...]}}"""
    def top_unit(nid):                                            # 모듈 바로 아래(최상위 클래스·함수)까지 올라간다
        while nid and nodes[nid]["type"] != "module":
            p = nodes[nid]["parent"]
            if p is None or nodes[p]["type"] == "module":
                return nid
            nid = p
        return None

    def unit_of(level, nid):
        n = nodes[nid]
        if level == "function":
            return nid if n["type"] in ("function", "method") else None
        if level == "class":
            return top_unit(nid)
        if level == "module":
            return n["file"]
        return _pkg_of(n["file"])

    acc = {lv: defaultdict(lambda: {"weight": 0, "by_type": Counter(), "extends": 0, "import_weight": 0})
           for lv in HIERARCHY_LEVELS}
    internal = {lv: Counter() for lv in HIERARCHY_LEVELS}
    for e in edges:
        t = e["type"]
        if t in CALL_LIKE:
            w, levels = e.get("count", 1), HIERARCHY_LEVELS
        elif t == "extends":
            w, levels = 1, ("package", "module", "class")
        elif t == "imports":
            w, levels = e.get("count", 1), ("package", "module")
        else:
            continue
        for lv in levels:
            a, b = unit_of(lv, e["from"]), unit_of(lv, e["to"])
            if a is None or b is None:
                continue
            if a == b:
                if t in CALL_LIKE:
                    internal[lv][f"{lv}:{a}"] += w
                continue
            rec = acc[lv][(f"{lv}:{a}", f"{lv}:{b}")]
            if t in CALL_LIKE:
                rec["weight"] += w
                rec["by_type"][t] += w
            elif t == "extends":
                rec["extends"] += w
            else:
                rec["import_weight"] += w

    units = {}                                                    # uid -> 단위

    def unit(level, uid_id, name, kind, parent_uid):
        u = units.setdefault(f"{level}:{uid_id}", {"uid": f"{level}:{uid_id}", "level": level, "id": uid_id,
                                                   "name": name, "kind": kind, "parent": parent_uid,
                                                   "loc": 0, "modules": 0, "functions": 0, "classes": 0,
                                                   "complexity": 0})
        return u

    for n in sorted((n for n in nodes.values() if n["type"] == "module"), key=lambda x: x["file"]):
        rel = n["file"]
        pid = _pkg_of(rel)
        while pid:                                                # 폴더 사슬
            par = _pkg_parent(pid)
            unit("package", pid, pid.rstrip("/").rpartition("/")[2] or ".", "package", f"package:{par}" if par else None)
            pid = par
        pu = units[f"package:{_pkg_of(rel)}"]
        pu["loc"] += n["loc"]
        pu["modules"] += 1
        mu = unit("module", rel, rel.rpartition("/")[2], "module", f"package:{_pkg_of(rel)}")
        mu["loc"] = n["loc"]
        mu["modules"] = 1
    for nid, n in sorted(nodes.items()):
        if n["type"] == "module":
            continue
        mod_rel = n["file"]
        tu = top_unit(nid)
        is_func = n["type"] in ("function", "method")
        if tu == nid:                                             # 최상위 클래스·함수
            cu = unit("class", nid, n["name"], "class" if n["type"] == "class" else "function", f"module:{mod_rel}")
            cu["loc"] = n["loc"]
        if is_func:
            parent = nodes[n["parent"]]
            if parent["type"] == "module":
                par_uid = f"class:{nid}"
            elif parent["type"] == "class":
                par_uid = f"class:{tu}"
            else:
                par_uid = f"function:{n['parent']}"
            fu = unit("function", nid, n["name"], n["type"], par_uid)
            fu["loc"], fu["functions"], fu["complexity"] = n["loc"], 1, n.get("complexity", 0)
            for lv_uid in (f"module:{mod_rel}", f"package:{_pkg_of(mod_rel)}", f"class:{tu}"):
                if lv_uid in units:
                    units[lv_uid]["functions"] += 1
                    units[lv_uid]["complexity"] += n.get("complexity", 0)
        else:
            for lv_uid in (f"module:{mod_rel}", f"package:{_pkg_of(mod_rel)}"):
                units[lv_uid]["classes"] += 1
            if tu == nid:
                units[f"class:{nid}"]["classes"] = 1
    # 폴더 단위의 loc 등은 '그 폴더에 직접 들어 있는 파일' 기준이고, 하위 폴더까지 합친 값은 total_* 로 따로 둔다
    for lv in ("package",):
        for u in units.values():
            if u["level"] == lv:
                u["total_loc"], u["total_modules"] = u["loc"], u["modules"]
        for u in sorted((x for x in units.values() if x["level"] == lv), key=lambda x: -len(x["id"])):
            if u["parent"]:
                units[u["parent"]]["total_loc"] += u["total_loc"]
                units[u["parent"]]["total_modules"] += u["total_modules"]

    out_edges = {lv: _edge_list(acc[lv]) for lv in HIERARCHY_LEVELS}
    for lv in HIERARCHY_LEVELS:
        ids = [u["uid"] for u in units.values() if u["level"] == lv]
        m = _graph_metrics(ids, out_edges[lv], internal[lv])
        for uid in ids:
            units[uid].update(m[uid])
    kids = Counter(u["parent"] for u in units.values() if u["parent"])
    for u in units.values():
        u["children"] = kids[u["uid"]]
    ordered = sorted(units.values(), key=lambda u: (HIERARCHY_LEVELS.index(u["level"]), u["id"]))
    return {"levels": list(HIERARCHY_LEVELS), "units": ordered, "edges": out_edges}


def rollup_packages(hierarchy, depth):
    """폴더 층을 경로 깊이 `depth` 까지만 남기고 합친다 (depth=0 은 프로젝트 전체, 1 은 최상위 폴더 단위).
    반환: {"depth", "units", "edges"}  — 합쳐지며 같은 단위 안이 된 호출은 internal_weight 로 옮겨진다"""
    def cut(pid):
        parts = [p for p in pid.split("/") if p]
        return "/".join(parts[:depth]) + "/" if parts[:depth] else "./"

    pk = {u["uid"]: u for u in hierarchy["units"] if u["level"] == "package"}
    units, internal = {}, Counter()
    for u in pk.values():
        c = cut(u["id"])
        r = units.setdefault(f"package:{c}", {"uid": f"package:{c}", "level": "package", "id": c, "loc": 0,
                                              "modules": 0, "functions": 0, "classes": 0, "complexity": 0})
        for k in ("loc", "modules", "functions", "classes", "complexity"):
            r[k] += u[k]
        internal[r["uid"]] += u["internal_weight"]
    acc = defaultdict(lambda: {"weight": 0, "by_type": Counter(), "extends": 0, "import_weight": 0})
    for e in hierarchy["edges"]["package"]:
        a, b = f"package:{cut(pk[e['from']]['id'])}", f"package:{cut(pk[e['to']]['id'])}"
        if a == b:
            internal[a] += e["weight"]
            continue
        rec = acc[(a, b)]
        rec["weight"] += e["weight"]
        for t, v in e["by_type"].items():
            rec["by_type"][t] += v
        rec["extends"] += e.get("extends", 0)
        rec["import_weight"] += e.get("import_weight", 0)
    edges = _edge_list(acc)
    m = _graph_metrics(list(units), edges, internal)
    for uid, u in units.items():
        u.update(m[uid])
    return {"depth": depth, "units": sorted(units.values(), key=lambda u: u["id"]), "edges": edges}


# =====================================================================
# 9. 실행
# =====================================================================
def analyze(root):
    root = Path(root).resolve()
    skipped = []
    files = parse_files(root, skipped)
    nodes, top, methods, nested, classes, defs, class_nodes = collect_defs(files)
    aliases = build_import_aliases(root, files)                              # src 레이아웃 등 import 이름 보정
    stars = {m: [] for m in files}
    module_imports = {m: collect_imports(m, rel, t, aliases, stars[m]) for m, (rel, t) in files.items()}
    stars = {m: v for m, v in stars.items() if v}
    var_defs = collect_module_var_defs(files)                                # 모듈 수준 변수 (타입 추적용)
    all_lists = collect_all_lists(files)                                     # 모듈의 __all__
    star_info = expand_star_imports(files, module_imports, top, var_defs, all_lists, stars)   # from x import * 로 들어오는 이름
    exports = compute_reexports(module_imports, top, var_defs)               # 재수출 이름 -> 실제 정의
    canonicalize_imports(module_imports, exports)
    inject_exports(exports, top, classes)
    class_bases, class_ext_bases = collect_class_bases(class_nodes, top, classes, module_imports)
    call_stats = {}
    edges, ext_edges, unresolved, extra_edges = collect_calls(files, top, methods, classes, class_bases,
                                                              class_ext_bases, root.name, module_imports, var_defs,
                                                              exports, defs, class_nodes, nodes, call_stats)
    structure = collect_structure(files, nodes, top, methods, class_bases, class_ext_bases, aliases)
    for (cm, cq) in class_nodes:                                             # 인스턴스를 만들 때 실제로 실행되는 __init__ (MRO 기준, 없으면 None)
        nodes[f"{files[cm][0]}::{cq}"]["constructor"] = find_method_up_chain(cm, cq, "__init__", methods, class_bases)
    dynamic = collect_dynamic(files, nodes, defs, methods, module_imports, top)
    import_edges = [e for e in structure if e["type"] == "imports"]
    cycles = find_import_cycles(import_edges)
    for c in cycles:
        for rel in c["modules"]:
            nodes[rel].setdefault("import_cycles", []).append(c["id"])
    entries = detect_entry_points(root, files, nodes, defs, top, methods, classes, class_nodes, class_bases,
                                  module_imports, var_defs, exports, all_lists, aliases)
    all_edges = edges + extra_edges + structure
    reachable = annotate_entry_depth(nodes, entries, all_edges)
    compute_importance(nodes, all_edges)
    reading = build_reading_order(nodes, entries)
    hierarchy = build_hierarchy(files, nodes, all_edges)
    struct_count = Counter(e["type"] for e in structure)
    extra_count = Counter((e["type"], e["via"]) for e in extra_edges)
    nested_nodes = sum(1 for n in nodes.values() if n.get("nested"))
    via_counts = Counter(e.get("via") or "direct" for e in edges if e["type"] == "calls")

    n_static = sum(1 for e in edges if e["type"] == "calls")
    n_override = sum(1 for e in edges if e["type"] == "calls_override")
    ext_count = Counter()
    for e in ext_edges:
        ext_count[e["category"]] += e["count"]
    unk_count = Counter(u["category"] for u in unresolved)
    total = n_static + sum(ext_count.values()) + sum(unk_count.values())
    n_inferred_int = sum(1 for e in edges if e["type"] == "calls" and e.get("via") in _TYPE_VIA)   # 타입 추적으로 연결된 것
    n_module_path = sum(1 for e in edges if e.get("via") in ("module_path", "module_var") and e["type"] == "calls")
    conf = Counter()
    for e in ext_edges:
        conf[e["confidence"]] += e["count"]
    sure = (n_static - n_inferred_int) + conf["certain"]               # 확실: 코드만 보고 결정된 것
    inferred = n_inferred_int + conf["inferred"]                       # 타입 추적으로 결정된 것
    guess = conf["heuristic"] + conf["weak"]                           # 메서드 이름만 보고 추정한 것

    name_count = Counter(name for ms in methods.values() for name in ms)
    guessable = sum(1 for u in unresolved if u["category"] == "unknown_receiver"
                    and name_count.get(u["expr"].split(".")[-1].split("(")[0], 0) == 1)

    summary = {
        "files": len(files), "nodes": len(nodes) - len(files), "module_nodes": len(files), "total_calls": total,
        "internal_resolved": n_static, "override_edges": n_override,
        "external": dict(ext_count), "unresolved": dict(unk_count),
        "classified_rate_sure": round(sure / total, 4) if total else 0,
        "classified_rate_incl_inferred": round((sure + inferred) / total, 4) if total else 0,
        "classified_rate_incl_guess": round((sure + inferred + guess) / total, 4) if total else 0,
        "type_inference": {"internal_edges_added": n_inferred_int, "external_classified": conf["inferred"]},
        "edge_via": dict(via_counts), "structure_edges": dict(struct_count),
        "implicit_and_reference_edges": {f"{t}:{v}": c for (t, v), c in sorted(extra_count.items())},
        "nested_nodes": nested_nodes,
        "skipped_files": len(skipped),
        "import_aliases": aliases, "module_path_edges": n_module_path,
        "nested_defs_not_collected": nested,
        "unknown_receiver_with_unique_method_name": guessable,
        "import_scopes": dict(Counter(e["scope"] for e in import_edges)),
        "import_cycles": dict(Counter(c["kind"] for c in cycles)),
        "star_imports": star_info,
        "entry_points": dict(Counter(e["kind"] for e in entries)),
        "reachable_from_entries": reachable,
        "dynamic_calls": dict(Counter(r["kind"] for r in dynamic)),
        "type_inference_return_bodies": call_stats.get("return_types_inferred", 0),
        "reading_order": {"modules": len(reading["modules"]), "nodes": len(reading["nodes"])},
        "hierarchy": {lv: sum(1 for u in hierarchy["units"] if u["level"] == lv) for lv in HIERARCHY_LEVELS},
    }
    ext_nodes = [{"id": f"EXTERNAL::{c}", "type": "external", "category": c} for c in EXTERNAL_CATS]
    result = {"schema_version": SCHEMA_VERSION, "nodes": list(nodes.values()), "edges": all_edges,
              "entry_points": entries, "import_cycles": cycles, "dynamic_calls": dynamic, "reading_order": reading,
              "hierarchy": hierarchy,
              "external_nodes": ext_nodes, "external_edges": ext_edges,
              "unresolved": unresolved, "skipped_files": skipped, "summary": summary}
    return root, result


def print_summary(root, result):
    s = result["summary"]
    total = s["total_calls"]
    pct = lambda x: f"{x / total * 100:5.1f}%" if total else "  0.0%"
    print(f"\n===== {root.name} =====")
    print(f"분석한 파일 {s['files']}개 / 노드 {s['nodes']}개 / 전체 호출 {total}건")
    print(f"\n[A] 프로젝트 내부로 연결 성공: {s['internal_resolved']}건 ({pct(s['internal_resolved'])})"
          f"   + 오버라이드 후보 연결 {s['override_edges']}건")
    print("\n[B] 외부로 분류")
    for c in EXTERNAL_CATS:
        if s["external"].get(c):
            print(f"   {c:20s} {s['external'][c]:5d}건 ({pct(s['external'][c])})  {CAT_LABEL[c]}")
    print("\n[C] 아직 확정 못 한 것")
    for c, v in sorted(s["unresolved"].items(), key=lambda x: -x[1]):
        print(f"   {c:20s} {v:5d}건 ({pct(v)})  {CAT_LABEL[c]}")
    ti = s["type_inference"]
    print(f"\n분류 완료율  확실한 것만 {s['classified_rate_sure'] * 100:.1f}%"
          f"  /  타입 추적 포함 {s['classified_rate_incl_inferred'] * 100:.1f}%"
          f"  /  이름 추정까지 포함 {s['classified_rate_incl_guess'] * 100:.1f}%")
    print(f"타입 추적으로 결정된 호출: 내부 연결 {ti['internal_edges_added']}건 + 외부 분류 {ti['external_classified']}건")
    if s["import_aliases"]:
        shown = ", ".join(f"{k} -> {v or '(분석 폴더)'}" for k, v in s["import_aliases"].items())
        print(f"import 루트 보정: {shown}   (점 표기로 모듈 트리를 따라가 연결한 호출 {s['module_path_edges']}건)")
    print(f"수집한 중첩 함수/클래스: {s['nested_nodes']}개 (수집하지 못한 정의 {s['nested_defs_not_collected']}개: if/try 안의 조건부 정의, 이름이 겹치는 중첩 정의)")
    extra = s["implicit_and_reference_edges"]
    if extra:
        print("호출이 아닌 연결: " + ", ".join(f"{k} {v}" for k, v in extra.items()))
    if s["import_scopes"]:
        print("import 간선 종류: " + ", ".join(f"{k} {v}" for k, v in s["import_scopes"].items()))
    for c in result["import_cycles"][:5]:
        print(f"순환 import({c['kind']}): " + " -> ".join(c["path"]))
    if len(result["import_cycles"]) > 5:
        print(f"   ... 외 {len(result['import_cycles']) - 5}개")
    si = s["star_imports"]
    if si["statements"]:
        print(f"from x import *: {si['statements']}건 (프로젝트 안 {si['project_statements']}건, 이름 {si['names_bound']}개 연결)")
    if s["entry_points"]:
        print("진입점: " + ", ".join(f"{k} {v}" for k, v in s["entry_points"].items())
              + f"   (진입점에서 호출로 닿는 함수·클래스 {s['reachable_from_entries']}개)")
    else:
        print("진입점: 찾지 못함")
    if result["reading_order"]["modules"]:
        print("읽는 순서(파일): " + " -> ".join(m["id"] for m in result["reading_order"]["modules"][:5])
              + (" ..." if len(result["reading_order"]["modules"]) > 5 else ""))
    print("계층: " + ", ".join(f"{k} {v}" for k, v in s["hierarchy"].items()))
    if result["skipped_files"]:
        print(f"\n[주의] 파싱하지 못해 건너뛴 파일 {len(result['skipped_files'])}개 (분석에서 빠졌습니다)")
        for sk in result["skipped_files"][:8]:
            print(f"   {sk['file']}: {sk['error']}")
    print(f"타입 추론 영역 중 메서드 이름이 프로젝트에서 유일한 것(이름 기반 추정 연결 후보): "
          f"{s['unknown_receiver_with_unique_method_name']}건")
    for cat in ("project_unresolved", "local_callable"):
        items = [u for u in result["unresolved"] if u["category"] == cat]
        if items:
            print(f"\n[{cat}] 예시 (최대 8개)")
            for u in items[:8]:
                print(f"   {u['from']}  ->  {u['expr']}  (줄 {u['line']})")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    root, result = analyze(target)
    print_summary(root, result)
    out = Path(f"graph_{root.name}.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{out} 저장 완료")


if __name__ == "__main__":
    main()
