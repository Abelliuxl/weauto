from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import tempfile
from dataclasses import dataclass


class PythonSandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class PythonSandboxResult:
    ok: bool
    output: str
    error: str = ""

    def to_tool_text(self) -> str:
        if self.ok:
            return self.output.strip() or "(no output)"
        return f"ERROR: {self.error.strip() or 'calculation failed'}"


_SAFE_CALLS = {
    "abs",
    "all",
    "any",
    "bool",
    "date",
    "datetime",
    "Decimal",
    "dict",
    "enumerate",
    "float",
    "Fraction",
    "int",
    "len",
    "list",
    "max",
    "mean",
    "median",
    "min",
    "pow",
    "print",
    "range",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "timedelta",
    "tuple",
    "zip",
}

_SAFE_MODULE_CALLS = {
    "math": {
        "acos",
        "asin",
        "atan",
        "atan2",
        "ceil",
        "comb",
        "cos",
        "degrees",
        "dist",
        "exp",
        "fabs",
        "factorial",
        "floor",
        "fsum",
        "gcd",
        "hypot",
        "isclose",
        "isfinite",
        "isinf",
        "isnan",
        "lcm",
        "log",
        "log10",
        "log2",
        "perm",
        "pow",
        "prod",
        "radians",
        "sin",
        "sqrt",
        "tan",
        "trunc",
    },
    "statistics": {
        "fmean",
        "geometric_mean",
        "harmonic_mean",
        "mean",
        "median",
        "median_grouped",
        "median_high",
        "median_low",
        "mode",
        "multimode",
        "pstdev",
        "pvariance",
        "quantiles",
        "stdev",
        "variance",
    },
    "datetime": {"now", "today", "strptime", "fromisoformat"},
    "date": {"today", "fromisoformat"},
}

_BANNED_NAMES = {
    "__builtins__",
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "object",
    "open",
    "setattr",
    "super",
    "type",
    "vars",
}

_ALLOWED_NODES = {
    ast.Add,
    ast.And,
    ast.Assign,
    ast.AsyncFor,
    ast.Attribute,
    ast.AugAssign,
    ast.BinOp,
    ast.BitAnd,
    ast.BitOr,
    ast.BitXor,
    ast.BoolOp,
    ast.Break,
    ast.Call,
    ast.Compare,
    ast.Constant,
    ast.Continue,
    ast.Dict,
    ast.DictComp,
    ast.Div,
    ast.Eq,
    ast.Expr,
    ast.FloorDiv,
    ast.For,
    ast.FormattedValue,
    ast.FunctionDef,
    ast.GeneratorExp,
    ast.Gt,
    ast.GtE,
    ast.If,
    ast.IfExp,
    ast.In,
    ast.Is,
    ast.IsNot,
    ast.JoinedStr,
    ast.List,
    ast.ListComp,
    ast.Load,
    ast.LShift,
    ast.Lt,
    ast.LtE,
    ast.Mod,
    ast.Module,
    ast.Mult,
    ast.Name,
    ast.NamedExpr,
    ast.Not,
    ast.NotEq,
    ast.NotIn,
    ast.Or,
    ast.Pass,
    ast.Pow,
    ast.Return,
    ast.RShift,
    ast.Set,
    ast.SetComp,
    ast.Slice,
    ast.Store,
    ast.Sub,
    ast.Subscript,
    ast.Tuple,
    ast.UAdd,
    ast.UnaryOp,
    ast.USub,
    ast.While,
    ast.arg,
    ast.arguments,
    ast.keyword,
    ast.comprehension,
}


