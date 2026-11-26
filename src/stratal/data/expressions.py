"""The transform's expression language (DATA-7.4, ADR-0019 Addendum B rule 30).

An expression is Python's expression syntax, parsed with ``ast.parse(text,
mode="eval")`` and accepted only if every node is on a short whitelist. Our own
walker then computes it on one row. Nothing here passes text to ``eval``,
``exec`` or ``compile``; a test scans this module for those names (DATA-7.4.AC1).

Allowed: numbers, strings, ``True`` and ``False``; column names, or
``col("name")`` for a name that is not an identifier; ``+ - * /`` and unary
minus on numbers; ``== != < <= > >=``; ``and``, ``or``, ``not``; the functions
``isnull``, ``notnull``, ``abs``, ``log``, ``sqrt``, ``round(x, n)``, ``min``
and ``max``. At most 500 characters and 100 nodes.

Types are checked before any row is read: arithmetic and ordering need numbers,
a categorical column is compared with strings by ``==`` and ``!=``, and
``and``/``or``/``not`` need conditions. A value that cannot be computed for a
row (a missing value, a division by zero, the logarithm of a number that is not
positive) is ``None``; ``and`` and ``or`` follow three-valued logic, so
``isnull(x) or x > 3`` is true where ``x`` is missing.
"""

import ast
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

MAX_CHARS = 500
MAX_NODES = 100

Type = Literal["numeric", "categorical", "bool"]
Value = float | str | bool | None


class ExpressionRefused(ValueError):
    """The expression is outside the language, or its types do not fit. The message names
    what was refused."""


# ---------------------------------------------------------------------------- the tree we keep


@dataclass(frozen=True)
class Const:
    value: float | str | bool
    type: Type


@dataclass(frozen=True)
class Col:
    name: str
    type: Type


@dataclass(frozen=True)
class Unary:
    op: Literal["neg", "not"]
    operand: "Node"
    type: Type


@dataclass(frozen=True)
class Binary:
    op: Literal["+", "-", "*", "/"]
    left: "Node"
    right: "Node"
    type: Type


@dataclass(frozen=True)
class Compare:
    op: Literal["==", "!=", "<", "<=", ">", ">="]
    left: "Node"
    right: "Node"
    type: Type


@dataclass(frozen=True)
class Logic:
    op: Literal["and", "or"]
    operands: tuple["Node", ...]
    type: Type


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Node", ...]
    type: Type


Node = Const | Col | Unary | Binary | Compare | Logic | Call


@dataclass(frozen=True)
class Expression:
    text: str
    root: Node

    @property
    def type(self) -> Type:
        return self.root.type

    @property
    def columns(self) -> frozenset[str]:
        found: set[str] = set()

        def walk(node: Node) -> None:
            match node:
                case Col(name=name):
                    found.add(name)
                case Unary(operand=operand):
                    walk(operand)
                case Binary(left=left, right=right) | Compare(left=left, right=right):
                    walk(left)
                    walk(right)
                case Logic(operands=operands) | Call(args=operands):
                    for child in operands:
                        walk(child)
                case Const():
                    pass

        walk(self.root)
        return frozenset(found)


# ---------------------------------------------------------------------------- parsing

_ARITH: dict[type[ast.operator], Literal["+", "-", "*", "/"]] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
}
_CMP: dict[type[ast.cmpop], Literal["==", "!=", "<", "<=", ">", ">="]] = {
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}
_FUNCTIONS = ("isnull", "notnull", "abs", "log", "sqrt", "round", "min", "max", "col")

_REFUSED_NAMES: dict[type[ast.AST], str] = {
    ast.Attribute: "attribute access (a.b)",
    ast.Subscript: "subscripts (a[b])",
    ast.ListComp: "comprehensions",
    ast.SetComp: "comprehensions",
    ast.DictComp: "comprehensions",
    ast.GeneratorExp: "comprehensions",
    ast.Lambda: "lambdas",
    ast.NamedExpr: "assignments (:=)",
    ast.IfExp: "conditional expressions (a if b else c)",
    ast.List: "lists",
    ast.Tuple: "tuples",
    ast.Dict: "dictionaries",
    ast.Set: "sets",
    ast.JoinedStr: "f-strings",
    ast.Starred: "starred arguments",
    ast.Await: "await",
    ast.Yield: "yield",
    ast.YieldFrom: "yield",
}


def _describe(node: ast.AST) -> str:
    for kind, words in _REFUSED_NAMES.items():
        if isinstance(node, kind):
            return words
    if isinstance(node, ast.BinOp):
        return f"the operator {type(node.op).__name__}"
    if isinstance(node, ast.UnaryOp):
        return f"the operator {type(node.op).__name__}"
    if isinstance(node, ast.Compare):
        return f"the comparison {type(node.ops[0]).__name__}"
    return type(node).__name__


