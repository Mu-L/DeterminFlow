"""
条件表达式解析器

支持比较表达式和 AND/OR/NOT 复合表达式评估。
变量通过 {{key}} 引用，评估前需先调用 resolve_placeholders 替换为实际值。

语法：
  expr       → or_expr
  or_expr    → and_expr ("OR" and_expr)*
  and_expr   → unary_expr ("AND" unary_expr)*
  unary_expr → "NOT" unary_expr | primary
  primary    → "(" expr ")" | comparison
  comparison → value OP value
  value      → NUMBER | STRING

运算符优先级（从高到低）：() > NOT > 比较符 > AND > OR

兼容模式：不含 AND/OR/NOT 的表达式使用原有简单解析器，行为不变。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 比较操作符（复用原有逻辑）
# ============================================================

def _numeric_cmp(a: str, b: str, op: str) -> bool:
    """将对数字字符串转为数值比较，非数字则字符串比较。"""
    try:
        na, nb = float(a), float(b)
    except (ValueError, TypeError):
        na, nb = a.strip(), b.strip()
    if op == ">=":
        return na >= nb
    if op == "<=":
        return na <= nb
    if op == ">":
        return na > nb
    return na < nb


OPS = {
    "!=": lambda a, b: a != b,
    "==": lambda a, b: a == b,
    ">=": lambda a, b: _numeric_cmp(a, b, ">="),
    "<=": lambda a, b: _numeric_cmp(a, b, "<="),
    ">":  lambda a, b: _numeric_cmp(a, b, ">"),
    "<":  lambda a, b: _numeric_cmp(a, b, "<"),
}

# 简单比较操作符匹配（按长度降序，确保 >= 在 > 之前匹配）
_SIMPLE_OP_RE = re.compile(r"\s*(!=|==|>=|<=|>|<)\s*")

# 检测复合表达式的正则（AND/OR/NOT 作为独立词出现）
_COMPOUND_KEYWORD_RE = re.compile(r'(?:^|\s)(AND|OR|NOT)(?:\s|$)', re.IGNORECASE)


# ============================================================
# Token 定义
# ============================================================

class TokenType(Enum):
    NUMBER = auto()
    STRING = auto()
    OP = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    LPAREN = auto()
    RPAREN = auto()
    EOF = auto()


@dataclass
class Token:
    type: TokenType
    value: str
    pos: int = 0


# ============================================================
# Lexer（词法分析器）
# ============================================================

_TOKEN_RE = re.compile(r"""
    \s*
    (?:
        (?P<NUMBER>\d+(?:\.\d+)?)
        |(?P<DSTRING>"[^"]*")
        |(?P<SSTRING>'[^']*')
        |(?P<OP>!=|==|>=|<=|>|<)
        |(?P<WORD>[a-zA-Z_]\w*)
        |(?P<LPAREN>\()
        |(?P<RPAREN>\))
        |(?P<UNEXPECTED>\S)
    )
