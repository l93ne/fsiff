#!/usr/bin/env python3
"""Компилятор и отладчик языка Mini-PL — целиком в одном файле.

Mini-PL — маленький учебный императивный язык. Здесь реализован полный путь
от текста программы до её выполнения:

    исходный текст
        -> лексический анализ      (поток токенов)
        -> синтаксический анализ   (абстрактное синтаксическое дерево)
        -> семантический анализ    (таблица символов, проверка типов)
        -> генерация кода          (байткод стековой машины)
        -> виртуальная машина      (выполнение) / отладчик

Оглавление файла
    ЧАСТЬ 1  ИСХОДНЫЙ ТЕКСТ: позиции и диапазоны
    ЧАСТЬ 2  ДИАГНОСТИКА: коды ошибок, цвета, красивый вывод
    ЧАСТЬ 3  ЗНАЧЕНИЯ Mini-PL и их печать
    ЧАСТЬ 4  СИСТЕМА ТИПОВ и таблицы допустимых операций
    ЧАСТЬ 5  ТОКЕНЫ
    ЧАСТЬ 6  ЛЕКСИЧЕСКИЙ АНАЛИЗАТОР
    ЧАСТЬ 7  АБСТРАКТНОЕ СИНТАКСИЧЕСКОЕ ДЕРЕВО
    ЧАСТЬ 8  СИНТАКСИЧЕСКИЙ АНАЛИЗАТОР (рекурсивный спуск)
    ЧАСТЬ 9  ТАБЛИЦА СИМВОЛОВ
    ЧАСТЬ 10 СЕМАНТИЧЕСКИЙ АНАЛИЗ и проверка типов
    ЧАСТЬ 11 БАЙТКОД: набор команд, Chunk, дизассемблер
    ЧАСТЬ 12 ГЕНЕРАТОР КОДА
    ЧАСТЬ 13 ВИРТУАЛЬНАЯ МАШИНА
    ЧАСТЬ 14 КОНВЕЙЕР КОМПИЛЯЦИИ
    ЧАСТЬ 15 ИНТЕРАКТИВНЫЙ ОТЛАДЧИК
    ЧАСТЬ 16 КОМАНДНАЯ СТРОКА

Грамматика (EBNF)
    <prog>   ::= <stmts>
    <stmts>  ::= { <stmt> ";" }
    <stmt>   ::= "var" <ident> ":" <type> [ ":=" <expr> ]
               | <ident> ":=" <expr>
               | "for" <ident> "in" <expr> ".." <expr> "do" <stmts> "end" "for"
               | "while" <expr> "do" <stmts> "end" "while"
               | "if" <expr> "do" <stmts> [ "else" <stmts> ] "end" "if"
               | "read" <ident>
               | "print" <expr>
               | "assert" "(" <expr> ")"
    <expr>   ::= <expr> <binop> <expr> | <unop> <expr> | <opnd>
    <opnd>   ::= <int> | <string> | <bool> | <ident> | "(" <expr> ")"
    <type>   ::= "int" | "string" | "bool"

    Приоритет операторов (от слабого к сильному):
        |   &   = <> < <= > >=   + -   * / %   унарные ! - +

    Комментарии: // до конца строки и /* ... */ (допускается вложенность).

Отличия от исходной спецификации Mini-PL (сознательные расширения):
    * выражения разбираются с приоритетами, поэтому «a + b * c» писать можно;
    * добавлены if/else, while, литералы true/false и операторы <= >= <> % |;
    * переменная цикла for объявляется неявно, если она не объявлена заранее;
    * операторы & и | вычисляются сокращённо (short-circuit).

Пример программы
    var n : int;
    read n;
    var f : int := 1;
    for i in 1..n do
        f := f * i;
    end for;
    print "n! = ";
    print f;
    print "\\n";
    assert (f > 0);

Запуск
    python3 minipl.py program.mpl              выполнить программу
    python3 minipl.py program.mpl --debug      выполнить под отладчиком
    python3 minipl.py --help                   полный список ключей

Используется только стандартная библиотека Python 3.8+.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any, Callable, Dict, List, Optional, Set, TextIO


##############################################################################
# ЧАСТЬ 1. ИСХОДНЫЙ ТЕКСТ: ПОЗИЦИИ И ДИАПАЗОНЫ
##############################################################################


@dataclass(frozen=True)
class Position:
    """Позиция одного символа в исходном файле (1-based строка и колонка)."""

    offset: int
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


@dataclass(frozen=True)
class Span:
    """Полуоткрытый диапазон [start, end) в исходном тексте."""

    start: Position
    end: Position

    @staticmethod
    def merge(first: "Span", second: "Span") -> "Span":
        start = first.start if first.start.offset <= second.start.offset else second.start
        end = first.end if first.end.offset >= second.end.offset else second.end
        return Span(start, end)

    @property
    def length(self) -> int:
        return max(1, self.end.offset - self.start.offset)

    def __str__(self) -> str:
        return f"{self.start}"


class Source:
    """Исходный файл вместе с индексом строк."""

    def __init__(self, text: str, name: str = "<stdin>") -> None:
        self.text = text
        self.name = name
        self.lines = text.splitlines()

    @classmethod
    def from_file(cls, path: str) -> "Source":
        with open(path, "r", encoding="utf-8") as handle:
            return cls(handle.read(), path)

    def line_text(self, line: int) -> str:
        if 1 <= line <= len(self.lines):
            return self.lines[line - 1]
        return ""

    @property
    def line_count(self) -> int:
        return len(self.lines)


##############################################################################
# ЧАСТЬ 2. ДИАГНОСТИКА: КОДЫ ОШИБОК, ЦВЕТА, КРАСИВЫЙ ВЫВОД
##############################################################################


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


# Коды диагностик сгруппированы по фазам компиляции:
#   E1xxx - лексический анализ
#   E2xxx - синтаксический анализ
#   E3xxx - семантический анализ
#   E4xxx - время выполнения
class Code:
    UNKNOWN_CHARACTER = "E1001"
    UNTERMINATED_STRING = "E1002"
    UNTERMINATED_COMMENT = "E1003"
    BAD_ESCAPE = "E1004"
    INT_OVERFLOW = "E1005"

    UNEXPECTED_TOKEN = "E2001"
    MISSING_SEMICOLON = "E2002"
    EXPECTED_EXPRESSION = "E2003"
    EXPECTED_IDENTIFIER = "E2004"
    EXPECTED_TYPE = "E2005"
    UNCLOSED_PAREN = "E2006"
    EMPTY_BLOCK = "E2007"

    UNDECLARED_VARIABLE = "E3001"
    REDECLARED_VARIABLE = "E3002"
    TYPE_MISMATCH = "E3003"
    BAD_OPERAND_TYPE = "E3004"
    ASSIGN_TO_LOOP_VARIABLE = "E3005"
    NON_INT_LOOP_RANGE = "E3006"
    ASSERT_NOT_BOOL = "E3007"
    READ_BAD_TYPE = "E3008"
    UNUSED_VARIABLE = "E3009"

    DIVISION_BY_ZERO = "E4001"
    ASSERTION_FAILED = "E4002"
    BAD_INPUT = "E4003"
    UNINITIALIZED = "E4004"
    STEP_LIMIT = "E4005"


class Colors:
    """ANSI-цвета; отключаются, если вывод не в терминал."""

    enabled = True

    RED = "\033[31;1m"
    YELLOW = "\033[33;1m"
    BLUE = "\033[34;1m"
    CYAN = "\033[36;1m"
    GREEN = "\033[32;1m"
    GREY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def paint(cls, text: str, color: str) -> str:
        if not cls.enabled:
            return text
        return f"{color}{text}{cls.RESET}"

    @classmethod
    def configure(cls, stream: TextIO, force: Optional[bool] = None) -> None:
        if force is not None:
            cls.enabled = force
        else:
            cls.enabled = hasattr(stream, "isatty") and stream.isatty()


@dataclass
class Diagnostic:
    """Одно сообщение об ошибке с привязкой к участку исходного кода."""

    severity: Severity
    code: str
    message: str
    span: Optional[Span] = None
    hint: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def render(self, source: Optional[Source]) -> str:
        color = {
            Severity.ERROR: Colors.RED,
            Severity.WARNING: Colors.YELLOW,
            Severity.NOTE: Colors.CYAN,
        }[self.severity]
        head = Colors.paint(f"{self.severity.value}[{self.code}]", color)
        out = [f"{head}: {Colors.paint(self.message, Colors.BOLD)}"]

        if self.span is not None and source is not None:
            line_no = self.span.start.line
            column = self.span.start.column
            gutter = len(str(line_no))
            pad = " " * gutter
            arrow = Colors.paint("-->", Colors.BLUE)
            bar = Colors.paint("|", Colors.BLUE)
            out.append(f"{pad} {arrow} {source.name}:{line_no}:{column}")
            out.append(f"{pad} {bar}")
            line_text = source.line_text(line_no).replace("\t", " ")
            out.append(f"{Colors.paint(str(line_no), Colors.BLUE)} {bar} {line_text}")
            width = self.span.length
            if self.span.end.line != line_no:
                width = max(1, len(line_text) - column + 1)
            width = max(1, min(width, max(1, len(line_text) - column + 1)))
            caret = Colors.paint("^" * width, color)
            tail = f" {self.hint}" if self.hint else ""
            out.append(f"{pad} {bar} {' ' * (column - 1)}{caret}{Colors.paint(tail, color)}")
        elif self.hint:
            out.append(f"  {Colors.paint('hint:', Colors.CYAN)} {self.hint}")

        for note in self.notes:
            out.append(f"  {Colors.paint('note:', Colors.CYAN)} {note}")
        return "\n".join(out)


class MiniPLError(Exception):
    """Базовое исключение компилятора, несущее готовую диагностику."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class CompileError(MiniPLError):
    """Ошибка, не позволяющая продолжить разбор (используется для восстановления)."""


class RuntimeMiniPLError(MiniPLError):
    """Ошибка времени выполнения виртуальной машины."""


class DiagnosticBag:
    """Накопитель диагностик: позволяет собрать несколько ошибок за проход."""

    def __init__(self, source: Optional[Source] = None) -> None:
        self.source = source
        self.items: List[Diagnostic] = []

    def add(self, diagnostic: Diagnostic) -> Diagnostic:
        self.items.append(diagnostic)
        return diagnostic

    def error(
        self,
        code: str,
        message: str,
        span: Optional[Span] = None,
        hint: Optional[str] = None,
        notes: Optional[List[str]] = None,
    ) -> Diagnostic:
        return self.add(
            Diagnostic(Severity.ERROR, code, message, span, hint, list(notes or []))
        )

    def warning(
        self,
        code: str,
        message: str,
        span: Optional[Span] = None,
        hint: Optional[str] = None,
    ) -> Diagnostic:
        return self.add(Diagnostic(Severity.WARNING, code, message, span, hint))

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.WARNING]

    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self.items)

    def sort(self) -> None:
        self.items.sort(key=lambda d: d.span.start.offset if d.span else -1)

    def report(self, stream: TextIO = sys.stderr) -> None:
        if not self.items:
            return
        self.sort()
        for diagnostic in self.items:
            print(diagnostic.render(self.source), file=stream)
            print(file=stream)
        errors = len(self.errors)
        warnings = len(self.warnings)
        parts = []
        if errors:
            parts.append(Colors.paint(f"{errors} error(s)", Colors.RED))
        if warnings:
            parts.append(Colors.paint(f"{warnings} warning(s)", Colors.YELLOW))
        if parts:
            print("compilation summary: " + ", ".join(parts), file=stream)


##############################################################################
# ЧАСТЬ 3. ЗНАЧЕНИЯ MINI-PL И ИХ ПЕЧАТЬ
##############################################################################


