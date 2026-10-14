"""
KnitCode 코드 구조 분석기 v2

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
"""
import ast
import builtins
import importlib.util
import json
import os
import sys
import sysconfig
from collections import Counter, defaultdict
from pathlib import Path

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


def parse_files(root):
    files = {}  # 모듈명 -> (상대경로, AST)
    for path in root.rglob("*.py"):
        if any(p in SKIP_DIRS for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"[건너뜀] {path}: {e}")
            continue
        files[module_name(root, path)] = (path.relative_to(root).as_posix(), tree)
    return files


# =====================================================================
# 2. 함수/클래스 정의 수집 (고유 ID 부여)
# =====================================================================
def collect_defs(files):
    nodes, top, methods = {}, {}, {}
    nested = 0
    for mod, (rel, tree) in files.items():
        top[mod] = {}
        direct = set()
        for n in tree.body:
            direct.add(id(n))
            if isinstance(n, FUNC_TYPES):
                nid = f"{rel}::{n.name}"
                nodes[nid] = {"id": nid, "type": "function", "file": rel, "line": n.lineno}
                top[mod][n.name] = nid
            elif isinstance(n, ast.ClassDef):
                cid = f"{rel}::{n.name}"
                nodes[cid] = {"id": cid, "type": "class", "file": rel, "line": n.lineno}
                top[mod][n.name] = cid
                methods[(mod, n.name)] = {}
                for m in n.body:
                    direct.add(id(m))
                    if isinstance(m, FUNC_TYPES):
                        mid = f"{rel}::{n.name}.{m.name}"
                        nodes[mid] = {"id": mid, "type": "method", "file": rel, "line": m.lineno}
                        methods[(mod, n.name)][m.name] = mid
        for n in ast.walk(tree):                       # 수집되지 않는 중첩 정의 개수(투명성용)
            if isinstance(n, FUNC_TYPES + (ast.ClassDef,)) and id(n) not in direct:
                nested += 1
    classes = {k: k for k in methods}           # (모듈, 클래스명) -> 실제 정의 위치. 재수출 이름도 나중에 여기에 추가된다
    return nodes, top, methods, nested, classes


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


def collect_imports(mod, rel, tree, aliases=None):
    """코드에서 쓰는 이름 -> ("module", 모듈경로, None, 0) 또는 ("name", 모듈경로, 원래이름, 상대import단계)
    aliases: 절대 import 에 쓰이는 패키지 이름을 분석기의 모듈 이름으로 바꾸는 표 (build_import_aliases)"""
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


def compute_reexports(module_imports, top):
    """(모듈, 이름) -> (실제로 정의된 모듈, 이름).  프로젝트 안의 다른 모듈에서 가져온 이름을 끝까지 따라간다
    예) requests/__init__.py 의 `from .api import get`  ->  ("requests", "get") -> ("requests.api", "get")"""
    def follow(mp, nm, seen):
        if nm in top.get(mp, {}):
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
            if imp[0] != "name" or name in top.get(mod, {}):
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
        top.setdefault(mod, {}).setdefault(name, top[dm][dn])
        if (dm, dn) in classes:
            classes.setdefault((mod, name), classes[(dm, dn)])


def resolve_dotted(func, imports, top, classes, methods, class_bases):
    """import 한 모듈에서 시작하는 점 표기 호출을 모듈 트리를 따라가며 해석
    예) pkg.utils.helper(...), pkg.Class(...), pkg.Class.method(...)"""
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
            elif _join_mod(m, part) in top:
                cur = ("module", _join_mod(m, part))
            else:
                return None
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


def collect_class_bases(files, top, classes, module_imports):
    """class_bases: (모듈,클래스) -> 프로젝트 안 부모들 / class_ext_bases: -> 프로젝트 밖 부모 이름들"""
    class_bases, class_ext_bases = {}, {}
    for mod, (rel, tree) in files.items():
        imports = module_imports[mod]
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                inner, outer = [], []
                for b in n.bases:
                    ref = resolve_class_ref(b, mod, imports, top, classes)
                    if ref:
                        inner.append(ref)
                    else:
                        name = ast.unparse(b.value if isinstance(b, ast.Subscript) else b)
                        if name != "object":
                            outer.append(name)
                class_bases[(mod, n.name)] = inner
                class_ext_bases[(mod, n.name)] = outer
    return class_bases, class_ext_bases