""", re.VERBOSE)

_KEYWORD_MAP = {"AND": TokenType.AND, "OR": TokenType.OR, "NOT": TokenType.NOT}


def _lex(expression: str) -> List[Token]:
    """词法分析：将表达式字符串分解为 Token 列表。"""
    tokens: List[Token] = []
    pos = 0
    for m in _TOKEN_RE.finditer(expression):
        pos = m.start()
        kind = m.lastgroup
        value = m.group(kind)  # 仅取命名组匹配内容，排除前导 \s*

        if kind == "NUMBER":
            tokens.append(Token(TokenType.NUMBER, value, pos))
        elif kind in ("DSTRING", "SSTRING"):
            # 去掉引号，保留内容
            tokens.append(Token(TokenType.STRING, value[1:-1], pos))
        elif kind == "OP":
            tokens.append(Token(TokenType.OP, value, pos))
        elif kind == "WORD":
            upper = value.upper()
            if upper in _KEYWORD_MAP:
                tokens.append(Token(_KEYWORD_MAP[upper], upper, pos))
            else:
                tokens.append(Token(TokenType.STRING, value, pos))
        elif kind == "LPAREN":
            tokens.append(Token(TokenType.LPAREN, value, pos))
        elif kind == "RPAREN":
            tokens.append(Token(TokenType.RPAREN, value, pos))
        elif kind == "UNEXPECTED":
            tokens.append(Token(TokenType.STRING, value, pos))

    tokens.append(Token(TokenType.EOF, "", len(expression)))
    return tokens


# ============================================================
# Parser（递归下降解析器）
# ============================================================

class ParseError(ValueError):
    """表达式解析错误，携带位置信息。"""
    def __init__(self, msg: str, pos: int = 0):
        super().__init__(msg)
        self.pos = pos


class Parser:
    """递归下降解析器。

    语法规则：
      expr       → or_expr EOF
      or_expr    → and_expr ("OR" and_expr)*
      and_expr   → unary_expr ("AND" unary_expr)*
      unary_expr → "NOT" unary_expr | primary
      primary    → "(" expr ")" | comparison
      comparison → value OP value
      value      → STRING | NUMBER
    """

    def __init__(self, tokens: List[Token]):
        self._tokens = tokens
        self._idx = 0

    @property
    def _cur(self) -> Token:
        return self._tokens[self._idx]

    def _advance(self) -> Token:
        t = self._cur
        self._idx += 1
        return t

    def _expect(self, ttype: TokenType, msg: str = "") -> Token:
        if self._cur.type != ttype:
            raise ParseError(
                msg or f"期望 {ttype.name}，实际是 {self._cur.type.name}",
                self._cur.pos,
            )
        return self._advance()

    def _match(self, *ttypes: TokenType) -> Optional[Token]:
        if self._cur.type in ttypes:
            return self._advance()
        return None

    # ---- 公开接口 ----

    def parse(self) -> bool:
        """解析并求值表达式。"""
        result = self._expr()
        self._expect(TokenType.EOF, "表达式后有额外内容")
        return result

    # ---- 递归下降规则 ----

    def _expr(self) -> bool:
        """expr → or_expr"""
        return self._or_expr()

    def _or_expr(self) -> bool:
        """or_expr → and_expr (OR and_expr)*"""
        left = self._and_expr()
        while self._match(TokenType.OR):
            right = self._and_expr()
            left = left or right
        return left

    def _and_expr(self) -> bool:
        """and_expr → unary_expr (AND unary_expr)*"""
        left = self._unary_expr()
        while self._match(TokenType.AND):
            right = self._unary_expr()
            left = left and right
        return left

    def _unary_expr(self) -> bool:
        """unary_expr → NOT unary_expr | primary"""
        if self._match(TokenType.NOT):
            return not self._unary_expr()
        return self._primary()

    def _primary(self) -> bool:
        """primary → ( expr ) | comparison"""
        if self._match(TokenType.LPAREN):
            result = self._expr()
            self._expect(TokenType.RPAREN, "缺少右括号 ')'")
            return result
        return self._comparison()

    def _comparison(self) -> bool:
        """comparison → value OP value"""
        left_tok: Token
        if self._cur.type in (TokenType.STRING, TokenType.NUMBER):
            left_tok = self._advance()
        else:
            raise ParseError(
                f"期望值（数字或字符串），实际是 {self._cur.type.name}",
                self._cur.pos,
            )

        if self._cur.type != TokenType.OP:
            raise ParseError(
                f"期望比较操作符 (== != >= <= > <)，实际是 {self._cur.type.name}: '{self._cur.value}'",
                self._cur.pos,
            )
        op_tok = self._advance()

        if self._cur.type not in (TokenType.STRING, TokenType.NUMBER):
            raise ParseError(
                f"期望值（数字或字符串），实际是 {self._cur.type.name}",
                self._cur.pos,
            )
        right_tok = self._advance()

        op_fn = OPS.get(op_tok.value)
        if op_fn is None:
            raise ParseError(f"不支持的操作符: {op_tok.value}", op_tok.pos)

        try:
            return op_fn(left_tok.value, right_tok.value)
        except Exception as e:
            raise ParseError(f"比较求值失败: {e}", op_tok.pos) from e


# ============================================================
# 公开 API
# ============================================================

def evaluate_condition(expression: str) -> bool:
    """评估条件表达式，返回 True/False。

    自动检测表达式类型：
    - 简单表达式（无 AND/OR/NOT）：使用原有简单解析器，行为完全不变
    - 复合表达式（含 AND/OR/NOT）：使用递归下降解析器

    Args:
        expression: 已被 resolve_placeholders 替换为实际值的表达式
                    如 "80 >= 60" 或 "80 >= 60 AND 80 < 100"

    Returns:
        表达式计算结果

    Raises:
        ValueError: 表达式格式无效
    """
    expr = expression.strip()
    if not expr:
        raise ValueError("条件表达式为空")

    # 检测是否为复合表达式
    if _COMPOUND_KEYWORD_RE.search(expr):
        return _evaluate_compound(expr)

    return _evaluate_simple(expr)


def _evaluate_simple(expression: str) -> bool:
    """原有简单比较表达式评估，行为完全不变。"""
    parts = _SIMPLE_OP_RE.split(expression, maxsplit=1)
    if len(parts) != 3:
        raise ValueError(
            f"条件表达式格式无效: '{expression}'，"
            f"期望格式: 值 操作符 值 (如 '{{score}} >= 60')"
        )

    left, op, right = parts[0].strip(), parts[1], parts[2].strip()
    if op not in OPS:
        raise ValueError(f"不支持的操作符: {op}")

    try:
        return OPS[op](left, right)
    except Exception as e:
        raise ValueError(f"条件评估失败 '{expression}': {e}") from e


def _evaluate_compound(expression: str) -> bool:
    """复合表达式评估（递归下降解析）。"""
    try:
        tokens = _lex(expression)
        parser = Parser(tokens)
        return parser.parse()
    except ParseError as e:
        raise ValueError(
            f"条件表达式解析失败: '{expression}'，{e}"
        ) from e
    except Exception as e:
        raise ValueError(
            f"条件表达式评估失败: '{expression}'，{e}"
        ) from e
