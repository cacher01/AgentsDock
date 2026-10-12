import ast
import math
import operator

_MAX_EXPRESSION_LENGTH = 200
_MAX_AST_NODES = 100
_MAX_AST_DEPTH = 20
_MAX_EXPONENT = 100
_MAX_ABSOLUTE_VALUE = 1e100


class CalculatorTool:
    name = "calculator"
    description = "Evaluate a basic arithmetic expression."

    async def run(self, arguments: dict) -> dict:
        expression = str(arguments["expression"])
        return {"result": _evaluate(expression)}


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _evaluate(expression: str) -> float:
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ValueError("Calculator expression is too long")
    try:
        node = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid calculator expression") from exc
    if sum(1 for _ in ast.walk(node)) > _MAX_AST_NODES:
        raise ValueError("Calculator expression has too many nodes")
    if _node_depth(node.body) > _MAX_AST_DEPTH:
        raise ValueError("Calculator expression is too deeply nested")
    return _eval_node(node.body)


def _eval_node(node: ast.AST) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return _validate_result(float(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ValueError("Calculator exponent exceeds limit")
        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, OverflowError) as exc:
            raise ValueError("Calculator operation failed") from exc
        return _validate_result(result)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _validate_result(_UNARY_OPERATORS[type(node.op)](_eval_node(node.operand)))
    raise ValueError("Unsupported calculator expression")


def _node_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_node_depth(child) for child in children)


def _validate_result(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("Calculator result must be finite")
    if abs(value) > _MAX_ABSOLUTE_VALUE:
        raise ValueError("Calculator result exceeds limit")
    return value