def parse(text: str, columns: Mapping[str, Literal["numeric", "categorical"]]) -> Expression:
    """Parse and type-check ``text`` against the row's columns (name → kind).

    Raises :class:`ExpressionRefused` naming the construct, the unknown column or the
    type that does not fit.
    """
    if not text.strip():
        raise ExpressionRefused("the expression is empty")
    if len(text) > MAX_CHARS:
        raise ExpressionRefused(f"the expression is {len(text)} characters; at most {MAX_CHARS}")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionRefused(f"not a valid expression: {exc.msg}") from exc
    count = sum(1 for _ in ast.walk(tree))
    if count > MAX_NODES:
        raise ExpressionRefused(f"the expression has {count} parts; at most {MAX_NODES}")
    return Expression(text, _node(tree.body, columns))


def _column(name: str, columns: Mapping[str, Literal["numeric", "categorical"]]) -> Col:
    if name not in columns:
        known = ", ".join(sorted(columns)[:12])
        raise ExpressionRefused(f"{name!r} is not a column; columns here: {known}")
    return Col(name, columns[name])


def _need(node: Node, wanted: Type, what: str) -> None:
    if node.type != wanted:
        raise ExpressionRefused(
            f"{what} needs a {_TYPE_WORDS[wanted]}, not a {_TYPE_WORDS[node.type]}"
        )


_TYPE_WORDS: dict[Type, str] = {"numeric": "number", "categorical": "string", "bool": "condition"}


def _node(node: ast.expr, columns: Mapping[str, Literal["numeric", "categorical"]]) -> Node:
    match node:
        case ast.Constant(value=bool() as value):
            return Const(value, "bool")
        case ast.Constant(value=int() | float() as value):
            if not math.isfinite(float(value)):
                raise ExpressionRefused("numbers must be finite")
            return Const(float(value), "numeric")
        case ast.Constant(value=str() as value):
            return Const(value, "categorical")
        case ast.Constant():
            raise ExpressionRefused(
                f"the constant {node.value!r}; use isnull(x) for a missing value"
            )
        case ast.Name(id=name):
            if name in _FUNCTIONS:
                raise ExpressionRefused(f"{name} is a function; call it as {name}(…)")
            return _column(name, columns)
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            inner = _node(operand, columns)
            _need(inner, "numeric", "unary minus")
            return Unary("neg", inner, "numeric")
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            inner = _node(operand, columns)
            _need(inner, "bool", "not")
            return Unary("not", inner, "bool")
        case ast.BinOp(op=op, left=left, right=right) if type(op) in _ARITH:
            a, b = _node(left, columns), _node(right, columns)
            symbol = _ARITH[type(op)]
            _need(a, "numeric", f"'{symbol}'")
            _need(b, "numeric", f"'{symbol}'")
            return Binary(symbol, a, b, "numeric")
        case ast.BoolOp(op=op, values=values):
            parts = tuple(_node(v, columns) for v in values)
            word: Literal["and", "or"] = "and" if isinstance(op, ast.And) else "or"
            for part in parts:
                _need(part, "bool", f"'{word}'")
            return Logic(word, parts, "bool")
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            return _compare(left, ops, comparators, columns)
        case ast.Call(func=ast.Name(id=name), args=args, keywords=keywords) if name in _FUNCTIONS:
            if keywords:
                raise ExpressionRefused(f"{name}() takes no keyword arguments")
            return _call(name, args, columns)
        case ast.Call(func=ast.Name(id=name)):
            raise ExpressionRefused(
                f"calls to {name}(); the functions allowed are {', '.join(_FUNCTIONS)}"
            )
        case ast.Call():
            raise ExpressionRefused("calls to anything but the allowed functions")
        case _:
            raise ExpressionRefused(f"{_describe(node)} is not allowed")


def _compare(
    left: ast.expr,
    ops: list[ast.cmpop],
    comparators: list[ast.expr],
    columns: Mapping[str, Literal["numeric", "categorical"]],
) -> Node:
    for op in ops:
        if type(op) not in _CMP:
            raise ExpressionRefused(f"{_describe_cmp(op)} is not allowed")
    nodes = [_node(left, columns), *(_node(c, columns) for c in comparators)]
    parts: list[Node] = []
    for i, op in enumerate(ops):
        symbol = _CMP[type(op)]
        a, b = nodes[i], nodes[i + 1]
        if a.type != b.type:
            raise ExpressionRefused(
                f"'{symbol}' compares a {_TYPE_WORDS[a.type]} with a {_TYPE_WORDS[b.type]}"
            )
        if symbol not in ("==", "!=") and a.type != "numeric":
            raise ExpressionRefused(f"'{symbol}' needs numbers; compare strings with == or !=")
        parts.append(Compare(symbol, a, b, "bool"))
    return parts[0] if len(parts) == 1 else Logic("and", tuple(parts), "bool")


