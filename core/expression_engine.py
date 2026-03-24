"""
Expression Engine — evaluates JavaScript-like expressions for derived tags.

Uses Python's ast module for safe expression evaluation.
Supports: arithmetic, comparisons, ternary-style (if/else), math functions.

Example:
    engine = ExpressionEngine()
    result = engine.evaluate("(tag1 + tag2) / 2", {"tag1": 10, "tag2": 20})
    # result = 15.0
"""

from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Safe operators for AST evaluation
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
    ast.Not: operator.not_,
}

# Safe math functions available in expressions
SAFE_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "int": int,
    "float": float,
    "sqrt": math.sqrt,
    "pow": pow,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
}


class ExpressionEngine:
    """Safe expression evaluator for derived tag calculations.

    Evaluates mathematical expressions with variable substitution.
    Uses Python AST parsing for safety — no exec/eval of arbitrary code.

    Supported syntax:
        - Arithmetic: +, -, *, /, //, %, **
        - Comparisons: ==, !=, <, <=, >, >=
        - Logical: and, or, not
        - Ternary: value_if_true if condition else value_if_false
        - Functions: abs, round, min, max, sqrt, pow, log, ceil, floor, etc.
        - Variables: any tag_id name (e.g., temperature, pressure)
    """

    def __init__(self):
        self._cache: dict[str, ast.Expression] = {}

    def evaluate(self, expression: str, variables: Dict[str, Any]) -> Any:
        """Evaluate an expression with the given variable values.

        Args:
            expression: The expression string (e.g., "(tag1 + tag2) / 2")
            variables: Dict mapping variable names to their current values

        Returns:
            The computed result

        Raises:
            ValueError: If the expression is invalid or uses unsafe operations
        """
        # Convert JavaScript-style syntax to Python
        expr = self._js_to_python(expression)

        try:
            # Parse and cache the AST
            if expr not in self._cache:
                tree = ast.parse(expr, mode="eval")
                self._cache[expr] = tree
            else:
                tree = self._cache[expr]

            # Build evaluation context
            context = {**SAFE_FUNCTIONS, **variables}

            # Evaluate safely using AST walker
            return self._eval_node(tree.body, context)

        except Exception as e:
            logger.error(f"Expression evaluation failed: '{expression}' → {e}")
            raise ValueError(f"Expression error: {e}")

    def _js_to_python(self, expr: str) -> str:
        """Convert common JavaScript syntax to Python equivalents."""
        # Replace JS ternary: condition ? a : b → a if condition else b
        # (Simple cases only — nested ternaries need manual conversion)
        expr = expr.replace("&&", " and ")
        expr = expr.replace("||", " or ")
        expr = expr.replace("!", " not ")
        expr = expr.replace("===", "==")
        expr = expr.replace("!==", "!=")
        # Math.xxx → xxx
        expr = expr.replace("Math.abs", "abs")
        expr = expr.replace("Math.round", "round")
        expr = expr.replace("Math.min", "min")
        expr = expr.replace("Math.max", "max")
        expr = expr.replace("Math.sqrt", "sqrt")
        expr = expr.replace("Math.pow", "pow")
        expr = expr.replace("Math.log", "log")
        expr = expr.replace("Math.ceil", "ceil")
        expr = expr.replace("Math.floor", "floor")
        expr = expr.replace("Math.PI", "pi")
        expr = expr.replace("Math.E", "e")
        return expr.strip()

    def _eval_node(self, node: ast.AST, context: dict) -> Any:
        """Recursively evaluate an AST node safely."""
        if isinstance(node, ast.Expression):
            return self._eval_node(node.body, context)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.Name):
            if node.id in context:
                return context[node.id]
            raise ValueError(f"Unknown variable: '{node.id}'")

        if isinstance(node, ast.UnaryOp):
            op = SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
            return op(self._eval_node(node.operand, context))

        if isinstance(node, ast.BinOp):
            op = SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            left = self._eval_node(node.left, context)
            right = self._eval_node(node.right, context)
            return op(left, right)

        if isinstance(node, ast.BoolOp):
            op = SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Unsupported boolean operator: {type(node.op).__name__}")
            values = [self._eval_node(v, context) for v in node.values]
            result = values[0]
            for v in values[1:]:
                result = op(result, v)
            return result

        if isinstance(node, ast.Compare):
            left = self._eval_node(node.left, context)
            for op_node, comparator in zip(node.ops, node.comparators):
                op = SAFE_OPERATORS.get(type(op_node))
                if op is None:
                    raise ValueError(f"Unsupported comparison: {type(op_node).__name__}")
                right = self._eval_node(comparator, context)
                if not op(left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.IfExp):
            # Ternary: value_if_true if condition else value_if_false
            condition = self._eval_node(node.test, context)
            if condition:
                return self._eval_node(node.body, context)
            return self._eval_node(node.orelse, context)

        if isinstance(node, ast.Call):
            func = self._eval_node(node.func, context)
            if not callable(func):
                raise ValueError(f"Not callable: {func}")
            args = [self._eval_node(arg, context) for arg in node.args]
            return func(*args)

        if isinstance(node, ast.Attribute):
            # Block attribute access for safety
            raise ValueError(f"Attribute access not allowed: {ast.dump(node)}")

        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    def validate(self, expression: str, available_vars: list[str]) -> dict:
        """Validate an expression without evaluating it.

        Returns: {"valid": bool, "error": str or None, "variables_used": list[str]}
        """
        expr = self._js_to_python(expression)
        try:
            tree = ast.parse(expr, mode="eval")
            # Extract variable names
            used_vars = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id not in SAFE_FUNCTIONS:
                    used_vars.add(node.id)

            missing = [v for v in used_vars if v not in available_vars]
            if missing:
                return {
                    "valid": False,
                    "error": f"Unknown variables: {', '.join(missing)}",
                    "variables_used": list(used_vars),
                }

            return {
                "valid": True,
                "error": None,
                "variables_used": list(used_vars),
            }
        except SyntaxError as e:
            return {"valid": False, "error": f"Syntax error: {e}", "variables_used": []}
