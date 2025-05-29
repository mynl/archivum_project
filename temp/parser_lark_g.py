"""Gemini version of lark parser."""

from pathlib import Path
from pprint import pprint
import re

from lark import Lark, Transformer, v_args


def parse_test(text, debug=False, show_tokens=False):
    """Convenience to test the grammar, run with a test text."""
    parser = ArcParser(debug=debug)
    try:
        tree = parser.parse(text)
        if show_tokens:
            print("Tokens (Lark handles lexing automatically):")
            for token in Lark(ArcParser.grammar, start='query', parser='earley', lexer='standard', debug=True).lex(text):
                 print(f"  {token.type:<10} {token.value!r}")
            print('-' * 80)
        result = ArcTransformer().transform(tree)
        if debug:
            print('-' * 80)
            print("Parsed AST:")
            print("===========\n")
            print(tree.pretty())
            print('-' * 80)
            print("Transformed result query spec")
            print("========================\n")
        pprint(result)
    except Exception as e:
        raise ValueError(f'Parsing Error: {e}')


def parser(text, debug=False):
    """One stop shop parsing."""
    parser = ArcParser(debug=debug)
    result = None
    try:
        tree = parser.parse(text)
        result = ArcTransformer().transform(tree)
    except Exception as e:
        raise ValueError(f'Parsing Error: {e}')
    return result


class ArcParser:
    """Parser for file database query language."""

    # EBNF grammar definition for Lark
    grammar = r"""
    ?query: clause_list

    clause_list: clause* -> multiple_clauses
               | clause   -> single_clause

    clause: TOP NUMBER                         -> top_clause
          | flags                              -> flags_clause
          | regexes                            -> regexes_clause
          | SELECT select_list                 -> select_clause
          | WHERE where_clause_expression      -> where_clause
          | ORDER_BY column_sort_list          -> order_by_clause

    flags: FLAG flags
         | FLAG

    regexes: regex (AND regex)*

    regex: IDENTIFIER TILDE REGEX_SLASHED
         | IDENTIFIER TILDE IDENTIFIER
         | BANG REGEX_SLASHED
         | BANG IDENTIFIER

    select_list: select_item ("," select_item)*

    select_item: IDENTIFIER                     -> select_include_identifier
               | NOT IDENTIFIER                 -> select_exclude_identifier
               | STAR                           -> select_all

    where_clause_expression: where_clause (AND where_clause)*

    where_clause: IDENTIFIER EQ_TEST QUOTED_STRING
                | IDENTIFIER EQ_TEST IDENTIFIER
                | IDENTIFIER EQ_TEST NUMBER
                | IDENTIFIER EQ_TEST DATETIME

    column_sort_list: column_sort ("," column_sort)*

    column_sort: IDENTIFIER
               | NOT IDENTIFIER

    // Tokens
    TOP: "top"i
    SELECT: "select"i
    WHERE: "where"i
    ORDER_BY: ("order"i | "sort"i)
    AND: "and"i
    FLAG: "recent"i | "verbose"i // | "duplicates"i

    NOT: "-"
    EQ_TEST: "==" | "<=" | "<" | ">" | ">="
    TILDE: "~"
    BANG: "!"
    STAR: "*"

    // Complex tokens
    REGEX_SLASHED: /\/[^\/\\]*(\/[^\/\\]*)*\/?/
    QUOTED_STRING: /"[^"\\]*(\\\\.[^"\\]*)*"|'[^'\\]*(\\\\.[^'\\]*)*'/
    NUMBER: /-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf/
    IDENTIFIER: /[^\s~\/!,][^\s~=<>,]*/

    %import common.NEWLINE
    %import common.WS
    %ignore WS
    %ignore NEWLINE

    // Regular expressions for DATETIME
    DATETIME: /(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\s(?:[01][0-9]|2[0-3]):[0-5][0-9]|(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])|(19|20)\d{2}-(0[1-9]|1[0-2])|(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])|(?:[01][0-9]|2[0-3]):[0-5][0-9]/
    """

    def __init__(self, debug=False):
        self.debug = debug
        self.lark_parser = Lark(self.grammar,
            start='query',
            parser='earley',
            lexer='auto',
            debug=debug)


    def parse(self, text):
        return self.lark_parser.parse(text)


