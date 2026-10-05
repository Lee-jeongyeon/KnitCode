import json
from collections import Counter

# 흔한 Python 내장 함수들 (필요하면 더 추가)
BUILTINS = {
    "print", "len", "str", "int", "open", "range", "sorted",
    "enumerate", "zip", "map", "filter", "sum", "isinstance",
    "sys.exit", "exit", "super", "type", "list", "set", "dict",
    "max", "hasattr", "min", "bool", "float", "repr", "next",
    "iter", "callable", "getattr", "setattr", "bytes",
    "ValueError", "TypeError", "KeyError", "OSError",
    "NotImplementedError", "AttributeError", "RuntimeError",
    "StopIteration", "cast", "OrderedDict", "urlparse", "urlunparse",
}

# 표준 라이브러리로 추정되는 모듈 접두사
STD_PREFIXES = ("sys.", "os.", "re.", "json.", "argparse.", "time.",
                 "threading.", "socket.", "queue.", "Queue.","multiprocessing.", "urlparse.", "requests.")

data = json.load(open("graph.json", encoding="utf-8"))
unresolved = data["unresolved"]

builtin_or_std = []
self_calls = []        # self.xxx() - 상속 문제로 의심되는 핵심 그룹
self_attr_calls = []   # self.yyy.xxx() - 속성 체이닝
other_attr_calls = []  # 그 외 변수.메서드()
other = []

for u in unresolved:
    expr = u["expr"]
    if expr in BUILTINS or expr.startswith(STD_PREFIXES):
        builtin_or_std.append(u)
    elif expr.startswith("self."):
        rest = expr[len("self."):]
        if "." in rest:
            self_attr_calls.append(u)
        else:
            self_calls.append(u)
    elif "." in expr:
        other_attr_calls.append(u)
    else:
        other.append(u)

print(f"내장/표준 라이브러리: {len(builtin_or_std)}개")
print(f"self.메서드() - 상속 의심 (★핵심): {len(self_calls)}개")
print(f"self.속성.메서드() - 체이닝: {len(self_attr_calls)}개")
print(f"기타 변수.메서드(): {len(other_attr_calls)}개")
print(f"분류 안 됨: {len(other)}개")

print("\n[★ self.메서드() - 상속 의심 전체 목록]")
for a in self_calls:
    print(f"  {a['from']}  →  {a['expr']}  (줄 {a['line']})")

# 기타 변수.메서드() 중, 뒤에 붙은 메서드명만 뽑아서 빈도 확인
method_names = Counter(a["expr"].split(".")[-1] for a in other_attr_calls)
print("\n[기타 변수.메서드() - 메서드명 빈도 TOP 15]")
for name, cnt in method_names.most_common(15):
    print(f"  {name}: {cnt}회")

# 분류 안됨은 표현식 자체로 빈도 확인
other_names = Counter(a["expr"] for a in other)
print("\n[분류 안됨 - 표현식 빈도 TOP 15]")
for name, cnt in other_names.most_common(15):
    print(f"  {name}: {cnt}회")