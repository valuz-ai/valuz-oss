"""Safe deterministic arithmetic shared by citation producers and validators."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from decimal import Decimal

_ALLOWED_BINARY = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
}
_ALLOWED_UNARY = {
    ast.UAdd: lambda value: value,
    ast.USub: lambda value: -value,
}


def evaluate_decimal_expression(expression: str, values: Mapping[str, Decimal]) -> Decimal:
    """Evaluate bounded arithmetic without calls, attributes, or subscripting."""

    if len(expression) > 500:
        raise ValueError("expression_too_long")
    try:
        root = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("invalid_expression") from exc

    def evaluate(node: ast.AST, depth: int = 0) -> Decimal:
        if depth > 32:
            raise ValueError("expression_too_deep")
        if isinstance(node, ast.Expression):
            return evaluate(node.body, depth + 1)
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return Decimal(str(node.value))
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINARY:
            return _ALLOWED_BINARY[type(node.op)](
                evaluate(node.left, depth + 1),
                evaluate(node.right, depth + 1),
            )
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](evaluate(node.operand, depth + 1))
        raise ValueError("unsupported_expression")

    result = evaluate(root)
    if not result.is_finite():
        raise ValueError("non_finite_result")
    return result