def _describe_cmp(op: ast.cmpop) -> str:
    words: dict[type[ast.cmpop], str] = {
        ast.Is: "'is'",
        ast.IsNot: "'is not'",
        ast.In: "'in'",
        ast.NotIn: "'not in'",
    }
    return words.get(type(op), type(op).__name__) + " (use isnull(x) for a missing value)"


def _call(
    name: str, args: list[ast.expr], columns: Mapping[str, Literal["numeric", "categorical"]]
) -> Node:
    if name == "col":
        if len(args) != 1 or not (
            isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)
        ):
            raise ExpressionRefused(
                'col() takes one column name in quotes, e.g. col("od280/od315")'
            )
        return _column(args[0].value, columns)
    nodes = tuple(_node(a, columns) for a in args)
    if name in ("isnull", "notnull"):
        if len(nodes) != 1:
            raise ExpressionRefused(f"{name}() takes one argument")
        return Call(name, nodes, "bool")
    if name in ("abs", "log", "sqrt"):
        if len(nodes) != 1:
            raise ExpressionRefused(f"{name}() takes one argument")
        _need(nodes[0], "numeric", f"{name}()")
        return Call(name, nodes, "numeric")
    if name == "round":
        if len(nodes) != 2 or not (
            isinstance(nodes[1], Const)
            and isinstance(nodes[1].value, float)
            and nodes[1].value.is_integer()
            and 0 <= nodes[1].value <= 12
        ):
            raise ExpressionRefused("round() takes a number and a whole number of digits, 0 to 12")
        _need(nodes[0], "numeric", "round()")
        return Call(name, nodes, "numeric")
    # min, max
    if len(nodes) < 2:
        raise ExpressionRefused(f"{name}() takes two or more numbers")
    for node in nodes:
        _need(node, "numeric", f"{name}()")
    return Call(name, nodes, "numeric")


# ---------------------------------------------------------------------------- evaluation


def evaluate(expression: Expression, row: Mapping[str, Any]) -> Value:
    """The value of ``expression`` on one row (values by column name). ``None`` when it
    cannot be computed for this row."""
    return _eval(expression.root, row)


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _num(value: Value) -> float | None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return None
    return float(value)


_ARITH_FN: dict[str, Callable[[float, float], float | None]] = {
    "+": lambda a, b: _finite(a + b),
    "-": lambda a, b: _finite(a - b),
    "*": lambda a, b: _finite(a * b),
    "/": lambda a, b: _finite(a / b) if b != 0 else None,
}


def _eval(node: Node, row: Mapping[str, Any]) -> Value:
    match node:
        case Const(value=value):
            return value
        case Col(name=name, type=kind):
            value = row.get(name)
            if value is None:
                return None
            if kind == "numeric":
                return _num(value)
            return value if isinstance(value, str) else None
        case Unary(op="neg", operand=operand):
            number = _num(_eval(operand, row))
            return None if number is None else -number
        case Unary(operand=operand):
            truth = _eval(operand, row)
            return None if truth is None else not truth
        case Binary(op=op, left=left, right=right):
            a, b = _num(_eval(left, row)), _num(_eval(right, row))
            if a is None or b is None:
                return None
            return _ARITH_FN[op](a, b)
        case Compare(op=op, left=left, right=right):
            return _compare_values(op, _eval(left, row), _eval(right, row))
        case Logic(op=op, operands=operands):
            return _logic(op, operands, row)
        case Call(name=name, args=args):
            return _call_value(name, args, row)


def _compare_values(op: str, a: Value, b: Value) -> bool | None:
    if a is None or b is None:
        return None
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return None
    return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def _logic(op: str, operands: tuple[Node, ...], row: Mapping[str, Any]) -> bool | None:
    """Three-valued: ``and`` is false if any part is false, ``or`` true if any part is true;
    otherwise a missing part makes the whole missing."""
    unknown = False
    for operand in operands:
        value = _eval(operand, row)
        if value is None:
            unknown = True
        elif op == "and" and value is False:
            return False
        elif op == "or" and value is True:
            return True
    if unknown:
        return None
    return op == "and"


def _call_value(name: str, args: tuple[Node, ...], row: Mapping[str, Any]) -> Value:
    if name == "isnull":
        return _eval(args[0], row) is None
    if name == "notnull":
        return _eval(args[0], row) is not None
    numbers = [_num(_eval(a, row)) for a in args]
    if any(n is None for n in numbers):
        return None
    values = [n for n in numbers if n is not None]
    if name == "abs":
        return abs(values[0])
    if name == "log":
        return math.log(values[0]) if values[0] > 0 else None
    if name == "sqrt":
        return math.sqrt(values[0]) if values[0] >= 0 else None
    if name == "round":
        return float(round(values[0], int(values[1])))
    return min(values) if name == "min" else max(values)
