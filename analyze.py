#ast 모듈로 구조 뽑기 
import ast

with open("sample.py") as f:
    tree = ast.parse(f.read())

# 함수/클래스 목록 뽑기
for node in ast.walk(tree): 
    if isinstance(node, ast.FunctionDef):
        print(f"함수: {node.name}, 줄번호: {node.lineno}")
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                print("  ㄴ 메서드:", item.name, "줄번호:", item.lineno)
    elif isinstance(node, ast.ClassDef):
        pass

class CallVisitor(ast.NodeVisitor):
    def __init__(self):
        self.calls = []
        self.current_function = None

    def visit_FunctionDef(self, node):
        self.current_function = node.name
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name):
            self.calls.append((self.current_function, node.func.id))
        elif isinstance(node.func, ast.Attribute):
            self.calls.append((self.current_function, node.func.attr))
        self.generic_visit(node)

visitor = CallVisitor()
visitor.visit(tree)
for caller, callee in visitor.calls:
    print(f"{caller} → {callee}")