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
    classes = set(methods)                      # (모듈, 클래스명) 집합 -> 함수와 클래스를 구분하는 데 쓴다
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


def collect_imports(mod, rel, tree):
    """코드에서 쓰는 이름 -> ("module", 모듈경로, None, 0) 또는 ("name", 모듈경로, 원래이름, 상대import단계)"""
    table = {}
    is_pkg = rel.endswith("__init__.py")
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                key = a.asname or a.name.split(".")[0]
                table[key] = ("module", a.name if a.asname else a.name.split(".")[0], None, 0)
        elif isinstance(n, ast.ImportFrom):
            base = resolve_from(mod, is_pkg, n.level, n.module)
            for a in n.names:
                table[a.asname or a.name] = ("name", base, a.name, n.level)
    return table


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
            return (mod, n)
        imp = imports.get(n)
        if imp and imp[0] == "name" and (imp[1], imp[2]) in classes:   # as 별칭이어도 원래 이름으로 찾는다
            return (imp[1], imp[2])
        return None
    root, mid, last = parts[0], parts[1:-1], parts[-1]
    imp = imports.get(root)
    if not imp:
        return None
    if imp[0] == "module":
        modpath = ".".join([imp[1]] + mid)
    else:
        modpath = ".".join(p for p in [imp[1], imp[2]] + mid if p)
    if (modpath, last) in classes:
        return (modpath, last)
    return None


def collect_class_bases(files, top, classes):
    """class_bases: (모듈,클래스) -> 프로젝트 안 부모들 / class_ext_bases: -> 프로젝트 밖 부모 이름들"""
    class_bases, class_ext_bases = {}, {}
    for mod, (rel, tree) in files.items():
        imports = collect_imports(mod, rel, tree)
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
# 6. 호출 대상 해석 (프로젝트 안에서 찾을 수 있는 것)
# =====================================================================
def resolve_call(func, mod, cls, imports, top, methods, class_bases, local_vars):
    if isinstance(func, ast.Name):
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
        if obj in top.get(mod, {}) and (mod, obj) in methods:          # ParentClass.__init__(self) 등
            return find_method_up_chain(mod, obj, attr, methods, class_bases)
        if obj in local_vars:
            vm, vc = local_vars[obj]
            found = find_method_up_chain(vm, vc, attr, methods, class_bases)
            if found:
                return found

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
        if name == "self" and cls:
            if v is root:                                                    # self.method(...)
                ext = chain_ext_bases(mod, cls, ctx["class_bases"], ctx["class_ext_bases"])
                if ext:
                    return ("inherited_external", ext[0].split(".")[0], f"self.{attr} <- 부모 {', '.join(ext)}", "certain")
                return ("local_callable", None, expr, "unknown")             # self.콜백() 처럼 속성에 담긴 함수
            return unknown_receiver(attr)                                    # self.속성.method(...)
        if name in imports:
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
def collect_calls(files, top, methods, classes, class_bases, class_ext_bases, root_name):
    module_imports = {m: collect_imports(m, rel, tree) for m, (rel, tree) in files.items()}
    module_vars = collect_module_vars(files)
    edges, unresolved = [], []
    ext_agg = {}
    all_desc = build_descendants(class_bases)
    project_roots = {m.split(".")[0] for m in files if m} | {root_name}
    project_method_names = Counter(name for ms in methods.values() for name in ms)

    for mod, (rel, tree) in files.items():
        imports = collect_imports(mod, rel, tree)
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
            ctx = {"mod": mod, "cls": cls, "imports": imports, "top": top, "methods": methods,
                   "class_bases": class_bases, "class_ext_bases": class_ext_bases,
                   "local_vars": local_vars, "project_roots": project_roots,
                   "project_method_names": project_method_names,
                   "module_imports": module_imports, "module_vars": module_vars}
            for c in ast.walk(fn):
                if not isinstance(c, ast.Call):
                    continue
                callee = resolve_call(c.func, mod, cls, imports, top, methods, class_bases, local_vars)
                if callee:
                    edges.append({"from": caller, "to": callee, "type": "calls", "line": c.lineno})

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
                    key = (caller, cat, pkg, detail)
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
    class_bases, class_ext_bases = collect_class_bases(files, top, classes)
    edges, ext_edges, unresolved = collect_calls(files, top, methods, classes, class_bases, class_ext_bases, root.name)

    n_static = sum(1 for e in edges if e["type"] == "calls")
    n_override = sum(1 for e in edges if e["type"] == "calls_override")
    ext_count = Counter()
    for e in ext_edges:
        ext_count[e["category"]] += e["count"]
    unk_count = Counter(u["category"] for u in unresolved)
    total = n_static + sum(ext_count.values()) + sum(unk_count.values())
    sure = n_static + sum(ext_count[c] for c in EXTERNAL_CATS if c != "builtin_type_method")
    guess = ext_count["builtin_type_method"]

    name_count = Counter(name for ms in methods.values() for name in ms)
    guessable = sum(1 for u in unresolved if u["category"] == "unknown_receiver"
                    and name_count.get(u["expr"].split(".")[-1].split("(")[0], 0) == 1)

    summary = {
        "files": len(files), "nodes": len(nodes), "total_calls": total,
        "internal_resolved": n_static, "override_edges": n_override,
        "external": dict(ext_count), "unresolved": dict(unk_count),
        "classified_rate_sure": round(sure / total, 4) if total else 0,
        "classified_rate_incl_guess": round((sure + guess) / total, 4) if total else 0,
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
    print(f"\n분류 완료율(확실한 것만): {s['classified_rate_sure'] * 100:.1f}%"
          f"   /  추정까지 포함: {s['classified_rate_incl_guess'] * 100:.1f}%")
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