def find_method_up_chain(mod, cls, attr, methods, class_bases, seen=None):
    """현재 클래스에 없으면 부모로 타고 올라가며 메서드를 찾는다"""
    seen = seen or set()
    if (mod, cls) in seen:
        return None
    seen.add((mod, cls))
    found = methods.get((mod, cls), {}).get(attr)
    if found:
        return found
    for bm, bc in class_bases.get((mod, cls), []):
        found = find_method_up_chain(bm, bc, attr, methods, class_bases, seen)
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



def collect_module_vars(files):
    """모듈 최상위의 'X = str', 'X = {...}' 같은 대입을 보고 내장 값/별칭인지 기록: (모듈, 이름) -> 'builtin'"""
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
# 5-2. 타입 추적 (매개변수 타입힌트 / 외부 호출 결과를 담은 변수 / self.속성)
# =====================================================================
# 타입 표현:  ("project", (모듈, 클래스명))   프로젝트 안 클래스의 인스턴스
#             ("ext", 출처, 패키지)           출처: builtin | stdlib | third_party
#
# 원칙: '확실할 때만' 타입을 부여한다. 같은 이름이 다른 방식으로도 바인딩되면(루프 변수, 튜플 풀기,
#       중첩 함수 인자, 서로 다른 타입의 재대입 등) 그 이름은 포기한다. 잘못된 연결을 만드는 것보다
#       미확정으로 남기는 편이 영향도 분석에 안전하기 때문이다.
_VALUE_BUILTIN_NODES = (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple, ast.JoinedStr,
                        ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
_BUILTIN_T = ("ext", "builtin", "builtins")
_NEUTRAL = "NEUTRAL"        # `self.x = None`, `x += 1` 처럼 타입 판단에 영향을 주지 않는 대입
_TYPE_VIA = ("type_hint", "local_assign", "self_attr")


def make_env(mod, imports, top, classes, project_roots, module_imports, module_vars):
    return {"mod": mod, "imports": imports, "top": top, "classes": classes,
            "project_roots": project_roots, "module_imports": module_imports, "module_vars": module_vars}


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _ext_type_of_name(name, env):
    imp = env["imports"].get(name)
    if imp:
        origin, pkg = origin_of_import(imp, env["top"], env["project_roots"],
                                       env["module_imports"], env["module_vars"])
        return None if origin == "project" else ("ext", origin, pkg)
    if name in BUILTIN_NAMES:
        return _BUILTIN_T
    return None


def ann_to_type(ann, env):
    """타입힌트 표현식 -> 타입. 모호하면 None (Union[A, B] 처럼 둘 이상이면 포기)"""
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
        if nm in ("Optional", "Union"):
            sl = ann.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
            elts = [e for e in elts if not _is_none(e)]
            return ann_to_type(elts[0], env) if len(elts) == 1 else None
        ann = base                                                              # List[Foo] 등은 바깥 타입만 본다
    ref = resolve_class_ref(ann, env["mod"], env["imports"], env["top"], env["classes"])
    if ref:
        return ("project", ref)
    root = root_of(ann)
    return _ext_type_of_name(root.id, env) if isinstance(root, ast.Name) else None


def value_to_type(val, env, known):
    """대입되는 값의 타입. known: 같은 함수 안에서 타입이 확정된 이름들"""
    if isinstance(val, _VALUE_BUILTIN_NODES):
        return _BUILTIN_T
    if isinstance(val, ast.Name):
        return known.get(val.id)
    if isinstance(val, ast.Call):
        ref = resolve_class_ref(val.func, env["mod"], env["imports"], env["top"], env["classes"])
        if ref:
            return ("project", ref)
        parts = dotted_parts(val.func)                      # a.b.C(...) 처럼 이름만으로 이루어진 호출만 본다
        if parts:
            if len(parts) == 1 and parts[0] in BUILTIN_NAMES and parts[0] not in env["imports"]:
                return _BUILTIN_T                           # open(...), dict(...), len(...) 등
            if parts[-1][:1].isupper():                     # 대문자로 시작하면 클래스 생성자로 본다 (argparse.ArgumentParser())
                return _ext_type_of_name(parts[0], env)     # 소문자 함수(os.getcwd 등)는 반환 타입을 몰라 포기
    return None


def _merge(types):
    """모든 대입이 같은 타입일 때만 그 타입. 하나라도 모르거나 다르면 None"""
    ts = [t for t in types if t != _NEUTRAL]
    if not ts or any(t is None for t in ts):
        return None
    return ts[0] if all(t == ts[0] for t in ts) else None


def _all_args(fn):
    a = fn.args
    out = list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
    if a.vararg:
        out.append(a.vararg)
    if a.kwarg:
        out.append(a.kwarg)
    return out


def _site_type(value, env, known):
    return _NEUTRAL if _is_none(value) else value_to_type(value, env, known)


def collect_name_types(fn, env):
    """함수 안의 이름(매개변수·지역변수) -> 타입.  반환: (types, src, shadow)
    src: 'type_hint' | 'local_assign',  shadow: import 한 이름을 가릴 수 있는 지역 이름 전체"""
    a = fn.args
    params = {}
    for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
        params[arg.arg] = ann_to_type(arg.annotation, env)
    if a.vararg:
        params[a.vararg.arg] = _BUILTIN_T                  # *args 는 항상 tuple
    if a.kwarg:
        params[a.kwarg.arg] = _BUILTIN_T                   # **kwargs 는 항상 dict
    known = {k: v for k, v in params.items() if v}

    stores, tainted, sites = Counter(), set(), defaultdict(list)
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

    for node in ast.walk(fn):                                               # '타입이 보이는' 단순 대입 지점
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            sites[node.targets[0].id].append(_site_type(node.value, env, known))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            sites[node.target.id].append(ann_to_type(node.annotation, env))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            sites[node.target.id].append(_NEUTRAL)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for it in node.items:
                if isinstance(it.optional_vars, ast.Name):
                    t = value_to_type(it.context_expr, env, known)
                    sites[it.optional_vars.id].append(None if (t and t[0] == "project") else t)   # __enter__ 반환값은 모른다

    types, src = {}, {}
    for name in set(params) | set(sites):
        if name in tainted:
            continue
        s = sites.get(name, [])
        if stores[name] != len(s):                          # 단순 대입이 아닌 바인딩(루프 변수, 튜플 풀기 등)이 섞여 있음
            continue
        if name in params:
            if params[name] is None:
                continue
            cand = [params[name]] + s
        else:
            cand = s
        t = _merge(cand)
        if t:
            types[name] = t
            src[name] = "type_hint" if name in params else "local_assign"
    shadow = set(params) | set(stores) | (tainted - imported_here)        # import 한 이름을 가리는 지역 이름들
    return types, src, shadow


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
        env = envs[member[0]]
        method_names |= set(methods.get(member, {}))
        for st in node.body:                                                # 클래스 본문의 `x = Foo()`, `x: Foo`
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                stores[st.targets[0].id] += 1
                sites[st.targets[0].id].append(_site_type(st.value, env, {}))
            elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                stores[st.target.id] += 1
                sites[st.target.id].append(ann_to_type(st.annotation, env))
        for fn in node.body:
            if not isinstance(fn, FUNC_TYPES):
                continue
            known, _, _ = collect_name_types(fn, env)
            for n in ast.walk(fn):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self" \
                        and isinstance(n.ctx, (ast.Store, ast.Del)):
                    stores[n.attr] += 1
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Attribute) \
                        and isinstance(n.targets[0].value, ast.Name) and n.targets[0].value.id == "self":
                    sites[n.targets[0].attr].append(_site_type(n.value, env, known))
                elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Attribute) \
                        and isinstance(n.target.value, ast.Name) and n.target.value.id == "self":
                    sites[n.target.attr].append(ann_to_type(n.annotation, env))
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


