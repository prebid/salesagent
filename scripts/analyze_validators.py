#!/usr/bin/env python3
"""
Analyze custom validators to determine which should be schema-based.

For each validator, determine:
1. What it's validating
2. Can it be expressed in JSON Schema?
3. Should it be in the schema or stay in code?
"""

import ast
from pathlib import Path

# Read schemas.py
schemas_file = Path("src/core/schemas.py")
content = schemas_file.read_text()

# Parse AST
tree = ast.parse(content)

validators = []

# Find all classes with model_validator decorators
for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef):
        class_name = node.name
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                # Check for model_validator decorator
                for decorator in item.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Attribute):
                            if decorator.func.attr == "model_validator":
                                # Extract the function
                                func_name = item.name
                                lineno = item.lineno

                                # Get docstring
                                docstring = ast.get_docstring(item) or "No docstring"

                                validators.append(
                                    {"class": class_name, "function": func_name, "line": lineno, "docstring": docstring}
                                )

print("=" * 80)
print("CUSTOM VALIDATORS ANALYSIS")
print("=" * 80)
print()

for i, v in enumerate(validators, 1):
    print(f"{i}. {v['class']}.{v['function']}() [Line {v['line']}]")
    docstring = str(v.get("docstring", ""))
    if docstring:
        print(f"   {docstring[:100]}...")
    print()

print(f"\nTotal validators found: {len(validators)}")