class _Validator(ast.NodeVisitor):
    def __init__(self, user_functions: set[str] | None = None) -> None:
        self.count = 0
        self.user_functions = user_functions or set()

    def generic_visit(self, node: ast.AST) -> None:
        self.count += 1
        if self.count > 800:
            raise PythonSandboxError("code is too complex")
        if type(node) not in _ALLOWED_NODES:
            raise PythonSandboxError(f"unsupported syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if "__" in node.id or node.id in _BANNED_NAMES:
            raise PythonSandboxError(f"name is not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if "__" in node.attr or node.attr.startswith("_"):
            raise PythonSandboxError(f"attribute is not allowed: {node.attr}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if "__" in node.name or node.name in _BANNED_NAMES:
            raise PythonSandboxError(f"function name is not allowed: {node.name}")
        if node.decorator_list:
            raise PythonSandboxError("decorators are not allowed")
        if node.returns is not None:
            raise PythonSandboxError("return type annotations are not allowed")
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        if "__" in node.arg or node.arg in _BANNED_NAMES:
            raise PythonSandboxError(f"argument name is not allowed: {node.arg}")
        if node.annotation is not None:
            raise PythonSandboxError("argument annotations are not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in _SAFE_CALLS and name not in self.user_functions:
                raise PythonSandboxError(f"function is not allowed: {name}")
            if name == "pow" and len(node.args) != 3:
                raise PythonSandboxError("pow requires 3 arguments: pow(base, exp, mod)")
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            root = node.func.value.id
            allowed = _SAFE_MODULE_CALLS.get(root, set())
            if node.func.attr not in allowed:
                raise PythonSandboxError(f"function is not allowed: {root}.{node.func.attr}")
        else:
            raise PythonSandboxError("only whitelisted function calls are allowed")
        self.generic_visit(node)


def _validate(code: str) -> ast.AST:
    if len(code) > 4000:
        raise PythonSandboxError("code is too long")
    if "__" in code:
        raise PythonSandboxError("double-underscore access is not allowed")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise PythonSandboxError(f"syntax error: {exc.msg}") from exc
    user_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and "__" not in node.name and node.name not in _BANNED_NAMES
    }
    _Validator(user_functions).visit(tree)
    return tree


_CHILD_WRAPPER = r"""
import math
import statistics
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

source = sys.stdin.read()
safe_builtins = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "pow": pow,
    "print": print,
    "range": range,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
env = {
    "__builtins__": safe_builtins,
    "date": date,
    "datetime": datetime,
    "Decimal": Decimal,
    "Fraction": Fraction,
    "math": math,
    "mean": statistics.mean,
    "median": statistics.median,
    "statistics": statistics,
    "timedelta": timedelta,
}
exec(compile(source, "<calculation>", "exec"), env, env)
"""


def run_python_calculation(code: str, *, timeout_sec: float = 2.0, max_output_chars: int = 4000) -> PythonSandboxResult:
    source = str(code or "").strip()
    if not source:
        return PythonSandboxResult(ok=False, output="", error="empty code")
    try:
        _validate(source)
    except PythonSandboxError as exc:
        return PythonSandboxResult(ok=False, output="", error=str(exc))

    wrapper = textwrap.dedent(_CHILD_WRAPPER).strip()
    try:
        with tempfile.TemporaryDirectory(prefix="weauto_calc_") as tmp:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-c", wrapper],
                input=source + "\n",
                text=True,
                capture_output=True,
                cwd=tmp,
                env={},
                timeout=timeout_sec,
            )
    except subprocess.TimeoutExpired:
        return PythonSandboxResult(ok=False, output="", error=f"timeout after {timeout_sec:.1f}s")
    except OSError as exc:
        return PythonSandboxResult(ok=False, output="", error=f"failed to run python: {exc}")

    stdout = (proc.stdout or "")[:max_output_chars]
    stderr = (proc.stderr or "")[:max_output_chars]
    if proc.returncode != 0:
        detail = stderr.strip() or f"python exited with status {proc.returncode}"
        return PythonSandboxResult(ok=False, output=stdout, error=detail[:max_output_chars])
    if len(proc.stdout or "") > max_output_chars:
        stdout = stdout.rstrip() + "\n[output truncated]"
    return PythonSandboxResult(ok=True, output=stdout)
