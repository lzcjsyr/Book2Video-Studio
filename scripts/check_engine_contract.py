"""Check that the staged engine provides every core.* module and name the backend imports.

Usage: python check_engine_contract.py <engine> <backend>
The backend imports the engine lazily inside functions, so a mismatched engine
would otherwise only fail when a user reaches that feature.
"""
import ast
import importlib
import sys
from pathlib import Path

engine, backend = map(Path, sys.argv[1:3])
sys.path.insert(0, str(engine))
wanted = {}
for file in sorted(backend.glob("*.py")):
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"), str(file))):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "core":
            wanted.setdefault(node.module, set()).update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "core":
                    wanted.setdefault(alias.name, set())
problems = []
for module, names in sorted(wanted.items()):
    try:
        loaded = importlib.import_module(module)
    except Exception as error:
        problems.append(f"{module}: {type(error).__name__}: {error}")
        continue
    for name in sorted(names):
        if name == "*" or hasattr(loaded, name):
            continue
        try:
            importlib.import_module(f"{module}.{name}")
        except ImportError:
            problems.append(f"{module} has no {name}")
if problems:
    sys.exit("The engine does not match what the backend expects:\n  " + "\n  ".join(problems))
print(f"Engine provides the {len(wanted)} modules the backend imports: ok")