def resolve_by_type(func, cls, methods, class_bases, name_types, name_src, self_types, ctor=None):
    """v2 방식으로 못 푼 호출을, 추적한 타입으로 한 번 더 시도. 반환: (호출대상, 근거, 수신 클래스)
    ctor=(모듈, import표, top, classes): `Foo(...).method()` 처럼 생성자 호출 결과에 바로 붙은 메서드를 풀 때 쓴다"""
    if not isinstance(func, ast.Attribute):
        return None, None, None
    v, t, via = func.value, None, None
    if isinstance(v, ast.Name):
        t, via = name_types.get(v.id), name_src.get(v.id)
    elif cls and isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
        t, via = self_types.get(v.attr), "self_attr"
    elif ctor and isinstance(v, ast.Call):                    # 생성자 호출 직후라 타입이 확실하다
        ref = resolve_class_ref(v.func, ctor[0], ctor[1], ctor[2], ctor[3])
        if ref:
            t, via = ("project", ref), "constructor"
    if t and t[0] == "project":
        found = find_method_up_chain(t[1][0], t[1][1], func.attr, methods, class_bases)
        if found:
            return found, via, t[1]
    return None, None, None



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
            for bm, bc in class_bases.get((mod, cls), []):
                found = find_method_up_chain(bm, bc, func.attr, methods, class_bases)
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
        tinfo = None                                                         # 타입을 추적해 둔 변수/속성이면 그 타입으로 분류
        if v is root and name in ctx["name_types"]:
            tinfo = ctx["name_types"][name]
        elif cls and isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self":
            tinfo = ctx["self_types"].get(v.attr)
        if tinfo:
            if tinfo[0] == "ext":
                return (tinfo[1], tinfo[2], expr, "inferred")
            res = ext_parent(tinfo[1][0], tinfo[1][1], expr)
            return (res[0], res[1], res[2], "inferred" if res[0] == "inherited_external" else res[3])

        if name == "self" and cls:
            if v is root:                                                    # self.method(...)
                ext = chain_ext_bases(mod, cls, ctx["class_bases"], ctx["class_ext_bases"])
                if ext:
                    return ("inherited_external", ext[0].split(".")[0], f"self.{attr} <- 부모 {', '.join(ext)}", "certain")
                return ("local_callable", None, expr, "unknown")             # self.콜백() 처럼 속성에 담긴 함수
            return unknown_receiver(attr)                                    # self.속성.method(...)
        if name in imports and name not in ctx["shadow"]:
            cat, pkg = from_import(imports[name])
            return (cat, pkg, expr, "certain")
        if v is not root:                                                    # a.b().c() 처럼 호출 결과에 붙은 메서드
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
def collect_calls(files, top, methods, classes, class_bases, class_ext_bases, root_name, module_imports):
    module_vars = collect_module_vars(files)
    edges, unresolved = [], []
    ext_agg = {}
    all_desc = build_descendants(class_bases)
    project_roots = {m.split(".")[0] for m in files if m} | {root_name}
    project_method_names = Counter(name for ms in methods.values() for name in ms)
    envs = {m: make_env(m, module_imports[m], top, classes, project_roots, module_imports, module_vars)
            for m in files}
    class_nodes = {(m, n.name): n for m, (_, t) in files.items() for n in t.body if isinstance(n, ast.ClassDef)}
    self_type_cache = {}

    def self_types_of(mod, cls):
        key = (mod, cls)
        if key not in self_type_cache:
            self_type_cache[key] = class_self_types(key, class_nodes, class_bases, all_desc, methods, envs)
        return self_type_cache[key]

    for mod, (rel, tree) in files.items():
        imports = module_imports[mod]
        targets = []
        for n in tree.body:
            if isinstance(n, FUNC_TYPES):
                targets.append((n, None, f"{rel}::{n.name}"))
            elif isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, FUNC_TYPES):
                        targets.append((m, n.name, f"{rel}::{n.name}.{m.name}"))

        for fn, cls, caller in targets:
            local_vars = collect_local_var_types(fn, mod, imports, top, classes)
            name_types, name_src, shadow = collect_name_types(fn, envs[mod])
            self_types = self_types_of(mod, cls) if cls else {}
            ctx = {"mod": mod, "cls": cls, "imports": imports, "top": top, "methods": methods,
                   "class_bases": class_bases, "class_ext_bases": class_ext_bases,
                   "local_vars": local_vars, "project_roots": project_roots,
                   "project_method_names": project_method_names,
                   "module_imports": module_imports, "module_vars": module_vars,
                   "name_types": name_types, "self_types": self_types, "shadow": shadow}
            for c in ast.walk(fn):
                if not isinstance(c, ast.Call):
                    continue
                callee = resolve_call(c.func, mod, cls, imports, top, methods, class_bases, local_vars, shadow)
                via = recv = None
                if not callee:                                           # v2 방식으로 못 풀면 추적한 타입으로 재시도
                    callee, via, recv = resolve_by_type(c.func, cls, methods, class_bases, name_types, name_src,
                                                        self_types, ctor=(mod, imports, top, classes))
                if not callee:                                           # 모듈 트리를 따라가는 점 표기 (pkg.sub.func)
                    parts = dotted_parts(c.func)
                    if parts and len(parts) > 1 and parts[0] not in shadow:
                        callee = resolve_dotted(c.func, imports, top, classes, methods, class_bases)
                        via = "module_path" if callee else None
                if callee:
                    edge = {"from": caller, "to": callee, "type": "calls", "line": c.lineno}
                    if via:
                        edge["via"] = via
                    edges.append(edge)
                    if via == "type_hint":                               # 선언된 타입의 자식이 오버라이드했을 수 있다
                        for d in all_desc(recv):
                            target = methods.get(d, {}).get(c.func.attr)
                            if target and target != callee:
                                edges.append({"from": caller, "to": target, "type": "calls_override",
                                              "line": c.lineno, "via": via})

                # self.method() 은 자식 클래스가 오버라이드한 버전이 실제로 실행될 수 있다
                overridden = False
                if cls and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) \
                        and c.func.value.id == "self":
                    for d in all_desc((mod, cls)):
                        target = methods.get(d, {}).get(c.func.attr)
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
    return edges, list(ext_agg.values()), unresolved


