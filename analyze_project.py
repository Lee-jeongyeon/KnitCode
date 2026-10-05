import ast
import json
import sys
from pathlib import Path

SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "build", "dist"}
FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


# ---------- 1. 파일 찾기 + 파싱 ----------
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


# ---------- 2. 함수/클래스 정의 수집 (고유 ID 부여) ----------
def collect_defs(files):
    nodes = {}    # ID -> 정보
    top = {}      # 모듈 -> {이름: ID}  (최상위 함수/클래스)
    methods = {}  # (모듈, 클래스) -> {메서드명: ID}
    for mod, (rel, tree) in files.items():
        top[mod] = {}
        for n in tree.body:
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
                    if isinstance(m, FUNC_TYPES):
                        mid = f"{rel}::{n.name}.{m.name}"
                        nodes[mid] = {"id": mid, "type": "method", "file": rel, "line": m.lineno}
                        methods[(mod, n.name)][m.name] = mid
    return nodes, top, methods


# ---------- 3. import 해석 ----------
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
    table = {}  # 코드에서 쓰는 이름 -> 실제 가리키는 곳
    is_pkg = rel.endswith("__init__.py")
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                key = a.asname or a.name.split(".")[0]
                table[key] = ("module", a.name if a.asname else a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            base = resolve_from(mod, is_pkg, n.level, n.module)
            for a in n.names:
                table[a.asname or a.name] = ("name", base, a.name)
    return table

# ---------- 3.5. 클래스 상속 관계 수집 ----------
def resolve_class_ref(node, mod, imports, top):
    """부모 클래스 표현식(ast.Name 또는 ast.Attribute)을 (모듈, 클래스명)으로 변환"""
    if isinstance(node, ast.Name):
        if node.id in top.get(mod, {}):
            return (mod, node.id)
        imp = imports.get(node.id)
        if imp and imp[0] == "name" and node.id in top.get(imp[1], {}):
            return (imp[1], node.id)
    elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        imp = imports.get(node.value.id)
        if imp and imp[0] == "module" and node.attr in top.get(imp[1], {}):
            return (imp[1], node.attr)
    return None


def collect_class_bases(files, top):
    """{(모듈, 클래스명): [(부모모듈, 부모클래스명), ...]} 형태로 상속 관계 기록"""
    class_bases = {}
    for mod, (rel, tree) in files.items():
        imports = collect_imports(mod, rel, tree)
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                bases = [resolve_class_ref(b, mod, imports, top) for b in n.bases]
                class_bases[(mod, n.name)] = [b for b in bases if b]
    return class_bases

def find_method_up_chain(mod, cls, attr, methods, class_bases, seen=None):
    """현재 클래스에 메서드가 없으면 부모 클래스로 계속 올라가며 탐색 (다중상속/순환 방지 포함)"""
    seen = seen or set()
    if (mod, cls) in seen:
        return None
    seen.add((mod, cls))

    found = methods.get((mod, cls), {}).get(attr)
    if found:
        return found

    for base_mod, base_cls in class_bases.get((mod, cls), []):
        found = find_method_up_chain(base_mod, base_cls, attr, methods, class_bases, seen)
        if found:
            return found
    return None

def collect_local_var_types(fn_node, mod, imports, top):
    """함수 안에서 'var = ClassName(...)' 형태의 대입을 찾아 {변수명: (모듈, 클래스명)} 반환"""
    var_types = {}
    for stmt in ast.walk(fn_node):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target = stmt.targets[0]
            if isinstance(target, ast.Name) and isinstance(stmt.value, ast.Call):
                callee = stmt.value.func
                ref = resolve_class_ref(callee, mod, imports, top)
                if ref:
                    var_types[target.id] = ref
    return var_types

# ---------- 4. 호출 대상이 어느 함수인지 찾기 ----------
def resolve_call(func, mod, cls, imports, top, methods, class_bases, local_vars):
    if isinstance(func, ast.Name):
        if func.id in top[mod]:
            return top[mod][func.id]
        imp = imports.get(func.id)
        if imp and imp[0] == "name":
            return top.get(imp[1], {}).get(imp[2])

    elif isinstance(func, ast.Attribute):
        # ↓ 추가: super().method() 패턴 처리
        if isinstance(func.value, ast.Call) and isinstance(func.value.func, ast.Name) \
                and func.value.func.id == "super" and cls:
            attr = func.attr
            for base_mod, base_cls in class_bases.get((mod, cls), []):
                found = find_method_up_chain(base_mod, base_cls, attr, methods, class_bases)
                if found:
                    return found
            return None

        if not isinstance(func.value, ast.Name):
            return None

        obj, attr = func.value.id, func.attr

        if obj == "self" and cls:
            return find_method_up_chain(mod, cls, attr, methods, class_bases)

        if obj in top.get(mod, {}) and (mod, obj) in methods:
            return find_method_up_chain(mod, obj, attr, methods, class_bases)

        if obj in local_vars:
            v_mod, v_cls = local_vars[obj]
            found = find_method_up_chain(v_mod, v_cls, attr, methods, class_bases)
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


def collect_calls(files, top, methods, class_bases):   # ← class_bases 추가
    edges, unresolved = [], []
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
            local_vars = collect_local_var_types(fn, mod, imports, top) #추가: 함수마다 지역 변수 타입 스캔 
            for c in ast.walk(fn):
                if isinstance(c, ast.Call):
                    callee = resolve_call(c.func, mod, cls, imports, top, methods, class_bases, local_vars)  # ← 전달
                    if callee:
                        edges.append({"from": caller, "to": callee, "type": "calls", "line": c.lineno})
                    else:
                        unresolved.append({"from": caller, "expr": ast.unparse(c.func), "line": c.lineno})
    return edges, unresolved


# ---------- 실행 ----------
def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    files = parse_files(root)
    nodes, top, methods = collect_defs(files)
    class_bases = collect_class_bases(files, top)
    edges, unresolved = collect_calls(files, top, methods, class_bases)

    print(f"분석한 파일: {len(files)}개 / 노드: {len(nodes)}개")
    print(f"해석된 호출: {len(edges)}개 / 미해결 호출: {len(unresolved)}개\n")
    for e in edges:
        print(f"{e['from']}  →  {e['to']}")
    print("\n[미해결 예시]")
    for u in unresolved[:10]:
        print(f"{u['from']}  →  {u['expr']}  (줄 {u['line']})")

    result = {"nodes": list(nodes.values()), "edges": edges, "unresolved": unresolved}
    Path("graph.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\ngraph.json 저장 완료")


if __name__ == "__main__":
    main()