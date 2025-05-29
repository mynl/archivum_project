"""Gemini translation."""

from pathlib import Path
from pprint import pprint
import re
from ply import lex, yacc


def parse_test(text, debug=False, show_tokens=False):
    """Convenience to test the grammar, run with a test text."""
    lexer = ArcLexer()
    parser = ArcParser(debug=debug)
    try:
        tokens = list(lexer.tokenize(text))
        if show_tokens:
            print("Tokens:")
            for tok in tokens:
                print(f"  {tok.type:<10} {tok.value!r}")
            print('-' * 80)
        result = parser.parse(iter(tokens))
        if debug:
            print('-' * 80)
            print("Parsed result query spec")
            print("========================\n")
        pprint(result)
    except Exception as e:
        raise ValueError(f'Parsing Error: {e}')


def parser(text, debug=False):
    """One stop shop parsing."""
    lexer = ArcLexer()
    parser = ArcParser(debug=debug)
    result = None
    try:
        tokens = list(lexer.tokenize(text))
        result = parser.parse(iter(tokens))
    except Exception as e:
        raise ValueError(f'Parsing Error: {e}')
    return result


class ArcLexer:
    """Lexer for file database query language."""

    tokens = (
        'IDENTIFIER', 'NUMBER', 'QUOTED_STRING', 'REGEX_SLASHED',
        'DATETIME', 'SELECT', 'ORDER_BY', 'WHERE', 'TOP', 'EQ_TEST', 'AND',
        'FLAG', 'STAR', 'NOT', 'TILDE', 'BANG'
    )

    ignore = ' \t'
    literals = {','}

    # longer more specific matches should come first
    SELECT = r'select|SELECT'
    ORDER_BY = r'order|ORDER|sort|SORT'
    WHERE = r'where|WHERE'
    TOP = r'top|TOP'
    AND = r'and|AND'
    FLAG = r'recent|RECENT|verbose|VERBOSE'

    NOT = r'\-'
    EQ_TEST = r'==|<=|<|>|>='
    DATETIME = r'''
(?x)
    # Full date and time: 2024-05-15 13:45
    (19|20)\d{2}
    -(0[1-9]|1[0-2])
    -(0[1-9]|[12][0-9]|3[01])
    \ (?:[01][0-9]|2[0-3]):[0-5][0-9]

  | # Full date: 2024-05-15
    (19|20)\d{2}
    -(0[1-9]|1[0-2])
    -(0[1-9]|[12][0-9]|3[01])

  | # Year and month only: 2024-05
    (19|20)\d{2}
    -(0[1-9]|1[0-2])

  | # Month and day only: 05-15
    (0[1-9]|1[0-2])
    -(0[1-9]|[12][0-9]|3[01])

  | # Time only: 13:45
    (?:[01][0-9]|2[0-3])
    :[0-5][0-9]
'''
    NUMBER = r'-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf'
    QUOTED_STRING = r'''"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''''
    REGEX_SLASHED = r'/([^/\\]|\\.)*(/|$)'
    IDENTIFIER = r'[^\s~/!,][^\s~=<>,]*'
    STAR = r'\*'
    TILDE = r'~'
    BANG = r'!'

    def t_QUOTED_STRING(self, t):
        r'''"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''''
        t.value = t.value[1:-1]
        return t

    def t_REGEX_SLASHED(self, t):
        r'/([^/\\]|\\.)*(/|$)'
        if t.value.endswith('/'):
            t.value = t.value[1:-1]
        else:
            t.value = t.value[1:]
        return t

    def t_newline(self, t):
        r'\n+'
        t.lexer.lineno += t.value.count('\n')

    def t_error(self, t):
        print(f"Illegal character {t.value[0]!r} at line {t.lineno}")
        t.lexer.skip(1)

    def tokenize(self, text):
        # PLY's lexer.input() and lexer.token() are used for tokenizing
        lexer = lex.lex(module=self)
        lexer.input(text)
        while True:
            tok = lexer.token()
            if not tok:
                break
            yield tok