# =====================================================================
# 9. 실행
# =====================================================================
def analyze(root):
    root = Path(root).resolve()
    files = parse_files(root)
    nodes, top, methods, nested, classes = collect_defs(files)
    aliases = build_import_aliases(root, files)                              # src 레이아웃 등 import 이름 보정
    module_imports = {m: collect_imports(m, rel, t, aliases) for m, (rel, t) in files.items()}
    exports = compute_reexports(module_imports, top)                         # 재수출 이름 -> 실제 정의
    canonicalize_imports(module_imports, exports)
    inject_exports(exports, top, classes)
    class_bases, class_ext_bases = collect_class_bases(files, top, classes, module_imports)
    edges, ext_edges, unresolved = collect_calls(files, top, methods, classes, class_bases, class_ext_bases,
                                                 root.name, module_imports)

    n_static = sum(1 for e in edges if e["type"] == "calls")
    n_override = sum(1 for e in edges if e["type"] == "calls_override")
    ext_count = Counter()
    for e in ext_edges:
        ext_count[e["category"]] += e["count"]
    unk_count = Counter(u["category"] for u in unresolved)
    total = n_static + sum(ext_count.values()) + sum(unk_count.values())
    n_inferred_int = sum(1 for e in edges if e["type"] == "calls" and e.get("via") in _TYPE_VIA)   # 타입 추적으로 연결된 것
    n_module_path = sum(1 for e in edges if e.get("via") == "module_path")
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
        "files": len(files), "nodes": len(nodes), "total_calls": total,
        "internal_resolved": n_static, "override_edges": n_override,
        "external": dict(ext_count), "unresolved": dict(unk_count),
        "classified_rate_sure": round(sure / total, 4) if total else 0,
        "classified_rate_incl_inferred": round((sure + inferred) / total, 4) if total else 0,
        "classified_rate_incl_guess": round((sure + inferred + guess) / total, 4) if total else 0,
        "type_inference": {"internal_edges_added": n_inferred_int, "external_classified": conf["inferred"]},
        "import_aliases": aliases, "module_path_edges": n_module_path,
        "nested_defs_not_collected": nested,
        "unknown_receiver_with_unique_method_name": guessable,
    }
    ext_nodes = [{"id": f"EXTERNAL::{c}", "type": "external", "category": c} for c in EXTERNAL_CATS]
    result = {"nodes": list(nodes.values()), "edges": edges,
              "external_nodes": ext_nodes, "external_edges": ext_edges,
              "unresolved": unresolved, "summary": summary}
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
    print(f"수집 안 되는 중첩 함수/클래스 정의: {s['nested_defs_not_collected']}개")
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