def format_value(value: Any) -> str:
    """Как значение печатается оператором print."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    return str(value)


def repr_value(value: Any) -> str:
    """Как значение показывается в отладчике и дизассемблере."""

    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(value)
    return str(value)


##############################################################################
# ЧАСТЬ 4. СИСТЕМА ТИПОВ И ТАБЛИЦЫ ДОПУСТИМЫХ ОПЕРАЦИЙ
##############################################################################


class Type(Enum):
    INT = "int"
    STRING = "string"
    BOOL = "bool"
    # Псевдотип для выражений, содержащих ошибку: гасит каскад сообщений.
    ERROR = "<error>"

    def __str__(self) -> str:
        return self.value


def default_value(type_: Type):
    if type_ is Type.INT:
        return 0
    if type_ is Type.STRING:
        return ""
    if type_ is Type.BOOL:
        return False
    return None


# Таблица допустимых бинарных операций: (оператор, тип операндов) -> тип результата
BINARY_RULES = {
    ("+", Type.INT): Type.INT,
    ("+", Type.STRING): Type.STRING,
    ("-", Type.INT): Type.INT,
    ("*", Type.INT): Type.INT,
    ("/", Type.INT): Type.INT,
    ("%", Type.INT): Type.INT,
    ("<", Type.INT): Type.BOOL,
    ("<", Type.STRING): Type.BOOL,
    ("<", Type.BOOL): Type.BOOL,
    ("<=", Type.INT): Type.BOOL,
    ("<=", Type.STRING): Type.BOOL,
    (">", Type.INT): Type.BOOL,
    (">", Type.STRING): Type.BOOL,
    (">=", Type.INT): Type.BOOL,
    (">=", Type.STRING): Type.BOOL,
    ("=", Type.INT): Type.BOOL,
    ("=", Type.STRING): Type.BOOL,
    ("=", Type.BOOL): Type.BOOL,
    ("<>", Type.INT): Type.BOOL,
    ("<>", Type.STRING): Type.BOOL,
    ("<>", Type.BOOL): Type.BOOL,
    ("&", Type.BOOL): Type.BOOL,
    ("|", Type.BOOL): Type.BOOL,
}

UNARY_RULES = {
    ("!", Type.BOOL): Type.BOOL,
    ("-", Type.INT): Type.INT,
    ("+", Type.INT): Type.INT,
}


##############################################################################
# ЧАСТЬ 5. ТОКЕНЫ
##############################################################################


class TokenType(Enum):
    # литералы и идентификаторы
    INT_LITERAL = auto()
    STRING_LITERAL = auto()
    BOOL_LITERAL = auto()
    IDENTIFIER = auto()

    # ключевые слова
    VAR = auto()
    FOR = auto()
    END = auto()
    IN = auto()
    DO = auto()
    READ = auto()
    PRINT = auto()
    ASSERT = auto()
    IF = auto()
    ELSE = auto()
    WHILE = auto()

    # имена типов
    INT = auto()
    STRING = auto()
    BOOL = auto()

    # операторы
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    LESS = auto()
    LESS_EQUAL = auto()
    GREATER = auto()
    GREATER_EQUAL = auto()
    EQUAL = auto()
    NOT_EQUAL = auto()
    AND = auto()
    OR = auto()
    NOT = auto()

    # разделители
    ASSIGN = auto()
    COLON = auto()
    SEMICOLON = auto()
    LPAREN = auto()
    RPAREN = auto()
    RANGE = auto()

    EOF = auto()


KEYWORDS = {
    "var": TokenType.VAR,
    "for": TokenType.FOR,
    "end": TokenType.END,
    "in": TokenType.IN,
    "do": TokenType.DO,
    "read": TokenType.READ,
    "print": TokenType.PRINT,
    "assert": TokenType.ASSERT,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "while": TokenType.WHILE,
    "int": TokenType.INT,
    "string": TokenType.STRING,
    "bool": TokenType.BOOL,
    "true": TokenType.BOOL_LITERAL,
    "false": TokenType.BOOL_LITERAL,
}

TYPE_TOKENS = {TokenType.INT, TokenType.STRING, TokenType.BOOL}

# Человекочитаемые названия для сообщений об ошибках.
TOKEN_DESCRIPTIONS = {
    TokenType.INT_LITERAL: "целочисленный литерал",
    TokenType.STRING_LITERAL: "строковый литерал",
    TokenType.BOOL_LITERAL: "логический литерал",
    TokenType.IDENTIFIER: "идентификатор",
    TokenType.ASSIGN: "':='",
    TokenType.COLON: "':'",
    TokenType.SEMICOLON: "';'",
    TokenType.LPAREN: "'('",
    TokenType.RPAREN: "')'",
    TokenType.RANGE: "'..'",
    TokenType.EOF: "конец файла",
}


@dataclass
class Token:
    type: TokenType
    lexeme: str
    span: Span
    value: Any = None

    def describe(self) -> str:
        if self.type in TOKEN_DESCRIPTIONS:
            return TOKEN_DESCRIPTIONS[self.type]
        return f"'{self.lexeme}'"

    def __str__(self) -> str:
        value = "" if self.value is None else f" value={self.value!r}"
        return f"{self.type.name:<14} {self.lexeme!r:<16} @{self.span.start}{value}"


##############################################################################
# ЧАСТЬ 6. ЛЕКСИЧЕСКИЙ АНАЛИЗАТОР
##############################################################################


INT_MAX = 2 ** 31 - 1

ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    '"': '"',
    "'": "'",
    "\\": "\\",
}


class Lexer:
    """Переводит текст программы в поток токенов.

    Лексер не останавливается на первой ошибке: все проблемы складываются в
    `DiagnosticBag`, а сканирование продолжается со следующего символа, чтобы
    пользователь увидел сразу все опечатки.
    """

    def __init__(self, source: Source, diagnostics: Optional[DiagnosticBag] = None) -> None:
        self.source = source
        self.text = source.text
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag(source)
        self.offset = 0
        self.line = 1
        self.column = 1

    # --- низкоуровневые помощники -------------------------------------------------

    def _position(self) -> Position:
        return Position(self.offset, self.line, self.column)

    def _at_end(self) -> bool:
        return self.offset >= len(self.text)

    def _peek(self, ahead: int = 0) -> str:
        index = self.offset + ahead
        if index >= len(self.text):
            return ""
        return self.text[index]

    def _advance(self) -> str:
        char = self.text[self.offset]
        self.offset += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _match(self, expected: str) -> bool:
        if self._peek() == expected:
            self._advance()
            return True
        return False

    def _span(self, start: Position) -> Span:
        return Span(start, self._position())

    def _token(self, type_: TokenType, start: Position, value=None) -> Token:
        span = self._span(start)
        lexeme = self.text[start.offset : self.offset]
        return Token(type_, lexeme, span, value)

    # --- основной цикл ------------------------------------------------------------

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        while True:
            token = self.next_token()
            if token is None:
                continue
            tokens.append(token)
            if token.type is TokenType.EOF:
                return tokens

    def next_token(self) -> Optional[Token]:
        self._skip_trivia()
        start = self._position()
        if self._at_end():
            return Token(TokenType.EOF, "", self._span(start))

        char = self._advance()

        if char.isdigit():
            return self._number(start)
        if char.isalpha() or char == "_":
            return self._identifier(start)
        if char == '"':
            return self._string(start)

        simple = {
            "+": TokenType.PLUS,
            "-": TokenType.MINUS,
            "*": TokenType.STAR,
            "%": TokenType.PERCENT,
            "=": TokenType.EQUAL,
            "&": TokenType.AND,
            "|": TokenType.OR,
            "!": TokenType.NOT,
            ";": TokenType.SEMICOLON,
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
        }
        if char in simple:
            return self._token(simple[char], start)

        if char == "/":
            return self._token(TokenType.SLASH, start)
        if char == ":":
            if self._match("="):
                return self._token(TokenType.ASSIGN, start)
            return self._token(TokenType.COLON, start)
        if char == "<":
            if self._match("="):
                return self._token(TokenType.LESS_EQUAL, start)
            if self._match(">"):
                return self._token(TokenType.NOT_EQUAL, start)
            return self._token(TokenType.LESS, start)
        if char == ">":
            if self._match("="):
                return self._token(TokenType.GREATER_EQUAL, start)
            return self._token(TokenType.GREATER, start)
        if char == ".":
            if self._match("."):
                return self._token(TokenType.RANGE, start)
            self.diagnostics.error(
                Code.UNKNOWN_CHARACTER,
                "одиночная точка не является оператором",
                self._span(start),
                hint="диапазон в цикле for записывается как '..'",
            )
            return None

        self.diagnostics.error(
            Code.UNKNOWN_CHARACTER,
            f"недопустимый символ {char!r} в исходном тексте",
            self._span(start),
            hint="удалите символ или заключите его в строковый литерал",
        )
        return None

    # --- пропуск пробелов и комментариев ------------------------------------------

    def _skip_trivia(self) -> None:
        while not self._at_end():
            char = self._peek()
            if char in " \t\r\n":
                self._advance()
            elif char == "/" and self._peek(1) == "/":
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            elif char == "/" and self._peek(1) == "*":
                self._block_comment()
            else:
                return

    def _block_comment(self) -> None:
        start = self._position()
        self._advance()  # /
        self._advance()  # *
        depth = 1  # блочные комментарии могут быть вложенными
        while depth > 0:
            if self._at_end():
                self.diagnostics.error(
                    Code.UNTERMINATED_COMMENT,
                    "незакрытый блочный комментарий",
                    Span(start, self._position()),
                    hint="добавьте '*/' в конце комментария",
                )
                return
            if self._peek() == "/" and self._peek(1) == "*":
                self._advance()
                self._advance()
                depth += 1
            elif self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                depth -= 1
            else:
                self._advance()

    # --- разбор отдельных лексем ---------------------------------------------------

    def _number(self, start: Position) -> Token:
        while self._peek().isdigit():
            self._advance()
        # цифры, слипшиеся с буквами (например 12abc), — это опечатка
        if self._peek().isalpha() or self._peek() == "_":
            while self._peek().isalnum() or self._peek() == "_":
                self._advance()
            span = self._span(start)
            self.diagnostics.error(
                Code.UNKNOWN_CHARACTER,
                f"некорректный числовой литерал {self.text[start.offset:self.offset]!r}",
                span,
                hint="идентификатор не может начинаться с цифры",
            )
            return Token(TokenType.INT_LITERAL, self.text[start.offset : self.offset], span, 0)

        lexeme = self.text[start.offset : self.offset]
        value = int(lexeme)
        if value > INT_MAX:
            self.diagnostics.error(
                Code.INT_OVERFLOW,
                f"целочисленный литерал {lexeme} выходит за пределы int ({INT_MAX})",
                self._span(start),
                hint="Mini-PL использует 32-битные знаковые целые",
            )
            value = INT_MAX
        return self._token(TokenType.INT_LITERAL, start, value)

    def _identifier(self, start: Position) -> Token:
        while self._peek().isalnum() or self._peek() == "_":
            self._advance()
        lexeme = self.text[start.offset : self.offset]
        type_ = KEYWORDS.get(lexeme, TokenType.IDENTIFIER)
        if type_ is TokenType.BOOL_LITERAL:
            return self._token(type_, start, lexeme == "true")
        if type_ is TokenType.IDENTIFIER:
            return self._token(type_, start, lexeme)
        return self._token(type_, start)

    def _string(self, start: Position) -> Token:
        chars: List[str] = []
        while True:
            if self._at_end() or self._peek() == "\n":
                self.diagnostics.error(
                    Code.UNTERMINATED_STRING,
                    "незакрытый строковый литерал",
                    Span(start, self._position()),
                    hint='строка должна закрываться символом \'"\' на той же строке',
                )
                return Token(
                    TokenType.STRING_LITERAL,
                    self.text[start.offset : self.offset],
                    self._span(start),
                    "".join(chars),
                )
            char = self._advance()
            if char == '"':
                return self._token(TokenType.STRING_LITERAL, start, "".join(chars))
            if char == "\\":
                escape_start = Position(self.offset - 1, self.line, self.column - 1)
                if self._at_end():
                    continue
                escaped = self._advance()
                if escaped in ESCAPES:
                    chars.append(ESCAPES[escaped])
                else:
                    self.diagnostics.error(
                        Code.BAD_ESCAPE,
                        f"неизвестная escape-последовательность '\\{escaped}'",
                        Span(escape_start, self._position()),
                        hint=r"поддерживаются \n \t \r \0 \" \' \\",
                    )
                    chars.append(escaped)
            else:
                chars.append(char)


def tokenize(source: Source, diagnostics: Optional[DiagnosticBag] = None) -> List[Token]:
    return Lexer(source, diagnostics).tokenize()


##############################################################################
# ЧАСТЬ 7. АБСТРАКТНОЕ СИНТАКСИЧЕСКОЕ ДЕРЕВО
##############################################################################


class Node:
    """Базовый узел AST. Каждый узел знает свой диапазон в исходном тексте."""

    span: Span

    def children(self) -> List["Node"]:
        return []

    def label(self) -> str:
        return type(self).__name__


# --- выражения -------------------------------------------------------------------


@dataclass
class Expr(Node):
    span: Span
    # заполняется семантическим анализатором
    type: Optional[Type] = field(default=None, init=False, repr=False)


@dataclass
class IntLiteral(Expr):
    value: int = 0

    def label(self) -> str:
        return f"Int({self.value})"


@dataclass
class StringLiteral(Expr):
    value: str = ""

    def label(self) -> str:
        return f"String({self.value!r})"


@dataclass
class BoolLiteral(Expr):
    value: bool = False

    def label(self) -> str:
        return f"Bool({str(self.value).lower()})"


@dataclass
class VarRef(Expr):
    name: str = ""

    def label(self) -> str:
        return f"VarRef({self.name})"


@dataclass
class Unary(Expr):
    op: str = ""
    operand: Optional[Expr] = None

    def children(self) -> List[Node]:
        return [self.operand] if self.operand else []

    def label(self) -> str:
        return f"Unary({self.op})"


@dataclass
class Binary(Expr):
    op: str = ""
    left: Optional[Expr] = None
    right: Optional[Expr] = None

    def children(self) -> List[Node]:
        return [child for child in (self.left, self.right) if child]

    def label(self) -> str:
        return f"Binary({self.op})"


# --- операторы --------------------------------------------------------------------


@dataclass
class Stmt(Node):
    span: Span


@dataclass
class VarDecl(Stmt):
    name: str = ""
    declared_type: Optional[Type] = None
    init: Optional[Expr] = None
    name_span: Optional[Span] = None

    def children(self) -> List[Node]:
        return [self.init] if self.init else []

    def label(self) -> str:
        return f"VarDecl({self.name}: {self.declared_type})"


@dataclass
class Assign(Stmt):
    name: str = ""
    value: Optional[Expr] = None
    name_span: Optional[Span] = None

    def children(self) -> List[Node]:
        return [self.value] if self.value else []

    def label(self) -> str:
        return f"Assign({self.name})"


@dataclass
class Print(Stmt):
    value: Optional[Expr] = None

    def children(self) -> List[Node]:
        return [self.value] if self.value else []


@dataclass
class Read(Stmt):
    name: str = ""
    name_span: Optional[Span] = None

    def label(self) -> str:
        return f"Read({self.name})"


@dataclass
class Assert(Stmt):
    condition: Optional[Expr] = None

    def children(self) -> List[Node]:
        return [self.condition] if self.condition else []


@dataclass
class Block(Node):
    span: Span
    statements: List[Stmt] = field(default_factory=list)

    def children(self) -> List[Node]:
        return list(self.statements)


@dataclass
class For(Stmt):
    var_name: str = ""
    start: Optional[Expr] = None
    end: Optional[Expr] = None
    body: Optional[Block] = None
    name_span: Optional[Span] = None

    def children(self) -> List[Node]:
        return [node for node in (self.start, self.end, self.body) if node]

    def label(self) -> str:
        return f"For({self.var_name})"


@dataclass
class While(Stmt):
    condition: Optional[Expr] = None
    body: Optional[Block] = None

    def children(self) -> List[Node]:
        return [node for node in (self.condition, self.body) if node]


@dataclass
class If(Stmt):
    condition: Optional[Expr] = None
    then_body: Optional[Block] = None
    else_body: Optional[Block] = None

    def children(self) -> List[Node]:
        return [node for node in (self.condition, self.then_body, self.else_body) if node]


@dataclass
class Program(Node):
    span: Span
    block: Optional[Block] = None

    def children(self) -> List[Node]:
        return [self.block] if self.block else []


def dump(node: Node) -> str:
    """Печатает дерево в виде ASCII-схемы (используется флагом --ast)."""

    lines: List[str] = []

    def walk(current: Node, prefix: str, connector: str) -> None:
        type_note = ""
        if isinstance(current, Expr) and current.type is not None:
            type_note = f" : {current.type}"
        lines.append(f"{prefix}{connector}{current.label()}{type_note} @{current.span.start}")
        if connector:
            child_prefix = prefix + ("   " if connector.startswith("`") else "|  ")
        else:
            child_prefix = prefix
        children = current.children()
        for index, child in enumerate(children):
            walk(child, child_prefix, "`- " if index == len(children) - 1 else "|- ")

    walk(node, "", "")
    return "\n".join(lines)


##############################################################################
# ЧАСТЬ 8. СИНТАКСИЧЕСКИЙ АНАЛИЗАТОР (РЕКУРСИВНЫЙ СПУСК)
##############################################################################


# Токены, с которых может начинаться оператор — точки синхронизации.
STATEMENT_STARTERS: Set[TokenType] = {
    TokenType.VAR,
    TokenType.FOR,
    TokenType.WHILE,
    TokenType.IF,
    TokenType.READ,
    TokenType.PRINT,
    TokenType.ASSERT,
    TokenType.IDENTIFIER,
}

BLOCK_TERMINATORS: Set[TokenType] = {TokenType.END, TokenType.ELSE, TokenType.EOF}

# Уровни приоритета бинарных операторов: от самого слабого к самому сильному.
PRECEDENCE_LEVELS = [
    {TokenType.OR: "|"},
    {TokenType.AND: "&"},
    {
        TokenType.EQUAL: "=",
        TokenType.NOT_EQUAL: "<>",
        TokenType.LESS: "<",
        TokenType.LESS_EQUAL: "<=",
        TokenType.GREATER: ">",
        TokenType.GREATER_EQUAL: ">=",
    },
    {TokenType.PLUS: "+", TokenType.MINUS: "-"},
    {TokenType.STAR: "*", TokenType.SLASH: "/", TokenType.PERCENT: "%"},
]

TYPE_BY_TOKEN = {
    TokenType.INT: Type.INT,
    TokenType.STRING: Type.STRING,
    TokenType.BOOL: Type.BOOL,
}


class Parser:
    def __init__(
        self,
        tokens: List[Token],
        source: Source,
        diagnostics: Optional[DiagnosticBag] = None,
    ) -> None:
        self.tokens = tokens
        self.source = source
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag(source)
        self.index = 0

    # --- навигация по потоку токенов ----------------------------------------------

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def _peek(self, ahead: int = 1) -> Token:
        index = min(self.index + ahead, len(self.tokens) - 1)
        return self.tokens[index]

    def _advance(self) -> Token:
        token = self.current
        if token.type is not TokenType.EOF:
            self.index += 1
        return token

    def _check(self, *types: TokenType) -> bool:
        return self.current.type in types

    def _match(self, *types: TokenType) -> Optional[Token]:
        if self._check(*types):
            return self._advance()
        return None

    def _error(self, code: str, message: str, token: Token, hint: Optional[str] = None) -> CompileError:
        diagnostic = Diagnostic(Severity.ERROR, code, message, token.span, hint)
        self.diagnostics.add(diagnostic)
        return CompileError(diagnostic)

    def _expect(self, type_: TokenType, code: str, message: str, hint: Optional[str] = None) -> Token:
        if self._check(type_):
            return self._advance()
        raise self._error(code, message, self.current, hint)

    def _synchronize(self) -> None:
        """Паника: пропускаем токены до конца текущего оператора."""

        while not self._check(TokenType.EOF):
            if self._match(TokenType.SEMICOLON):
                return
            if self.current.type in STATEMENT_STARTERS or self.current.type in {
                TokenType.END,
                TokenType.ELSE,
            }:
                return
            self._advance()

    # --- разбор программы ----------------------------------------------------------

    def parse(self) -> Program:
        start = self.current.span.start
        block = self._block(BLOCK_TERMINATORS)
        if not self._check(TokenType.EOF):
            token = self.current
            self.diagnostics.error(
                Code.UNEXPECTED_TOKEN,
                f"неожиданный токен {token.describe()} на верхнем уровне программы",
                token.span,
                hint="возможно, лишнее 'end' или незакрытый блок выше",
            )
            while not self._check(TokenType.EOF):
                self._advance()
        end = self.current.span.end
        return Program(Span(start, end), block)

    def parse_expression(self) -> Expr:
        """Разбирает одно выражение (используется отладчиком для `print`)."""

        expr = self._expression()
        if not self._check(TokenType.EOF):
            raise self._error(
                Code.UNEXPECTED_TOKEN,
                f"лишний текст после выражения: {self.current.describe()}",
                self.current,
            )
        return expr

    def _block(self, terminators: Set[TokenType]) -> Block:
        start = self.current.span.start
        statements: List[Stmt] = []
        while not self._check(*terminators) and not self._check(TokenType.EOF):
            before = self.index
            try:
                statement = self._statement()
                self._expect_semicolon(statement)
                statements.append(statement)
            except CompileError:
                self._synchronize()
            # страховка от зацикливания, если ни одна ветка не сдвинула позицию
            if self.index == before:
                self._advance()
        end = self.tokens[max(0, self.index - 1)].span.end if self.index else start
        return Block(Span(start, end), statements)

    def _expect_semicolon(self, statement: Stmt) -> None:
        if self._match(TokenType.SEMICOLON):
            return
        previous = self.tokens[max(0, self.index - 1)]
        end = previous.span.end
        span = Span(end, Position(end.offset + 1, end.line, end.column + 1))
        raise self._error(
            Code.MISSING_SEMICOLON,
            "пропущена ';' в конце оператора",
            Token(TokenType.SEMICOLON, ";", span),
            hint="каждый оператор Mini-PL завершается точкой с запятой",
        )

    # --- операторы ------------------------------------------------------------------

    def _statement(self) -> Stmt:
        token = self.current
        if token.type is TokenType.VAR:
            return self._var_declaration()
        if token.type is TokenType.FOR:
            return self._for_statement()
        if token.type is TokenType.WHILE:
            return self._while_statement()
        if token.type is TokenType.IF:
            return self._if_statement()
        if token.type is TokenType.READ:
            return self._read_statement()
        if token.type is TokenType.PRINT:
            return self._print_statement()
        if token.type is TokenType.ASSERT:
            return self._assert_statement()
        if token.type is TokenType.IDENTIFIER:
            return self._assignment()
        raise self._error(
            Code.UNEXPECTED_TOKEN,
            f"оператор не может начинаться с {token.describe()}",
            token,
            hint="ожидались 'var', 'for', 'while', 'if', 'read', 'print', 'assert' или присваивание",
        )

    def _var_declaration(self) -> VarDecl:
        keyword = self._advance()
        name_token = self._expect(
            TokenType.IDENTIFIER,
            Code.EXPECTED_IDENTIFIER,
            f"после 'var' ожидалось имя переменной, найдено {self.current.describe()}",
            hint="пример: var x : int := 0;",
        )
        self._expect(
            TokenType.COLON,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось ':' после имени переменной, найдено {self.current.describe()}",
            hint="тип переменной указывается после двоеточия",
        )
        type_token = self.current
        if type_token.type not in TYPE_TOKENS:
            raise self._error(
                Code.EXPECTED_TYPE,
                f"ожидался тип (int, string, bool), найдено {type_token.describe()}",
                type_token,
            )
        self._advance()
        declared = TYPE_BY_TOKEN[type_token.type]

        init: Optional[Expr] = None
        if self._match(TokenType.ASSIGN):
            init = self._expression()
        end = self.tokens[self.index - 1].span.end
        return VarDecl(
            Span(keyword.span.start, end),
            name_token.value,
            declared,
            init,
            name_token.span,
        )

    def _assignment(self) -> Assign:
        name_token = self._advance()
        if not self._check(TokenType.ASSIGN):
            hint = None
            if self._check(TokenType.EQUAL):
                hint = "для присваивания используется ':=', а '=' сравнивает значения"
            raise self._error(
                Code.UNEXPECTED_TOKEN,
                f"ожидалось ':=' после имени переменной, найдено {self.current.describe()}",
                self.current,
                hint=hint,
            )
        self._advance()
        value = self._expression()
        return Assign(
            Span(name_token.span.start, value.span.end),
            name_token.value,
            value,
            name_token.span,
        )

    def _for_statement(self) -> For:
        keyword = self._advance()
        name_token = self._expect(
            TokenType.IDENTIFIER,
            Code.EXPECTED_IDENTIFIER,
            f"после 'for' ожидалось имя переменной цикла, найдено {self.current.describe()}",
            hint="пример: for i in 1..10 do ... end for;",
        )
        self._expect(
            TokenType.IN,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'in' после переменной цикла, найдено {self.current.describe()}",
        )
        start_expr = self._expression()
        self._expect(
            TokenType.RANGE,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось '..' между границами диапазона, найдено {self.current.describe()}",
        )
        end_expr = self._expression()
        self._expect(
            TokenType.DO,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'do' перед телом цикла, найдено {self.current.describe()}",
        )
        body = self._block(BLOCK_TERMINATORS)
        self._expect(
            TokenType.END,
            Code.UNEXPECTED_TOKEN,
            f"незакрытый цикл for: ожидалось 'end for', найдено {self.current.describe()}",
        )
        end_token = self._expect(
            TokenType.FOR,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'for' после 'end', найдено {self.current.describe()}",
        )
        self._warn_if_empty(body, "for")
        return For(
            Span(keyword.span.start, end_token.span.end),
            name_token.value,
            start_expr,
            end_expr,
            body,
            name_token.span,
        )

    def _while_statement(self) -> While:
        keyword = self._advance()
        condition = self._expression()
        self._expect(
            TokenType.DO,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'do' перед телом while, найдено {self.current.describe()}",
        )
        body = self._block(BLOCK_TERMINATORS)
        self._expect(
            TokenType.END,
            Code.UNEXPECTED_TOKEN,
            f"незакрытый цикл while: ожидалось 'end while', найдено {self.current.describe()}",
        )
        end_token = self._expect(
            TokenType.WHILE,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'while' после 'end', найдено {self.current.describe()}",
        )
        self._warn_if_empty(body, "while")
        return While(Span(keyword.span.start, end_token.span.end), condition, body)

    def _if_statement(self) -> If:
        keyword = self._advance()
        condition = self._expression()
        self._expect(
            TokenType.DO,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'do' после условия if, найдено {self.current.describe()}",
        )
        then_body = self._block(BLOCK_TERMINATORS)
        else_body: Optional[Block] = None
        if self._match(TokenType.ELSE):
            else_body = self._block(BLOCK_TERMINATORS)
        self._expect(
            TokenType.END,
            Code.UNEXPECTED_TOKEN,
            f"незакрытый if: ожидалось 'end if', найдено {self.current.describe()}",
        )
        end_token = self._expect(
            TokenType.IF,
            Code.UNEXPECTED_TOKEN,
            f"ожидалось 'if' после 'end', найдено {self.current.describe()}",
        )
        self._warn_if_empty(then_body, "if")
        return If(Span(keyword.span.start, end_token.span.end), condition, then_body, else_body)

    def _read_statement(self) -> Read:
        keyword = self._advance()
        name_token = self._expect(
            TokenType.IDENTIFIER,
            Code.EXPECTED_IDENTIFIER,
            f"после 'read' ожидалось имя переменной, найдено {self.current.describe()}",
        )
        return Read(
            Span(keyword.span.start, name_token.span.end), name_token.value, name_token.span
        )

    def _print_statement(self) -> Print:
        keyword = self._advance()
        value = self._expression()
        return Print(Span(keyword.span.start, value.span.end), value)

    def _assert_statement(self) -> Assert:
        keyword = self._advance()
        self._expect(
            TokenType.LPAREN,
            Code.UNEXPECTED_TOKEN,
            f"после 'assert' ожидалась '(', найдено {self.current.describe()}",
            hint="условие assert записывается в скобках: assert (x > 0);",
        )
        condition = self._expression()
        closing = self._expect(
            TokenType.RPAREN,
            Code.UNCLOSED_PAREN,
            f"незакрытая скобка в assert: ожидалась ')', найдено {self.current.describe()}",
        )
        return Assert(Span(keyword.span.start, closing.span.end), condition)

    def _warn_if_empty(self, block: Block, keyword: str) -> None:
        # если разбор уже сбоил, пустой блок — скорее всего следствие
        # восстановления после ошибки, а не намерение автора программы
        if not block.statements and not self.diagnostics.has_errors():
            self.diagnostics.warning(
                Code.EMPTY_BLOCK,
                f"тело '{keyword}' пустое",
                block.span,
                hint="блок без операторов не выполняет никакой работы",
            )

    # --- выражения ------------------------------------------------------------------

    def _expression(self, level: int = 0) -> Expr:
        if level >= len(PRECEDENCE_LEVELS):
            return self._unary()
        operators = PRECEDENCE_LEVELS[level]
        left = self._expression(level + 1)
        while self.current.type in operators:
            op_token = self._advance()
            right = self._expression(level + 1)
            node = Binary(
                Span.merge(left.span, right.span),
                operators[op_token.type],
                left,
                right,
            )
            left = node
        return left

    def _unary(self) -> Expr:
        token = self.current
        if token.type in (TokenType.NOT, TokenType.MINUS, TokenType.PLUS):
            self._advance()
            operand = self._unary()
            op = {"!": "!", "-": "-", "+": "+"}[token.lexeme]
            return Unary(Span.merge(token.span, operand.span), op, operand)
        return self._primary()

    def _primary(self) -> Expr:
        token = self.current
        if token.type is TokenType.INT_LITERAL:
            self._advance()
            return IntLiteral(token.span, token.value)
        if token.type is TokenType.STRING_LITERAL:
            self._advance()
            return StringLiteral(token.span, token.value)
        if token.type is TokenType.BOOL_LITERAL:
            self._advance()
            return BoolLiteral(token.span, bool(token.value))
        if token.type is TokenType.IDENTIFIER:
            self._advance()
            return VarRef(token.span, token.value)
        if token.type is TokenType.LPAREN:
            self._advance()
            inner = self._expression()
            closing = self._expect(
                TokenType.RPAREN,
                Code.UNCLOSED_PAREN,
                f"незакрытая скобка: ожидалась ')', найдено {self.current.describe()}",
                hint="проверьте баланс круглых скобок в выражении",
            )
            inner.span = Span(token.span.start, closing.span.end)
            return inner
        raise self._error(
            Code.EXPECTED_EXPRESSION,
            f"ожидалось выражение, найдено {token.describe()}",
            token,
            hint="выражение — это литерал, переменная, унарная операция или скобки",
        )


def parse(tokens: List[Token], source: Source, diagnostics: Optional[DiagnosticBag] = None) -> Program:
    return Parser(tokens, source, diagnostics).parse()


##############################################################################
# ЧАСТЬ 9. ТАБЛИЦА СИМВОЛОВ
##############################################################################


@dataclass
class Symbol:
    name: str
    type: Type
    slot: int
    declared_at: Optional[Span] = None
    # переменная цикла for неизменяема внутри своего тела
    locked: bool = False
    initialized: bool = False
    used: bool = False
    assignments: int = 0


class SymbolTable:
    """Плоская таблица имён: в Mini-PL все переменные глобальные."""

    def __init__(self) -> None:
        self._symbols: Dict[str, Symbol] = {}
        self._order: List[Symbol] = []

    def declare(self, name: str, type_: Type, span: Optional[Span] = None) -> Symbol:
        symbol = Symbol(name, type_, len(self._order), span)
        self._symbols[name] = symbol
        self._order.append(symbol)
        return symbol

    def lookup(self, name: str) -> Optional[Symbol]:
        return self._symbols.get(name)

    def is_declared(self, name: str) -> bool:
        return name in self._symbols

    def similar_names(self, name: str, limit: int = 3) -> List[str]:
        """Подбирает похожие имена для подсказки «возможно, вы имели в виду …»."""


        return difflib.get_close_matches(name, list(self._symbols), n=limit, cutoff=0.6)

    @property
    def symbols(self) -> List[Symbol]:
        return list(self._order)

    @property
    def slot_count(self) -> int:
        return len(self._order)

    def names_by_slot(self) -> List[str]:
        return [symbol.name for symbol in self._order]

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self):
        return iter(self._order)


##############################################################################
# ЧАСТЬ 10. СЕМАНТИЧЕСКИЙ АНАЛИЗ И ПРОВЕРКА ТИПОВ
##############################################################################


class SemanticAnalyzer:
    def __init__(self, source: Source, diagnostics: Optional[DiagnosticBag] = None) -> None:
        self.source = source
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticBag(source)
        self.symbols = SymbolTable()

    # --- точка входа ----------------------------------------------------------------

    def analyze(self, program: Program) -> SymbolTable:
        if program.block is not None:
            self._block(program.block)
        self._report_unused()
        return self.symbols

    def check_expression(self, expr: Expr) -> Type:
        """Проверяет отдельное выражение в уже собранной таблице символов."""

        return self._expression(expr)

    def _report_unused(self) -> None:
        for symbol in self.symbols:
            if not symbol.used and symbol.declared_at is not None:
                self.diagnostics.warning(
                    Code.UNUSED_VARIABLE,
                    f"переменная '{symbol.name}' объявлена, но нигде не используется",
                    symbol.declared_at,
                    hint="удалите объявление или используйте переменную",
                )

    # --- операторы --------------------------------------------------------------------

    def _block(self, block: Block) -> None:
        for statement in block.statements:
            self._statement(statement)

    def _statement(self, node: Stmt) -> None:
        handler = {
            VarDecl: self._var_decl,
            Assign: self._assign,
            Print: self._print,
            Read: self._read,
            Assert: self._assert,
            For: self._for,
            While: self._while,
            If: self._if,
        }.get(type(node))
        if handler is not None:
            handler(node)

    def _var_decl(self, node: VarDecl) -> None:
        existing = self.symbols.lookup(node.name)
        if existing is not None:
            self.diagnostics.error(
                Code.REDECLARED_VARIABLE,
                f"переменная '{node.name}' уже объявлена",
                node.name_span or node.span,
                hint="каждое имя в Mini-PL объявляется один раз",
                notes=[
                    f"предыдущее объявление: {self.source.name}:{existing.declared_at.start}"
                ]
                if existing.declared_at
                else [],
            )
            # продолжаем анализ инициализатора, чтобы найти ошибки и в нём
            if node.init is not None:
                self._expression(node.init)
            return

        if node.init is not None:
            init_type = self._expression(node.init)
            if init_type is not Type.ERROR and init_type is not node.declared_type:
                self.diagnostics.error(
                    Code.TYPE_MISMATCH,
                    f"нельзя инициализировать переменную типа {node.declared_type} "
                    f"выражением типа {init_type}",
                    node.init.span,
                    hint=f"ожидалось выражение типа {node.declared_type}",
                )
        symbol = self.symbols.declare(node.name, node.declared_type, node.name_span or node.span)
        symbol.initialized = True
        if node.init is not None:
            symbol.assignments += 1

    def _assign(self, node: Assign) -> None:
        symbol = self._resolve(node.name, node.name_span or node.span)
        value_type = self._expression(node.value) if node.value else Type.ERROR
        if symbol is None:
            return
        if symbol.locked:
            self.diagnostics.error(
                Code.ASSIGN_TO_LOOP_VARIABLE,
                f"нельзя изменять переменную цикла '{symbol.name}' внутри тела цикла",
                node.name_span or node.span,
                hint="счётчиком цикла управляет сам оператор for",
            )
            return
        symbol.assignments += 1
        if value_type is not Type.ERROR and value_type is not symbol.type:
            self.diagnostics.error(
                Code.TYPE_MISMATCH,
                f"нельзя присвоить значение типа {value_type} переменной '{symbol.name}' "
                f"типа {symbol.type}",
                node.span,
                hint=f"приведите выражение к типу {symbol.type}",
            )

    def _print(self, node: Print) -> None:
        if node.value is not None:
            self._expression(node.value)

    def _read(self, node: Read) -> None:
        symbol = self._resolve(node.name, node.name_span or node.span)
        if symbol is None:
            return
        if symbol.locked:
            self.diagnostics.error(
                Code.ASSIGN_TO_LOOP_VARIABLE,
                f"нельзя читать значение в переменную цикла '{symbol.name}'",
                node.name_span or node.span,
            )
            return
        if symbol.type not in (Type.INT, Type.STRING):
            self.diagnostics.error(
                Code.READ_BAD_TYPE,
                f"'read' поддерживает только int и string, а '{symbol.name}' имеет тип {symbol.type}",
                node.name_span or node.span,
                hint="прочитайте значение во временную переменную нужного типа",
            )
        symbol.assignments += 1

    def _assert(self, node: Assert) -> None:
        if node.condition is None:
            return
        condition_type = self._expression(node.condition)
        if condition_type not in (Type.BOOL, Type.ERROR):
            self.diagnostics.error(
                Code.ASSERT_NOT_BOOL,
                f"условие assert должно быть типа bool, а не {condition_type}",
                node.condition.span,
                hint="например: assert (x > 0);",
            )

    def _for(self, node: For) -> None:
        symbol = self.symbols.lookup(node.var_name)
        if symbol is None:
            # расширение: переменная цикла объявляется неявно как int
            symbol = self.symbols.declare(node.var_name, Type.INT, node.name_span or node.span)
            symbol.initialized = True
        elif symbol.type is not Type.INT:
            self.diagnostics.error(
                Code.NON_INT_LOOP_RANGE,
                f"переменная цикла '{node.var_name}' должна иметь тип int, а не {symbol.type}",
                node.name_span or node.span,
            )
        if symbol.locked:
            self.diagnostics.error(
                Code.ASSIGN_TO_LOOP_VARIABLE,
                f"переменная '{symbol.name}' уже используется как счётчик внешнего цикла",
                node.name_span or node.span,
                hint="возьмите другое имя для вложенного цикла",
            )
        symbol.used = True
        symbol.assignments += 1

        for bound, label in ((node.start, "нижняя"), (node.end, "верхняя")):
            if bound is None:
                continue
            bound_type = self._expression(bound)
            if bound_type not in (Type.INT, Type.ERROR):
                self.diagnostics.error(
                    Code.NON_INT_LOOP_RANGE,
                    f"{label} граница диапазона должна быть int, а не {bound_type}",
                    bound.span,
                )

        was_locked = symbol.locked
        symbol.locked = True
        if node.body is not None:
            self._block(node.body)
        symbol.locked = was_locked

    def _while(self, node: While) -> None:
        if node.condition is not None:
            condition_type = self._expression(node.condition)
            if condition_type not in (Type.BOOL, Type.ERROR):
                self.diagnostics.error(
                    Code.TYPE_MISMATCH,
                    f"условие while должно быть типа bool, а не {condition_type}",
                    node.condition.span,
                )
        if node.body is not None:
            self._block(node.body)

    def _if(self, node: If) -> None:
        if node.condition is not None:
            condition_type = self._expression(node.condition)
            if condition_type not in (Type.BOOL, Type.ERROR):
                self.diagnostics.error(
                    Code.TYPE_MISMATCH,
                    f"условие if должно быть типа bool, а не {condition_type}",
                    node.condition.span,
                )
        if node.then_body is not None:
            self._block(node.then_body)
        if node.else_body is not None:
            self._block(node.else_body)

    # --- выражения ----------------------------------------------------------------------

    def _resolve(self, name: str, span) -> Optional[Symbol]:
        symbol = self.symbols.lookup(name)
        if symbol is None:
            similar = self.symbols.similar_names(name)
            hint = None
            if similar:
                hint = "возможно, имелось в виду: " + ", ".join(f"'{s}'" for s in similar)
            self.diagnostics.error(
                Code.UNDECLARED_VARIABLE,
                f"переменная '{name}' не объявлена",
                span,
                hint=hint or "объявите переменную: var имя : тип;",
            )
        return symbol

    def _expression(self, node: Expr) -> Type:
        if isinstance(node, IntLiteral):
            node.type = Type.INT
        elif isinstance(node, StringLiteral):
            node.type = Type.STRING
        elif isinstance(node, BoolLiteral):
            node.type = Type.BOOL
        elif isinstance(node, VarRef):
            symbol = self._resolve(node.name, node.span)
            if symbol is None:
                node.type = Type.ERROR
            else:
                symbol.used = True
                node.type = symbol.type
        elif isinstance(node, Unary):
            node.type = self._unary(node)
        elif isinstance(node, Binary):
            node.type = self._binary(node)
        else:
            node.type = Type.ERROR
        return node.type

    def _unary(self, node: Unary) -> Type:
        operand_type = self._expression(node.operand) if node.operand else Type.ERROR
        if operand_type is Type.ERROR:
            return Type.ERROR
        result = UNARY_RULES.get((node.op, operand_type))
        if result is None:
            self.diagnostics.error(
                Code.BAD_OPERAND_TYPE,
                f"унарный оператор '{node.op}' неприменим к типу {operand_type}",
                node.span,
                hint="'!' работает с bool, унарный '-' — с int",
            )
            return Type.ERROR
        return result

    def _binary(self, node: Binary) -> Type:
        left_type = self._expression(node.left) if node.left else Type.ERROR
        right_type = self._expression(node.right) if node.right else Type.ERROR
        if left_type is Type.ERROR or right_type is Type.ERROR:
            return Type.ERROR
        if left_type is not right_type:
            self.diagnostics.error(
                Code.TYPE_MISMATCH,
                f"оператор '{node.op}' требует операнды одного типа, "
                f"получены {left_type} и {right_type}",
                node.span,
                hint="Mini-PL не выполняет неявных приведений типов",
            )
            return Type.ERROR
        result = BINARY_RULES.get((node.op, left_type))
        if result is None:
            self.diagnostics.error(
                Code.BAD_OPERAND_TYPE,
                f"оператор '{node.op}' неприменим к операндам типа {left_type}",
                node.span,
            )
            return Type.ERROR
        return result


def analyze(
    program: Program, source: Source, diagnostics: Optional[DiagnosticBag] = None
) -> SymbolTable:
    return SemanticAnalyzer(source, diagnostics).analyze(program)


##############################################################################
# ЧАСТЬ 11. БАЙТКОД: НАБОР КОМАНД, CHUNK, ДИЗАССЕМБЛЕР
##############################################################################


class Op(IntEnum):
    """Набор команд Mini-PL VM (стековая машина)."""

    CONST = 1      # положить константу с индексом arg
    LOAD = 2       # положить значение слота arg
    STORE = 3      # снять вершину и записать в слот arg
    POP = 4
    DUP = 5

    ADD = 10
    SUB = 11
    MUL = 12
    DIV = 13
    MOD = 14
    NEG = 15

    LT = 20
    LE = 21
    GT = 22
    GE = 23
    EQ = 24
    NE = 25
    NOT = 28

    JMP = 30       # безусловный переход на arg
    JMPF = 31      # переход, если вершина стека ложна (вершина снимается)
    JMPT = 32

    PRINT = 40
    READ = 41      # прочитать значение в слот arg
    ASSERT = 42
    HALT = 43


# Команды, у которых есть операнд-аргумент.
OPS_WITH_ARG = {Op.CONST, Op.LOAD, Op.STORE, Op.READ, Op.JMP, Op.JMPF, Op.JMPT}
JUMP_OPS = {Op.JMP, Op.JMPF, Op.JMPT}


@dataclass
class Instruction:
    op: Op
    arg: Optional[int] = None
    span: Optional[Span] = None
    note: str = ""

    @property
    def line(self) -> int:
        return self.span.start.line if self.span else 0


@dataclass
class Chunk:
    """Скомпилированная программа: код, константы и отладочная информация."""

    source_name: str = "<stdin>"
    code: List[Instruction] = field(default_factory=list)
    constants: List[Any] = field(default_factory=list)
    slot_names: List[str] = field(default_factory=list)
    slot_types: List[Type] = field(default_factory=list)

    # --- сборка ---------------------------------------------------------------------

    def add_constant(self, value: Any) -> int:
        for index, existing in enumerate(self.constants):
            if type(existing) is type(value) and existing == value:
                return index
        self.constants.append(value)
        return len(self.constants) - 1

    def emit(self, op: Op, arg: Optional[int] = None, span: Optional[Span] = None, note: str = "") -> int:
        self.code.append(Instruction(op, arg, span, note))
        return len(self.code) - 1

    def patch(self, index: int, target: int) -> None:
        self.code[index].arg = target

    def __len__(self) -> int:
        return len(self.code)

    # --- дизассемблер ----------------------------------------------------------------

    def format_instruction(self, index: int) -> str:
        instruction = self.code[index]
        arg = "" if instruction.arg is None else str(instruction.arg)
        comment = ""
        if instruction.op is Op.CONST and instruction.arg is not None:
            comment = f"// {repr_value(self.constants[instruction.arg])}"
        elif instruction.op in (Op.LOAD, Op.STORE, Op.READ) and instruction.arg is not None:
            name = self.slot_names[instruction.arg]
            type_ = self.slot_types[instruction.arg]
            comment = f"// {name} : {type_}"
        elif instruction.op in JUMP_OPS:
            comment = f"// -> {instruction.arg}"
        if instruction.note and not comment:
            comment = f"// {instruction.note}"
        line = f"L{instruction.line}" if instruction.line else "-"
        return f"{index:04d}  {line:<6} {instruction.op.name:<7} {arg:<5} {comment}"

    def disassemble(self) -> str:
        header = [
            f"; программа: {self.source_name}",
            f"; инструкций: {len(self.code)}, констант: {len(self.constants)}, "
            f"слотов: {len(self.slot_names)}",
        ]
        if self.slot_names:
            header.append(
                "; переменные: "
                + ", ".join(
                    f"[{index}] {name}:{self.slot_types[index]}"
                    for index, name in enumerate(self.slot_names)
                )
            )
        body = [self.format_instruction(index) for index in range(len(self.code))]
        return "\n".join(header + body)

    # --- сериализация ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "source_name": self.source_name,
            "constants": self.constants,
            "slot_names": self.slot_names,
            "slot_types": [type_.value for type_ in self.slot_types],
            "code": [
                {
                    "op": int(instruction.op),
                    "arg": instruction.arg,
                    "line": instruction.line,
                    "column": instruction.span.start.column if instruction.span else 0,
                    "note": instruction.note,
                }
                for instruction in self.code
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Chunk":
        chunk = cls(source_name=data.get("source_name", "<bytecode>"))
        chunk.constants = list(data.get("constants", []))
        chunk.slot_names = list(data.get("slot_names", []))
        chunk.slot_types = [Type(value) for value in data.get("slot_types", [])]
        for item in data.get("code", []):
            line = int(item.get("line", 0) or 0)
            column = int(item.get("column", 0) or 0)
            span = None
            if line:
                position = Position(0, line, max(1, column))
                span = Span(position, position)
            chunk.code.append(
                Instruction(Op(item["op"]), item.get("arg"), span, item.get("note", ""))
            )
        return chunk

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> "Chunk":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


##############################################################################
# ЧАСТЬ 12. ГЕНЕРАТОР КОДА
##############################################################################


BINARY_OPS: Dict[str, Op] = {
    "+": Op.ADD,
    "-": Op.SUB,
    "*": Op.MUL,
    "/": Op.DIV,
    "%": Op.MOD,
    "<": Op.LT,
    "<=": Op.LE,
    ">": Op.GT,
    ">=": Op.GE,
    "=": Op.EQ,
    "<>": Op.NE,
}


class CodeGenerator:
    def __init__(self, source: Source, symbols: SymbolTable) -> None:
        self.source = source
        self.symbols = symbols
        self.chunk = Chunk(source_name=source.name)
        for symbol in symbols:
            self.chunk.slot_names.append(symbol.name)
            self.chunk.slot_types.append(symbol.type)
        self._hidden_slots: Dict[str, int] = {}

    # --- вспомогательное ---------------------------------------------------------------

    def _slot(self, name: str) -> int:
        symbol = self.symbols.lookup(name)
        if symbol is None:  # не должно случаться: семантика уже отработала
            raise KeyError(name)
        return symbol.slot

    def _hidden_slot(self, key: str) -> int:
        if key not in self._hidden_slots:
            self.chunk.slot_names.append(key)
            self.chunk.slot_types.append(Type.INT)
            self._hidden_slots[key] = len(self.chunk.slot_names) - 1
        return self._hidden_slots[key]

    def _const(self, value, span, note: str = "") -> None:
        self.chunk.emit(Op.CONST, self.chunk.add_constant(value), span, note)

    # --- точка входа ---------------------------------------------------------------------

    def generate(self, program: Program) -> Chunk:
        if program.block is not None:
            self._block(program.block)
        end_span = Span(program.span.end, program.span.end)
        self.chunk.emit(Op.HALT, None, end_span, "конец программы")
        return self.chunk

    def generate_expression(self, expr: Expr) -> Chunk:
        """Компилирует одиночное выражение — так отладчик считает `print i * 2`."""

        self._expression(expr)
        self.chunk.emit(Op.HALT, None, expr.span, "конец выражения")
        return self.chunk

    def _block(self, block: Block) -> None:
        for statement in block.statements:
            self._statement(statement)

    def _statement(self, node: Stmt) -> None:
        if isinstance(node, VarDecl):
            self._var_decl(node)
        elif isinstance(node, Assign):
            self._assign(node)
        elif isinstance(node, Print):
            self._print(node)
        elif isinstance(node, Read):
            self._read(node)
        elif isinstance(node, Assert):
            self._assert(node)
        elif isinstance(node, For):
            self._for(node)
        elif isinstance(node, While):
            self._while(node)
        elif isinstance(node, If):
            self._if(node)

    def _var_decl(self, node: VarDecl) -> None:
        if node.init is not None:
            self._expression(node.init)
        else:
            # объявление без инициализатора даёт значение по умолчанию;
            # при повторном входе (тело цикла) переменная сбрасывается
            self._const(default_value(node.declared_type), node.span, "значение по умолчанию")
        self.chunk.emit(Op.STORE, self._slot(node.name), node.span, f"{node.name} :=")

    def _assign(self, node: Assign) -> None:
        self._expression(node.value)
        self.chunk.emit(Op.STORE, self._slot(node.name), node.span, f"{node.name} :=")

    def _print(self, node: Print) -> None:
        self._expression(node.value)
        self.chunk.emit(Op.PRINT, None, node.span, "print")

    def _read(self, node: Read) -> None:
        self.chunk.emit(Op.READ, self._slot(node.name), node.span, f"read {node.name}")

    def _assert(self, node: Assert) -> None:
        self._expression(node.condition)
        self.chunk.emit(Op.ASSERT, None, node.span, "assert")

    def _for(self, node: For) -> None:
        counter = self._slot(node.var_name)
        limit = self._hidden_slot(f"$limit#{node.span.start.line}:{node.span.start.column}")

        self._expression(node.start)
        self.chunk.emit(Op.STORE, counter, node.span, f"{node.var_name} := начало диапазона")
        self._expression(node.end)
        self.chunk.emit(Op.STORE, limit, node.span, "верхняя граница диапазона")

        condition_at = len(self.chunk)
        self.chunk.emit(Op.LOAD, counter, node.span, node.var_name)
        self.chunk.emit(Op.LOAD, limit, node.span, "предел")
        self.chunk.emit(Op.LE, None, node.span, "продолжать цикл?")
        exit_jump = self.chunk.emit(Op.JMPF, None, node.span, "выход из for")

        if node.body is not None:
            self._block(node.body)

        # шаг цикла: counter := counter + 1
        self.chunk.emit(Op.LOAD, counter, node.span, node.var_name)
        self._const(1, node.span, "шаг цикла")
        self.chunk.emit(Op.ADD, None, node.span)
        self.chunk.emit(Op.STORE, counter, node.span, f"{node.var_name} := {node.var_name} + 1")
        self.chunk.emit(Op.JMP, condition_at, node.span, "новая итерация")
        self.chunk.patch(exit_jump, len(self.chunk))

    def _while(self, node: While) -> None:
        condition_at = len(self.chunk)
        self._expression(node.condition)
        exit_jump = self.chunk.emit(Op.JMPF, None, node.span, "выход из while")
        if node.body is not None:
            self._block(node.body)
        self.chunk.emit(Op.JMP, condition_at, node.span, "новая итерация")
        self.chunk.patch(exit_jump, len(self.chunk))

    def _if(self, node: If) -> None:
        self._expression(node.condition)
        else_jump = self.chunk.emit(Op.JMPF, None, node.span, "ветка else")
        if node.then_body is not None:
            self._block(node.then_body)
        if node.else_body is not None:
            end_jump = self.chunk.emit(Op.JMP, None, node.span, "конец if")
            self.chunk.patch(else_jump, len(self.chunk))
            self._block(node.else_body)
            self.chunk.patch(end_jump, len(self.chunk))
        else:
            self.chunk.patch(else_jump, len(self.chunk))

    # --- выражения --------------------------------------------------------------------------

    def _expression(self, node: Optional[Expr]) -> None:
        if node is None:
            return
        if isinstance(node, (IntLiteral, StringLiteral, BoolLiteral)):
            self._const(node.value, node.span)
        elif isinstance(node, VarRef):
            self.chunk.emit(Op.LOAD, self._slot(node.name), node.span, node.name)
        elif isinstance(node, Unary):
            self._unary(node)
        elif isinstance(node, Binary):
            self._binary(node)

    def _unary(self, node: Unary) -> None:
        self._expression(node.operand)
        if node.op == "-":
            self.chunk.emit(Op.NEG, None, node.span)
        elif node.op == "!":
            self.chunk.emit(Op.NOT, None, node.span)
        # унарный '+' не меняет значение

    def _binary(self, node: Binary) -> None:
        if node.op in ("&", "|"):
            self._short_circuit(node)
            return
        self._expression(node.left)
        self._expression(node.right)
        self.chunk.emit(BINARY_OPS[node.op], None, node.span, f"операция '{node.op}'")

    def _short_circuit(self, node: Binary) -> None:
        self._expression(node.left)
        self.chunk.emit(Op.DUP, None, node.span, "сохранить левый операнд")
        jump_op = Op.JMPF if node.op == "&" else Op.JMPT
        skip = self.chunk.emit(jump_op, None, node.span, f"сокращённое вычисление '{node.op}'")
        self.chunk.emit(Op.POP, None, node.span)
        self._expression(node.right)
        self.chunk.patch(skip, len(self.chunk))


def generate(program: Program, source: Source, symbols: SymbolTable) -> Chunk:
    return CodeGenerator(source, symbols).generate(program)


##############################################################################
# ЧАСТЬ 13. ВИРТУАЛЬНАЯ МАШИНА
##############################################################################


INT_MIN = -(2 ** 31)
INT_MAX = 2 ** 31 - 1


class InputBuffer:
    """Разбирает входной поток на слова: одно слово — одно значение для read."""

    def __init__(self, read_line: Callable[[], str]) -> None:
        self._read_line = read_line
        self._pending: List[str] = []

    def next_word(self) -> Optional[str]:
        while not self._pending:
            line = self._read_line()
            if line == "":
                return None
            self._pending = line.split()
        return self._pending.pop(0)


def _stdin_reader(stream: TextIO) -> Callable[[], str]:
    def read_line() -> str:
        return stream.readline()

    return read_line


@dataclass
class VMState:
    """Снимок состояния машины — используется отладчиком."""

    pc: int
    stack: List[Any]
    slots: List[Any]
    steps: int


@dataclass
class VM:
    """Исполняет `Chunk`. Ввод/вывод вынесены в callables ради тестируемости."""

    chunk: Chunk
    output: Callable[[str], None] = lambda text: sys.stdout.write(text)
    input_source: Optional[InputBuffer] = None
    trace: bool = False
    trace_stream: TextIO = sys.stderr
    max_steps: Optional[int] = None

    pc: int = 0
    steps: int = 0
    halted: bool = False
    stack: List[Any] = field(default_factory=list)
    slots: List[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.slots = [default_value(type_) for type_ in self.chunk.slot_types]
        if self.input_source is None:
            self.input_source = InputBuffer(_stdin_reader(sys.stdin))

    # --- служебное ---------------------------------------------------------------------

    def state(self) -> VMState:
        return VMState(self.pc, list(self.stack), list(self.slots), self.steps)

    def slot_value(self, name: str) -> Any:
        if name not in self.chunk.slot_names:
            raise KeyError(name)
        return self.slots[self.chunk.slot_names.index(name)]

    def _fail(self, code: str, message: str, hint: Optional[str] = None) -> RuntimeMiniPLError:
        instruction = self.chunk.code[self.pc] if self.pc < len(self.chunk.code) else None
        span = instruction.span if instruction else None
        notes = [f"остановлено на инструкции {self.pc:04d} ({instruction.op.name if instruction else '?'})"]
        if self.stack:
            notes.append("стек операндов: " + ", ".join(format_value(v) for v in self.stack[-4:]))
        diagnostic = Diagnostic(Severity.ERROR, code, message, span, hint, notes)
        return RuntimeMiniPLError(diagnostic)

    def _check_int(self, value: int) -> int:
        if value < INT_MIN or value > INT_MAX:
            raise self._fail(
                Code.INT_OVERFLOW,
                f"переполнение 32-битного целого: результат {value}",
                hint="Mini-PL использует знаковые 32-битные целые числа",
            )
        return value

    # --- исполнение ------------------------------------------------------------------------

    def run(self) -> int:
        """Выполняет программу до HALT. Возвращает число выполненных инструкций."""

        while not self.halted:
            self.step()
        return self.steps

    def step(self) -> None:
        """Выполняет одну инструкцию."""

        if self.halted:
            return
        if self.pc >= len(self.chunk.code):
            self.halted = True
            return
        if self.max_steps is not None and self.steps >= self.max_steps:
            raise self._fail(
                Code.STEP_LIMIT,
                f"превышен лимит в {self.max_steps} инструкций — похоже на бесконечный цикл",
                hint="увеличьте лимит флагом --max-steps или проверьте условие цикла",
            )

        instruction = self.chunk.code[self.pc]
        if self.trace:
            print(
                f"{self.chunk.format_instruction(self.pc):<52} стек={self._format_stack()}",
                file=self.trace_stream,
            )
        self.pc += 1
        self.steps += 1
        self._execute(instruction.op, instruction.arg)

    def _format_stack(self) -> str:
        return "[" + ", ".join(format_value(value) for value in self.stack) + "]"

    def _execute(self, op: Op, arg: Optional[int]) -> None:
        stack = self.stack

        if op is Op.CONST:
            stack.append(self.chunk.constants[arg])
        elif op is Op.LOAD:
            stack.append(self.slots[arg])
        elif op is Op.STORE:
            self.slots[arg] = stack.pop()
        elif op is Op.POP:
            stack.pop()
        elif op is Op.DUP:
            stack.append(stack[-1])

        elif op is Op.ADD:
            right = stack.pop()
            left = stack.pop()
            if isinstance(left, str):
                stack.append(left + right)
            else:
                stack.append(self._check_int(left + right))
        elif op is Op.SUB:
            right = stack.pop()
            stack.append(self._check_int(stack.pop() - right))
        elif op is Op.MUL:
            right = stack.pop()
            stack.append(self._check_int(stack.pop() * right))
        elif op is Op.DIV:
            right = stack.pop()
            left = stack.pop()
            # шаг назад, чтобы ошибка указала на саму операцию деления
            self.pc -= 1
            if right == 0:
                raise self._fail(
                    Code.DIVISION_BY_ZERO,
                    f"деление на ноль: {left} / 0",
                    hint="проверьте делитель перед операцией, например: if d <> 0 do ... end if;",
                )
            self.pc += 1
            stack.append(self._truncate_div(left, right))
        elif op is Op.MOD:
            right = stack.pop()
            left = stack.pop()
            self.pc -= 1
            if right == 0:
                raise self._fail(
                    Code.DIVISION_BY_ZERO,
                    f"остаток от деления на ноль: {left} % 0",
                    hint="делитель должен быть отличен от нуля",
                )
            self.pc += 1
            stack.append(left - self._truncate_div(left, right) * right)
        elif op is Op.NEG:
            stack.append(self._check_int(-stack.pop()))

        elif op is Op.LT:
            right = stack.pop()
            stack.append(stack.pop() < right)
        elif op is Op.LE:
            right = stack.pop()
            stack.append(stack.pop() <= right)
        elif op is Op.GT:
            right = stack.pop()
            stack.append(stack.pop() > right)
        elif op is Op.GE:
            right = stack.pop()
            stack.append(stack.pop() >= right)
        elif op is Op.EQ:
            right = stack.pop()
            stack.append(stack.pop() == right)
        elif op is Op.NE:
            right = stack.pop()
            stack.append(stack.pop() != right)
        elif op is Op.NOT:
            stack.append(not stack.pop())

        elif op is Op.JMP:
            self.pc = arg
        elif op is Op.JMPF:
            if not stack.pop():
                self.pc = arg
        elif op is Op.JMPT:
            if stack.pop():
                self.pc = arg

        elif op is Op.PRINT:
            self.output(format_value(stack.pop()))
        elif op is Op.READ:
            self._read_into(arg)
        elif op is Op.ASSERT:
            value = stack.pop()
            if not value:
                self.pc -= 1
                raise self._fail(
                    Code.ASSERTION_FAILED,
                    "нарушено утверждение assert",
                    hint="условие оказалось ложным во время выполнения",
                )
        elif op is Op.HALT:
            self.halted = True
        else:  # pragma: no cover - защита от рассинхронизации набора команд
            raise self._fail(Code.ASSERTION_FAILED, f"неизвестная инструкция {op}")

    @staticmethod
    def _truncate_div(left: int, right: int) -> int:
        """Целочисленное деление с усечением к нулю (как в Pascal/C)."""

        quotient = abs(left) // abs(right)
        return quotient if (left >= 0) == (right >= 0) else -quotient

    def _read_into(self, slot: int) -> None:
        expected = self.chunk.slot_types[slot]
        name = self.chunk.slot_names[slot]
        word = self.input_source.next_word()
        self.pc -= 1  # ошибки ввода указывают на сам оператор read
        if word is None:
            raise self._fail(
                Code.BAD_INPUT,
                f"неожиданный конец ввода при чтении переменной '{name}'",
                hint="передайте данные через стандартный ввод или флаг --stdin",
            )
        if expected is Type.INT:
            try:
                value = int(word)
            except ValueError:
                raise self._fail(
                    Code.BAD_INPUT,
                    f"ожидалось целое число для '{name}', получено {word!r}",
                    hint="введите число, например 42",
                ) from None
            self._check_int(value)
        else:
            value = word
        self.pc += 1
        self.slots[slot] = value


##############################################################################
# ЧАСТЬ 14. КОНВЕЙЕР КОМПИЛЯЦИИ
##############################################################################


@dataclass
class Compilation:
    """Результат компиляции: артефакты всех фаз плюс собранные диагностики."""

    source: Source
    diagnostics: DiagnosticBag
    tokens: List[Token]
    program: Optional[Program] = None
    symbols: Optional[SymbolTable] = None
    chunk: Optional[Chunk] = None

    @property
    def ok(self) -> bool:
        return self.chunk is not None and not self.diagnostics.has_errors()


def compile_source(source: Source, stop_after: Optional[str] = None) -> Compilation:
    """Проходит фазы лексинга, разбора, семантики и генерации кода.

    Каждая фаза выполняется только если предыдущая не нашла ошибок: сообщать
    о типах в дереве, которое не удалось разобрать, бессмысленно.
    `stop_after` (`tokens` | `ast` | `semantics`) останавливает конвейер раньше.
    """

    diagnostics = DiagnosticBag(source)
    tokens = tokenize(source, diagnostics)
    result = Compilation(source, diagnostics, tokens)
    if stop_after == "tokens" or diagnostics.has_errors():
        return result

    result.program = parse(tokens, source, diagnostics)
    if stop_after == "ast" or diagnostics.has_errors():
        return result

    result.symbols = analyze(result.program, source, diagnostics)
    if stop_after == "semantics" or diagnostics.has_errors():
        return result

    result.chunk = generate(result.program, source, result.symbols)
    return result##############################################################################
# ЧАСТЬ 15. ИНТЕРАКТИВНЫЙ ОТЛАДЧИК
##############################################################################
#
# Отладчик работает поверх виртуальной машины: он сам управляет циклом
# `vm.step()`, поэтому может останавливаться на точках останова, шагать по
# строкам исходника и по отдельным инструкциям.
#
# Выражения в командах `print` и `break ... if ...` разбираются тем же
# компилятором, что и программа: текст выражения проходит лексер, парсер,
# проверку типов в уже построенной таблице символов и генерацию кода, после
# чего исполняется на копии слотов основной машины.


class DebugCommandError(Exception):
    """Ошибка в команде отладчика (не в отлаживаемой программе)."""


@dataclass
class Breakpoint:
    id: int
    kind: str  # "line" — на строке исходника, "addr" — на адресе инструкции
    value: int
    condition: Optional[str] = None
    enabled: bool = True
    hits: int = 0

    def describe(self) -> str:
        where = f"строка {self.value}" if self.kind == "line" else f"адрес {self.value:04d}"
        state = "" if self.enabled else " [выключена]"
        condition = f" если {self.condition}" if self.condition else ""
        return f"#{self.id} {where}{condition}{state}, срабатываний: {self.hits}"


HELP_TEXT = """\
Команды отладчика Mini-PL
  run, r               перезапустить программу с начала
  continue, c          продолжить до точки останова или до конца
  step, s, n           шаг по исходному коду (одна строка)
  stepi, si [N]        шаг по байткоду (N инструкций, по умолчанию 1)
  break, b <строка>    точка останова на строке; b *<адрес> — на инструкции
                       b <строка> if <условие> — условная точка останова
  delete, d [номер]    удалить точку останова (без номера — все)
  enable/disable <n>   включить или выключить точку останова
  info breaks          список точек останова
  watch, w <перем>     останавливаться при изменении переменной
  unwatch [перем]      снять наблюдение
  print, p <выраж>     вычислить выражение в текущем состоянии
  vars [all]           значения всех переменных (all — со служебными слотами)
  set <перем> := <выр> изменить значение переменной на лету
  stack                показать стек операндов
  list, l [строка]     показать исходный код вокруг строки
  dis [N]              дизассемблировать N инструкций вокруг счётчика команд
  where, bt            где мы сейчас находимся
  help, h              эта справка
  quit, q              выйти из отладчика\
