"""Inventory backend language debt offline; never rewrite linguistic/user data blindly."""
import ast
import json
from pathlib import Path
import sys

LINGUISTIC = {"speech_numbers.py", "speech_pronunciation.py", "speech_text.py", "suggestion_defaults.py"}

def scan(root: Path) -> dict:
    files = []
    for directory in ("local_mode", "actions", "vision", "agent", "memory"):
        for path in sorted((root / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                          if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                          and node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(node.body[0].value, ast.Constant)
                          and isinstance(node.body[0].value.value, str)}
            findings = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or id(node) in docstrings:
                    continue
                if any("\u0400" <= char <= "\u04ff" for char in node.value):
                    findings.append({"line": node.lineno,
                                     "kind": "linguistic-data-review" if path.name in LINGUISTIC else "message-or-language-rule-review",
                                     "sample": node.value[:120]})
            if findings:
                files.append({"file": path.relative_to(root).as_posix(), "findings": findings})
    return {"files": files, "literalCandidates": sum(len(item["findings"]) for item in files),
            "note": "Candidates are not all UI defects: classify prompts, regex/word lists and spoken-language data before migration."}

if __name__ == "__main__":
    result = scan(Path(__file__).resolve().parents[1])
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(result["files"]), "literalCandidates": result["literalCandidates"]}))