class ArcParser:
    """Parser for file database query language."""

    tokens = ArcLexer.tokens
    debugfile = None

    def __init__(self, debug=False):
        self.debug = debug
        if debug:
            print(f'ArcParser created {debug = }')
            self.debugfile = 'parser.out'
        # self.enhance_debugfile() # Not directly applicable for standard PLY usage in this context

    def logger(self, msg, p, force=False):
        if not force and not self.debug:
            return
        parts = []
        for i, val in enumerate(p):
            parts.append(f"[{i}] {val!r}")
        nl = '\n\t'
        print(f"LOGGER: {msg:20s}\n\t{nl.join(parts)}")

    def p_query(self, p):
        'query : clause_list'
        self.logger('clause_list -> query', p)
        spec = {
            'select': {},
            'sort': [],
            'regexes': [],
            'where': None,
            'top': -1,
            'flags': {}
        }
        for clause in p[1]:
            spec[clause[0]] = clause[1]
        p[0] = spec

    def p_clause_list_empty(self, p):
        'clause_list : '
        self.logger('none -> clause_list', p)
        p[0] = []

    def p_clause_list_multiple(self, p):
        'clause_list : clause_list clause'
        self.logger('clause_list clause -> clause_list', p)
        p[0] = p[1] + [p[2]]

    def p_clause_list_single(self, p):
        'clause_list : clause'
        self.logger('clause -> clause_list', p)
        p[0] = [p[1]]

    def p_clause_top(self, p):
        'clause : TOP NUMBER'
        self.logger('TOP NUMBER -> clause', p)
        try:
            n = int(p[2])
        except ValueError:
            n = 10
            print('Error parsing top nn, nn not an int')
        p[0] = ('top', n)

    def p_clause_flags(self, p):
        'clause : flags'
        self.logger('flags -> clause', p)
        p[0] = ('flags', p[1])

    def p_flags_multiple(self, p):
        'flags : FLAG flags'
        self.logger('flags FLAG -> flags', p)
        p[2].update({p[1]: True})
        p[0] = p[2]

    def p_flags_single(self, p):
        'flags : FLAG'
        self.logger('FLAG -> flags', p)
        p[0] = {p[1]: True}

    def p_clause_regexes(self, p):
        'clause : regexes'
        self.logger('regexes -> clause', p)
        p[0] = ('regexes', p[1])

    def p_regexes_multiple(self, p):
        'regexes : regexes AND regex'
        self.logger('regexes AND regex -> regexes', p)
        p[1].append(p[3])
        p[0] = p[1]

    def p_regexes_single(self, p):
        'regexes : regex'
        self.logger('regex -> regexes', p)
        p[0] = [p[1]]

    def p_regex_identifier_tilde_slashed(self, p):
        'regex : IDENTIFIER TILDE REGEX_SLASHED'
        self.logger('IDENTIFIER TILDE REGEX_SLASHED -> regex', p)
        p[0] = (p[1], p[3])

    def p_regex_identifier_tilde_identifier(self, p):
        'regex : IDENTIFIER TILDE IDENTIFIER'
        self.logger('IDENTIFIER TILDE IDENTIFIER -> regex', p)
        p[0] = (p[1], p[3])

    def p_regex_bang_slashed(self, p):
        'regex : BANG REGEX_SLASHED'
        self.logger('BANG REGEX_SLASHED -> regex', p)
        p[0] = ('BANG', p[2])

    def p_regex_bang_identifier(self, p):
        'regex : BANG IDENTIFIER'
        self.logger('BANG IDENTIFIER -> regex', p)
        p[0] = ('BANG', p[2])

    def p_clause_select(self, p):
        'clause : SELECT select_list'
        self.logger('SELECT select_list -> clause', p)
        p[0] = ('select', p[2])

    def p_select_list_multiple(self, p):
        'select_list : select_list "," select_item'
        self.logger('select_list "," select_item -> select_list', p)
        for k, v in p[3].items():
            p[1][k].extend(v)
        p[0] = p[1]

    def p_select_list_single(self, p):
        'select_list : select_item'
        self.logger('select_item -> select_list', p)
        p[0] = p[1]

    def p_select_item_identifier(self, p):
        'select_item : IDENTIFIER'
        self.logger('IDENTIFIER -> select_item', p)
        p[0] = {'include': [p[1]], 'exclude': []}

    def p_select_item_not_identifier(self, p):
        'select_item : NOT IDENTIFIER'
        self.logger('NOT IDENTIFIER -> select_item', p)
        p[0] = {'include': [], 'exclude': [p[2]]}

    def p_select_item_star(self, p):
        'select_item : STAR'
        self.logger('STAR -> select_item', p)
        p[0] = {'include': ['*'], 'exclude': []}

    def p_clause_where(self, p):
        'clause : WHERE where_clause_expression'
        self.logger('WHERE where_clause_expression -> clause', p)
        p[0] = ('where', p[2])

    def p_where_clause_expression_multiple(self, p):
        'where_clause_expression : where_clause_expression AND where_clause'
        self.logger('where_clause_expression AND where_clause -> where_clause_expression', p)
        p[0] = p[1] + ' and ' + p[3]

    def p_where_clause_expression_single(self, p):
        'where_clause_expression : where_clause'
        self.logger('where_clause -> where_clause_expression', p)
        p[0] = p[1]

    def p_where_clause_quoted_string(self, p):
        'where_clause : IDENTIFIER EQ_TEST QUOTED_STRING'
        self.logger('IDENTIFIER EQ_TEST QUOTED_STRING -> where_clause', p)
        p[0] = f'{p[1]} {p[2]} "{p[3]}"'

    def p_where_clause_identifier(self, p):
        'where_clause : IDENTIFIER EQ_TEST IDENTIFIER'
        self.logger('IDENTIFIER EQ_TEST IDENTIFIER -> where_clause', p)
        p[0] = f'{p[1]} {p[2]} "{p[3]}"'

    def p_where_clause_number(self, p):
        'where_clause : IDENTIFIER EQ_TEST NUMBER'
        self.logger('IDENTIFIER EQ_TEST NUMBER -> where_clause', p)
        p[0] = f'{p[1]} {p[2]} {p[3]}'

    def p_where_clause_datetime(self, p):
        'where_clause : IDENTIFIER EQ_TEST DATETIME'
        self.logger('IDENTIFIER EQ_TEST DATETIME -> where_clause', p)
        p[0] = f'{p[1]} {p[2]} "{p[3]}"'

    def p_clause_order_by(self, p):
        'clause : ORDER_BY column_sort_list'
        self.logger('ORDER_BY column_sort_list -> clause', p)
        p[0] = ('sort', p[2])

    def p_column_sort_list_multiple(self, p):
        'column_sort_list : column_sort_list "," column_sort'
        self.logger('column_sort_list "," column_sort -> column_sort_list', p)
        p[0] = p[1] + [p[3]]

    def p_column_sort_list_single(self, p):
        'column_sort_list : column_sort'
        self.logger('column_sort -> column_sort_list', p)
        p[0] = [p[1]]

    def p_column_sort_identifier(self, p):
        'column_sort : IDENTIFIER'
        self.logger('IDENTIFIER -> column_sort', p)
        p[0] = (p[1], True)

    def p_column_sort_not_identifier(self, p):
        'column_sort : NOT IDENTIFIER'
        self.logger('NOT IDENTIFIER -> column_sort', p)
        p[0] = (p[2], False)

    def p_error(self, p):
        if p:
            raise SyntaxError(f"Syntax error at token {p.type}: {p.value!r}")
        else:
            raise SyntaxError("Unexpected end of input")

    def parse(self, tokens):
        # PLY's parser.parse() expects an iterable of tokens
        return yacc.parse(lexer=lex.lex(module=ArcLexer()), token_sequence=tokens, debug=self.debug)
