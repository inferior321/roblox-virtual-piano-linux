"""Catch self.attribute and self.method typos without importing PyQt6."""

import ast
import builtins
import pathlib
import sys

PACKAGE = pathlib.Path("rpiano")
problems = []


def module_names(tree):
    """Everything defined or imported at module level."""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Try):
            for sub in node.body + [s for h in node.handlers for s in h.body]:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        names.add(alias.asname or alias.name.split(".")[0])
    return names


def class_attrs(cls):
    """self.X assigned anywhere in the class, plus its methods and dataclass fields."""
    assigned = set()
    methods = set()
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.add(node.name)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.AugStore)):
                assigned.add(node.attr)
    # class-body assignments (pyqtSignal declarations, dataclass fields, constants)
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.add(node.target.id)
    return assigned | methods


def class_reads(cls):
    reads = set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "self" and isinstance(node.ctx, ast.Load):
                reads.add(node.attr)
    return reads


INHERITED_OK = {
    # Qt base-class members we legitimately call on self
    "setWindowTitle", "resize", "setGeometry", "geometry", "setCentralWidget",
    "statusBar", "show", "close", "update", "width", "height", "rect",
    "setMinimumHeight", "setMaximumHeight", "setMinimumWidth", "setMinimumSize",
    "accept", "reject", "exec", "setLayout", "style", "parent", "window",
    "setObjectName", "setWordWrap", "setText", "text", "setEnabled",
    "setWindowFlag", "setWindowOpacity", "windowFlags", "statusBar",
    "setCursor", "setToolTip", "heightForWidth",
    "setAcceptDrops", "setDragEnabled", "setDragDropMode",
    "setDropIndicatorShown", "indexAt", "itemAt", "model", "rootIndex",
    "setSourceModel", "sourceModel", "mapToSource", "mapFromSource",
    "setDynamicSortFilter", "setSortCaseSensitivity",
}

for path in sorted(PACKAGE.glob("*.py")):
    tree = ast.parse(path.read_text(), filename=str(path))
    globals_here = module_names(tree) | set(dir(builtins))

    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def inherited(cls, seen=None):
        """Attributes from base classes written in this same file.

        A mixin's methods belong to the class that mixes it in, and without
        this every call to one reads as a typo - which is the sort of noise
        that gets a checker switched off rather than fixed.
        """
        seen = seen or set()
        found = set()
        for base in cls.bases:
            name = base.id if isinstance(base, ast.Name) else None
            if name in classes and name not in seen:
                seen.add(name)
                found |= class_attrs(classes[name]) | inherited(classes[name], seen)
        return found

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        defined = class_attrs(node) | inherited(node) | INHERITED_OK
        for attr in sorted(class_reads(node) - defined):
            problems.append(f"{path}:{node.name}: self.{attr} is read but never assigned")

    # module-level name resolution, ignoring anything bound inside a scope
    class Scope(ast.NodeVisitor):
        def __init__(self, enclosing=frozenset()):
            # Names the function we are inside of can see. A nested function
            # closes over them, and without carrying them down every captured
            # name looked undefined - which made any decorator or inner helper
            # unreportable noise.
            self.enclosing = enclosing

        def visit_FunctionDef(self, fn):
            local = set(globals_here) | set(self.enclosing)
            local |= {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            if fn.args.vararg:
                local.add(fn.args.vararg.arg)
            if fn.args.kwarg:
                local.add(fn.args.kwarg.arg)
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    local.add(sub.id)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        local.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    local.add(sub.name)
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    local.add(sub.name)
                elif isinstance(sub, ast.arg):
                    local.add(sub.arg)
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                    if sub.id not in local:
                        problems.append(
                            f"{path}:{sub.lineno}: name '{sub.id}' may be undefined "
                            f"(in {fn.name})"
                        )
            # Recurse with this scope as the enclosing one, so a nested
            # function is still checked on its own terms but can see what it
            # legitimately closes over.
            for sub in fn.body:
                Scope(local).visit(sub)

    Scope().visit(tree)

for problem in problems:
    print(problem)
print(f"\n{len(problems)} potential problems")
sys.exit(1 if problems else 0)