@v_args(inline=True)    # Affects the signatures of the methods
class ArcTransformer(Transformer):
    """
    Transforms the Lark parse tree into the desired Python dictionary structure.
    """

    def __init__(self):
        super().__init__()
        self.spec = {
            'select': {'include': [], 'exclude': []},
            'sort': [],
            'regexes': [],
            'where': None,
            'top': -1,
            'flags': {}
        }

    def query(self, clause_list):
        # The clause_list is already processed by its own transformer rule
        # No specific action here beyond returning the accumulated spec
        return self.spec

    def multiple_clauses(self, *clauses):
        for clause_type, value in clauses:
            if clause_type == 'select':
                self.spec['select']['include'].extend(value['include'])
                self.spec['select']['exclude'].extend(value['exclude'])
            elif clause_type == 'sort':
                self.spec['sort'].extend(value)
            elif clause_type == 'regexes':
                self.spec['regexes'].extend(value)
            elif clause_type == 'where':
                # For 'where', we expect only one, so replace or combine if logic allows.
                # Here, assuming the last 'where' clause overrides or they are 'AND'ed
                # This needs careful consideration based on desired semantic.
                # For simplicity, if multiple 'where' clauses, they are ANDed together here.
                if self.spec['where'] is None:
                    self.spec['where'] = value
                else:
                    self.spec['where'] += ' and ' + value # Simple concatenation
            elif clause_type == 'top':
                self.spec['top'] = value
            elif clause_type == 'flags':
                self.spec['flags'].update(value)
        return 'clauses_processed', None # Return a dummy value, actual result is in self.spec

    def single_clause(self, clause_data):
        # This is for a single clause in the input, transform it into the spec.
        clause_type, value = clause_data
        if clause_type == 'select':
            self.spec['select']['include'].extend(value['include'])
            self.spec['select']['exclude'].extend(value['exclude'])
        elif clause_type == 'sort':
            self.spec['sort'].extend(value)
        elif clause_type == 'regexes':
            self.spec['regexes'].extend(value)
        elif clause_type == 'where':
            self.spec['where'] = value
        elif clause_type == 'top':
            self.spec['top'] = value
        elif clause_type == 'flags':
            self.spec['flags'].update(value)
        return 'clause_processed', None

    # Clause transformations
    def top_clause(self, _top_token, number_token):
        try:
            return 'top', int(number_token.value)
        except ValueError:
            print('Error parsing top nn, nn not an int')
            return 'top', 10

    def flags_clause(self, flags_list):
        return 'flags', flags_list

    def regexes_clause(self, regex_list):
        return 'regexes', regex_list

    def select_clause(self, _select_token, select_list):
        return 'select', select_list

    def where_clause(self, _where_token, where_expr):
        return 'where', where_expr

    def order_by_clause(self, _order_by_token, column_sort_list):
        return 'sort', column_sort_list

    # flags
    def flags(self, arg1, arg2=None):
        # Case 1: flags: FLAG (base case)
        print(f'{type(arg1) = }\t{arg1 = }')
        if arg2 is None:
            # arg1 is the FLAG Token
            return {arg1.lower(): True}
        # Case 2: flags: FLAG flags (recursive case)
        else:
            # arg1 is the FLAG Token for the current flag
            # arg2 is the dictionary result from the recursive 'flags' call
            existing_flags = arg2
            existing_flags[arg1.lower()] = True
            return existing_flags

    # regexes
    def regexes(self, head_regex, *rest_regexes):
        return [head_regex] + list(rest_regexes)

    def regex(self, *args):
        if len(args) == 3: # IDENTIFIER TILDE REGEX_SLASHED or IDENTIFIER TILDE IDENTIFIER
            return (args[0].value, args[2].value)
        elif len(args) == 2: # BANG REGEX_SLASHED or BANG IDENTIFIER
            return ('BANG', args[1].value)

    # select_list
    def select_list(self, head_item, *rest_items):
        combined_select = {'include': [], 'exclude': []}
        for item in [head_item] + list(rest_items):
            combined_select['include'].extend(item['include'])
            combined_select['exclude'].extend(item['exclude'])
        return combined_select

    # def select_item(self, token):
    #     print(f'{type(token) = }\t{token = }')
    #     if token.type == 'IDENTIFIER':
    #         return {'include': [token.value], 'exclude': []}
    #     elif token.type == 'STAR':
    #         return {'include': ['*'], 'exclude': []}
    #     elif token.type == 'NOT': # This handles the NOT IDENTIFIER case
    #         return {'include': [], 'exclude': [token.value]}
    def select_include_identifier(self, ident_token):
        # ident_token will be a Token (e.g., Token('IDENTIFIER', 'name'))
        return {'include': [ident_token.value], 'exclude': []}

    def select_exclude_identifier(self, not_token, ident_token):
        # not_token will be Token('NOT', '-')
        # ident_token will be Token('IDENTIFIER', 'column_name')
        return {'include': [], 'exclude': [ident_token.value]}

    def select_all(self, star_token):
        # star_token will be Token('STAR', '*')
        return {'include': ['*'], 'exclude': []}

    # where_clause_expression
    def where_clause_expression(self, head_where, *rest_where):
        result = head_where
        for w in rest_where:
            result += ' and ' + w
        return result

    def where_clause(self, ident, eq_test, value_token):
        val_str = value_token.value
        if value_token.type == 'QUOTED_STRING' or value_token.type == 'DATETIME':
            val_str = f'"{val_str}"'
        elif value_token.type == 'IDENTIFIER': # Treat identifiers on RHS as strings
             val_str = f'"{val_str}"'

        return f'{ident.value} {eq_test.value} {val_str}'

    # column_sort_list
    def column_sort_list(self, head_sort, *rest_sort):
        return [head_sort] + list(rest_sort)

    def column_sort(self, *args):
        if len(args) == 1: # IDENTIFIER
            return (args[0].value, True)
        elif len(args) == 2: # NOT IDENTIFIER
            return (args[1].value, False)

    # Token transformations (optional, can be handled by parser)
    NUMBER = lambda self, n: n
    QUOTED_STRING = lambda self, s: s[1:-1] if s.startswith('"') or s.startswith("'") else s
    REGEX_SLASHED = lambda self, r: r[1:-1] if r.endswith('/') else r[1:]
    IDENTIFIER = lambda self, i: i.value
    DATETIME = lambda self, d: d.value
    # For keywords, just return their string value or leave them to be ignored by rules
    TOP = lambda self, t: t.value
    SELECT = lambda self, t: t.value
    WHERE = lambda self, t: t.value
    ORDER_BY = lambda self, t: t.value
    AND = lambda self, t: t.value
    FLAG = lambda self, t: t.value
    NOT = lambda self, t: t.value
    EQ_TEST = lambda self, t: t.value
    TILDE = lambda self, t: t.value
    BANG = lambda self, t: t.value
    STAR = lambda self, t: t.value