"""


class Debugger:
    """Интерактивный отладчик уровня исходного кода."""

    def __init__(
        self,
        compilation: "Compilation",
        input_factory: Optional[Callable[[], "InputBuffer"]] = None,
        output: Optional[Callable[[str], None]] = None,
        console: TextIO = sys.stdout,
        read_command: Optional[Callable[[str], str]] = None,
        max_steps: Optional[int] = None,
    ) -> None:
        if compilation.chunk is None or compilation.symbols is None:
            raise ValueError("отладчику нужна успешно скомпилированная программа")
        self.compilation = compilation
        self.source = compilation.source
        self.chunk = compilation.chunk
        self.symbols = compilation.symbols
        self.console = console
        self.output = output or (lambda text: (console.write(text), console.flush()))
        # ввод создаётся фабрикой: при перезапуске программы данные читаются заново
        self.input_factory = input_factory or (lambda: InputBuffer(sys.stdin.readline))
        self.input_source = self.input_factory()
        self.max_steps = max_steps
        self.read_command = read_command or (lambda prompt: input(prompt))

        self.breakpoints: List[Breakpoint] = []
        self.watches: List[str] = []
        self._next_breakpoint_id = 1
        self.crashed = False
        self.vm = self._new_vm()
        self._last_list_line = 1

    # --- инфраструктура ---------------------------------------------------------------

    def _new_vm(self) -> "VM":
        self.input_source = self.input_factory()
        return VM(
            self.chunk,
            output=self.output,
            input_source=self.input_source,
            max_steps=self.max_steps,
        )

    def _say(self, text: str = "") -> None:
        print(text, file=self.console)

    def _error(self, text: str) -> None:
        self._say(Colors.paint(f"ошибка: {text}", Colors.RED))

    def _current_line(self) -> int:
        if 0 <= self.vm.pc < len(self.chunk.code):
            return self.chunk.code[self.vm.pc].line
        return 0

    def _user_slots(self) -> int:
        """Количество «настоящих» переменных (без служебных слотов циклов)."""

        return len(self.symbols)

    # --- вычисление выражений тем же компилятором ---------------------------------------

    def _compile_expression(self, text: str) -> "Chunk":
        source = Source(text, "<debug>")
        bag = DiagnosticBag(source)
        tokens = tokenize(source, bag)
        if bag.has_errors():
            raise DebugCommandError(bag.items[0].message)
        parser = Parser(tokens, source, bag)
        try:
            expr = parser.parse_expression()
        except CompileError as exc:
            raise DebugCommandError(exc.diagnostic.message) from None
        analyzer = SemanticAnalyzer(source, bag)
        analyzer.symbols = self.symbols  # выражение видит переменные программы
        expr_type = analyzer.check_expression(expr)
        if bag.has_errors():
            raise DebugCommandError(bag.errors[0].message)
        if expr_type is Type.ERROR:
            raise DebugCommandError("выражение содержит ошибку типов")
        return CodeGenerator(source, self.symbols).generate_expression(expr)

    def evaluate(self, text: str) -> Any:
        """Вычисляет выражение в текущем состоянии машины."""

        chunk = self._compile_expression(text)
        sandbox = VM(chunk, output=lambda _: None, input_source=self.input_source)
        limit = min(len(sandbox.slots), len(self.vm.slots))
        sandbox.slots[:limit] = self.vm.slots[:limit]
        try:
            sandbox.run()
        except RuntimeMiniPLError as exc:
            raise DebugCommandError(exc.diagnostic.message) from None
        if not sandbox.stack:
            raise DebugCommandError("выражение не вернуло значения")
        return sandbox.stack[-1]

    # --- точки останова -------------------------------------------------------------------

    def _breakpoint_hit(self) -> Optional[Breakpoint]:
        """Проверяет, стоит ли остановиться на текущем счётчике команд."""

        pc = self.vm.pc
        if pc >= len(self.chunk.code):
            return None
        line = self.chunk.code[pc].line
        previous_line = self.chunk.code[pc - 1].line if pc > 0 else -1
        for breakpoint in self.breakpoints:
            if not breakpoint.enabled:
                continue
            if breakpoint.kind == "addr" and breakpoint.value != pc:
                continue
            # точка останова на строке срабатывает только на её первой инструкции
            if breakpoint.kind == "line" and not (
                breakpoint.value == line and previous_line != line
            ):
                continue
            if breakpoint.condition:
                try:
                    if not self.evaluate(breakpoint.condition):
                        continue
                except DebugCommandError as exc:
                    self._error(f"условие точки останова #{breakpoint.id}: {exc}")
            breakpoint.hits += 1
            return breakpoint
        return None

    # --- исполнение под управлением отладчика -----------------------------------------------

    def _snapshot_watches(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for name in self.watches:
            symbol = self.symbols.lookup(name)
            if symbol is not None:
                values[name] = self.vm.slots[symbol.slot]
        return values

    def _resume(self, mode: str, count: int = 1) -> None:
        """Основной цикл: mode — 'continue', 'line' или 'instr'."""

        if self.vm.halted:
            self._say("программа уже завершена; используйте 'run' для перезапуска")
            return

        start_line = self._current_line()
        executed = 0
        while True:
            before = self._snapshot_watches()
            try:
                self.vm.step()
            except RuntimeMiniPLError as exc:
                self.crashed = True
                self.vm.halted = True
                self._say()
                self._say(exc.diagnostic.render(self.source))
                self._say("выполнение остановлено; состояние доступно для осмотра")
                return
            executed += 1

            changed = [
                name
                for name, value in self._snapshot_watches().items()
                if before.get(name) != value
            ]
            if changed:
                for name in changed:
                    self._say(
                        Colors.paint(
                            f"наблюдение: {name} = {repr_value(self.vm.slot_value(name))}"
                            f" (было {repr_value(before[name])})",
                            Colors.YELLOW,
                        )
                    )
                self._report_stop()
                return

            if self.vm.halted:
                self._say(
                    Colors.paint(
                        f"программа завершена, выполнено инструкций: {self.vm.steps}",
                        Colors.GREEN,
                    )
                )
                return

            breakpoint = self._breakpoint_hit()
            if breakpoint is not None:
                self._say(Colors.paint(f"остановка на точке {breakpoint.describe()}", Colors.CYAN))
                self._report_stop()
                return

            if mode == "instr" and executed >= count:
                self._report_stop()
                return
            if mode == "line" and self._current_line() != start_line:
                self._report_stop()
                return

    def _report_stop(self) -> None:
        line = self._current_line()
        instruction = (
            self.chunk.format_instruction(self.vm.pc)
            if self.vm.pc < len(self.chunk.code)
            else "<конец программы>"
        )
        self._say(f"{Colors.paint('строка', Colors.BLUE)} {line}: {self.source.line_text(line)}")
        self._say(f"{Colors.paint('след. инструкция', Colors.BLUE)} {instruction}")
        self._last_list_line = line

    # --- команды --------------------------------------------------------------------------

    def cmd_list(self, argument: str) -> None:
        center = self._current_line() or self._last_list_line
        if argument:
            try:
                center = int(argument)
            except ValueError:
                raise DebugCommandError(f"'{argument}' не является номером строки")
        first = max(1, center - 5)
        last = min(self.source.line_count, center + 5)
        breakpoint_lines = {bp.value for bp in self.breakpoints if bp.kind == "line" and bp.enabled}
        for number in range(first, last + 1):
            marker = "=>" if number == self._current_line() else "  "
            flag = "*" if number in breakpoint_lines else " "
            self._say(f"{marker}{flag}{number:4d} | {self.source.line_text(number)}")
        self._last_list_line = center

    def cmd_dis(self, argument: str) -> None:
        window = 6
        if argument:
            try:
                window = max(1, int(argument))
            except ValueError:
                raise DebugCommandError(f"'{argument}' не является числом")
        first = max(0, self.vm.pc - window // 2)
        last = min(len(self.chunk.code), first + window)
        for index in range(first, last):
            marker = "=>" if index == self.vm.pc else "  "
            self._say(f"{marker} {self.chunk.format_instruction(index)}")

    def cmd_break(self, argument: str) -> None:
        if not argument:
            raise DebugCommandError("укажите строку: b 12, b *42 или b 12 if i > 3")
        condition = None
        if " if " in argument:
            argument, condition = argument.split(" if ", 1)
            argument, condition = argument.strip(), condition.strip()
            self._compile_expression(condition)  # проверяем условие сразу
        if argument.startswith("*"):
            kind, raw = "addr", argument[1:]
        else:
            kind, raw = "line", argument
        try:
            value = int(raw)
        except ValueError:
            raise DebugCommandError(f"'{raw}' не является числом") from None
        if kind == "line" and not any(
            instruction.line == value for instruction in self.chunk.code
        ):
            raise DebugCommandError(
                f"строка {value} не содержит исполняемого кода — "
                "поставьте точку останова на строку с оператором"
            )
        if kind == "addr" and not 0 <= value < len(self.chunk.code):
            raise DebugCommandError(f"адрес {value} вне диапазона 0..{len(self.chunk.code) - 1}")
        breakpoint = Breakpoint(self._next_breakpoint_id, kind, value, condition)
        self._next_breakpoint_id += 1
        self.breakpoints.append(breakpoint)
        self._say(f"создана точка останова {breakpoint.describe()}")

    def cmd_delete(self, argument: str) -> None:
        if not argument:
            self.breakpoints.clear()
            self._say("все точки останова удалены")
            return
        try:
            identifier = int(argument)
        except ValueError:
            raise DebugCommandError(f"'{argument}' не является номером точки останова") from None
        before = len(self.breakpoints)
        self.breakpoints = [bp for bp in self.breakpoints if bp.id != identifier]
        if len(self.breakpoints) == before:
            raise DebugCommandError(f"точка останова #{identifier} не найдена")
        self._say(f"точка останова #{identifier} удалена")

    def cmd_toggle(self, argument: str, enabled: bool) -> None:
        try:
            identifier = int(argument)
        except ValueError:
            raise DebugCommandError("укажите номер точки останова") from None
        for breakpoint in self.breakpoints:
            if breakpoint.id == identifier:
                breakpoint.enabled = enabled
                self._say(f"точка останова #{identifier}: {'включена' if enabled else 'выключена'}")
                return
        raise DebugCommandError(f"точка останова #{identifier} не найдена")

    def cmd_info(self, argument: str) -> None:
        topic = argument.strip() or "breaks"
        if topic.startswith("break"):
            if not self.breakpoints:
                self._say("точек останова нет")
            for breakpoint in self.breakpoints:
                self._say(breakpoint.describe())
            if self.watches:
                self._say("наблюдение за: " + ", ".join(self.watches))
        elif topic.startswith("chunk") or topic.startswith("code"):
            self._say(self.chunk.disassemble())
        else:
            raise DebugCommandError("известные разделы: info breaks, info chunk")

    def cmd_vars(self, argument: str) -> None:
        show_all = argument.strip() == "all"
        limit = len(self.chunk.slot_names) if show_all else self._user_slots()
        if limit == 0:
            self._say("переменных нет")
        for slot in range(limit):
            name = self.chunk.slot_names[slot]
            type_ = self.chunk.slot_types[slot]
            value = repr_value(self.vm.slots[slot])
            self._say(f"  [{slot}] {name} : {type_} = {value}")

    def cmd_print(self, argument: str) -> None:
        if not argument:
            raise DebugCommandError("укажите выражение: p x + 1")
        value = self.evaluate(argument)
        self._say(f"{argument} = {repr_value(value)}")

    def cmd_set(self, argument: str) -> None:
        separator = ":=" if ":=" in argument else "=" if "=" in argument else None
        if separator is None:
            raise DebugCommandError("формат: set x := выражение")
        name, expression = argument.split(separator, 1)
        name, expression = name.strip(), expression.strip()
        symbol = self.symbols.lookup(name)
        if symbol is None:
            raise DebugCommandError(f"переменная '{name}' не объявлена")
        value = self.evaluate(expression)
        actual = (
            Type.BOOL
            if isinstance(value, bool)
            else Type.INT
            if isinstance(value, int)
            else Type.STRING
        )
        if actual is not symbol.type:
            raise DebugCommandError(
                f"переменная '{name}' имеет тип {symbol.type}, а значение — {actual}"
            )
        self.vm.slots[symbol.slot] = value
        self._say(f"{name} = {repr_value(value)}")

    def cmd_watch(self, argument: str) -> None:
        name = argument.strip()
        if not name:
            raise DebugCommandError("укажите имя переменной")
        if self.symbols.lookup(name) is None:
            raise DebugCommandError(f"переменная '{name}' не объявлена")
        if name not in self.watches:
            self.watches.append(name)
        self._say(f"наблюдение за '{name}' включено")

    def cmd_unwatch(self, argument: str) -> None:
        name = argument.strip()
        if not name:
            self.watches.clear()
            self._say("наблюдение снято со всех переменных")
            return
        if name in self.watches:
            self.watches.remove(name)
            self._say(f"наблюдение за '{name}' снято")
        else:
            raise DebugCommandError(f"за '{name}' наблюдение не велось")

    def cmd_stack(self) -> None:
        if not self.vm.stack:
            self._say("стек операндов пуст")
            return
        for index, value in enumerate(reversed(self.vm.stack)):
            marker = "вершина" if index == 0 else "       "
            self._say(f"  {marker} [{len(self.vm.stack) - 1 - index}] {repr_value(value)}")

    def cmd_where(self) -> None:
        line = self._current_line()
        self._say(f"файл {self.source.name}, строка {line}, инструкция {self.vm.pc:04d}, "
                  f"выполнено шагов: {self.vm.steps}")
        if line:
            self._say(f"  {line} | {self.source.line_text(line)}")

    def cmd_restart(self) -> None:
        self.vm = self._new_vm()
        self.crashed = False
        self._say("программа перезапущена")
        self._report_stop()

    # --- цикл чтения команд --------------------------------------------------------------

    def start(self) -> int:
        self._say(Colors.paint("отладчик Mini-PL", Colors.BOLD))
        self._say(f"файл: {self.source.name}, инструкций: {len(self.chunk.code)}")
        self._say("введите 'help' для списка команд, 'run' или 'step' для запуска")
        self._report_stop()

        previous = ""
        while True:
            try:
                raw = self.read_command("(minipl) ")
            except EOFError:
                self._say()
                break
            except KeyboardInterrupt:
                self._say()
                self._say("прервано; 'quit' для выхода")
                continue

            line = raw.strip()
            if not line:
                line = previous  # пустая строка повторяет предыдущую команду
            if not line:
                continue
            previous = line
            command, _, argument = line.partition(" ")
            command, argument = command.strip(), argument.strip()

            try:
                if command in ("quit", "q", "exit"):
                    break
                elif command in ("help", "h", "?"):
                    self._say(HELP_TEXT)
                elif command in ("run", "r"):
                    self.cmd_restart()
                elif command in ("continue", "c"):
                    self._resume("continue")
                elif command in ("step", "s", "n", "next"):
                    self._resume("line")
                elif command in ("stepi", "si"):
                    count = int(argument) if argument else 1
                    self._resume("instr", count)
                elif command in ("break", "b"):
                    self.cmd_break(argument)
                elif command in ("delete", "d"):
                    self.cmd_delete(argument)
                elif command == "enable":
                    self.cmd_toggle(argument, True)
                elif command == "disable":
                    self.cmd_toggle(argument, False)
                elif command == "info":
                    self.cmd_info(argument)
                elif command in ("print", "p"):
                    self.cmd_print(argument)
                elif command == "vars":
                    self.cmd_vars(argument)
                elif command == "set":
                    self.cmd_set(argument)
                elif command in ("watch", "w"):
                    self.cmd_watch(argument)
                elif command == "unwatch":
                    self.cmd_unwatch(argument)
                elif command == "stack":
                    self.cmd_stack()
                elif command in ("list", "l"):
                    self.cmd_list(argument)
                elif command == "dis":
                    self.cmd_dis(argument)
                elif command in ("where", "bt", "backtrace"):
                    self.cmd_where()
                else:
                    self._error(f"неизвестная команда '{command}'; 'help' покажет список")
            except DebugCommandError as exc:
                self._error(str(exc))
            except ValueError as exc:
                self._error(str(exc))

        return 2 if self.crashed else 0
##############################################################################
# ЧАСТЬ 16. КОМАНДНАЯ СТРОКА
##############################################################################

EXIT_OK = 0
EXIT_COMPILE_ERROR = 1
EXIT_RUNTIME_ERROR = 2
EXIT_USAGE_ERROR = 3

VERSION = "1.0"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minipl",
        description="Компилятор и отладчик языка Mini-PL.",
        epilog=(
            "примеры:\n"
            "  python3 minipl.py program.mpl                 скомпилировать и выполнить\n"
            "  python3 minipl.py program.mpl --debug         запустить под отладчиком\n"
            "  python3 minipl.py program.mpl --tokens --ast  показать токены и дерево\n"
            "  python3 minipl.py program.mpl --dis --check    показать байткод без запуска\n"
            "  python3 minipl.py program.mpl --emit out.mplc  сохранить байткод\n"
            "  python3 minipl.py out.mplc --from-bytecode     выполнить сохранённый байткод\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", nargs="?", help="файл с программой на Mini-PL")
    parser.add_argument("-e", "--eval", metavar="КОД", help="выполнить код, переданный строкой")

    stages = parser.add_argument_group("что показать")
    stages.add_argument("--tokens", action="store_true", help="вывести поток токенов")
    stages.add_argument("--ast", action="store_true", help="вывести синтаксическое дерево")
    stages.add_argument("--dis", "--disasm", action="store_true", dest="dis",
                        help="вывести дизассемблированный байткод")
    stages.add_argument("--symbols", action="store_true", help="вывести таблицу символов")

    modes = parser.add_argument_group("режимы работы")
    modes.add_argument("--check", action="store_true", help="только проверить программу, не запускать")
    modes.add_argument("--debug", "-d", action="store_true", help="запустить интерактивный отладчик")
    modes.add_argument("--trace", action="store_true", help="печатать каждую выполняемую инструкцию")
    modes.add_argument("--emit", metavar="ФАЙЛ", help="сохранить байткод в файл")
    modes.add_argument("--from-bytecode", action="store_true",
                       help="входной файл содержит ранее сохранённый байткод")

    runtime = parser.add_argument_group("выполнение")
    runtime.add_argument("--stdin", metavar="ТЕКСТ", help="данные для оператора read")
    runtime.add_argument("--stdin-file", metavar="ФАЙЛ", help="файл с данными для оператора read")
    runtime.add_argument("--max-steps", type=int, metavar="N",
                         help="прервать выполнение после N инструкций (защита от зацикливания)")

    output = parser.add_argument_group("диагностика")
    output.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="раскраска сообщений (по умолчанию auto)")
    output.add_argument("--no-warnings", action="store_true", help="не показывать предупреждения")
    output.add_argument("--strict", action="store_true", help="считать предупреждения ошибками")
    output.add_argument("--version", action="version", version=f"Mini-PL compiler {VERSION}")
    return parser


def _make_input_buffer(args: argparse.Namespace) -> InputBuffer:
    """Готовит источник данных для оператора read."""

    if args.stdin is not None:
        lines = iter(args.stdin.splitlines(True))
        return InputBuffer(lambda: next(lines, ""))
    if args.stdin_file is not None:
        handle = open(args.stdin_file, "r", encoding="utf-8")
        return InputBuffer(handle.readline)
    return InputBuffer(sys.stdin.readline)


def _load_source(args: argparse.Namespace) -> Source:
    if args.eval is not None:
        return Source(args.eval, "<командная строка>")
    return Source.from_file(args.file)


def _print_stage_output(compilation: Compilation, args: argparse.Namespace) -> None:
    if args.tokens:
        print("--- токены ---")
        for token in compilation.tokens:
            print(f"  {token}")
    if args.ast and compilation.program is not None:
        print("--- синтаксическое дерево ---")
        print(dump(compilation.program))
    if args.symbols and compilation.symbols is not None:
        print("--- таблица символов ---")
        for symbol in compilation.symbols:
            place = f" объявлена в {symbol.declared_at.start}" if symbol.declared_at else ""
            print(
                f"  [{symbol.slot}] {symbol.name} : {symbol.type}"
                f" (присваиваний: {symbol.assignments}, используется: "
                f"{'да' if symbol.used else 'нет'}){place}"
            )
    if args.dis and compilation.chunk is not None:
        print("--- байткод ---")
        print(compilation.chunk.disassemble())


def _report(compilation: Compilation, args: argparse.Namespace) -> None:
    bag = compilation.diagnostics
    if args.no_warnings:
        bag.items = [item for item in bag.items if item.severity is not Severity.WARNING]
    bag.report(sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    Colors.configure(sys.stderr, force=None if args.color == "auto" else args.color == "always")

    if args.file is None and args.eval is None:
        parser.print_usage(sys.stderr)
        print("minipl: укажите файл с программой или используйте -e 'код'", file=sys.stderr)
        return EXIT_USAGE_ERROR

    # --- ветка запуска готового байткода -------------------------------------------------
    if args.from_bytecode:
        try:
            chunk = Chunk.load(args.file)
        except (OSError, ValueError, KeyError) as exc:
            print(f"minipl: не удалось прочитать байткод: {exc}", file=sys.stderr)
            return EXIT_USAGE_ERROR
        if args.dis:
            print("--- байткод ---")
            print(chunk.disassemble())
        if args.check:
            return EXIT_OK
        return _execute(chunk, Source("", chunk.source_name), args)

    # --- обычная компиляция ------------------------------------------------------------
    try:
        source = _load_source(args)
    except OSError as exc:
        print(f"minipl: не удалось открыть файл: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    compilation = compile_source(source)
    _print_stage_output(compilation, args)
    _report(compilation, args)

    if compilation.diagnostics.has_errors():
        return EXIT_COMPILE_ERROR
    if args.strict and compilation.diagnostics.warnings:
        print("minipl: предупреждения считаются ошибками (--strict)", file=sys.stderr)
        return EXIT_COMPILE_ERROR
    if compilation.chunk is None:
        return EXIT_COMPILE_ERROR

    if args.emit:
        try:
            compilation.chunk.save(args.emit)
        except OSError as exc:
            print(f"minipl: не удалось сохранить байткод: {exc}", file=sys.stderr)
            return EXIT_USAGE_ERROR
        print(f"байткод сохранён в {args.emit}", file=sys.stderr)

    if args.check:
        print("проверка завершена: ошибок не найдено", file=sys.stderr)
        return EXIT_OK

    if args.debug:
        try:
            debugger = Debugger(
                compilation,
                input_factory=lambda: _make_input_buffer(args),
                max_steps=args.max_steps,
            )
        except OSError as exc:
            print(f"minipl: {exc}", file=sys.stderr)
            return EXIT_USAGE_ERROR
        return debugger.start()

    return _execute(compilation.chunk, source, args)


def _execute(chunk: Chunk, source: Source, args: argparse.Namespace) -> int:
    """Запускает виртуальную машину и печатает ошибки времени выполнения."""

    try:
        input_source = _make_input_buffer(args)
    except OSError as exc:
        print(f"minipl: не удалось открыть файл ввода: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    machine = VM(
        chunk,
        output=lambda text: (sys.stdout.write(text), sys.stdout.flush()),
        input_source=input_source,
        trace=args.trace,
        max_steps=args.max_steps,
    )
    try:
        machine.run()
    except RuntimeMiniPLError as exc:
        sys.stdout.flush()
        print(file=sys.stderr)
        print(exc.diagnostic.render(source if source.text else None), file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    except KeyboardInterrupt:
        print("\nminipl: выполнение прервано пользователем", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    if args.trace:
        print(f"; выполнено инструкций: {machine.steps}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
