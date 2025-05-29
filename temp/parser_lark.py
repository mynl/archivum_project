"""Lark-based parser for querex query language."""

from pathlib import Path
from pprint import pprint
from lark import Lark, Transformer, v_args

lark_grammar = r"""
    start: clause_list              -> start
        |                           -> empty

    clause_list: clause_list clause -> merge_clauses
               | clause -> single_clause

    clause: TOP NUMBER              -> top
          | flags                   -> flags
          | regexes                 -> regexes
          | SELECT select_list      -> select
          | WHERE where_expr        -> where
          | ORDER_BY sort_list      -> sort

    flags: FLAG+                    -> flag_list

    regexes: regexes AND regex      -> append_regex
           | regex                  -> single_regex

    regex: IDENTIFIER TILDE REGEX_SLASHED     -> regex_field
         | IDENTIFIER TILDE IDENTIFIER        -> regex_plain
         | BANG REGEX_SLASHED                 -> regex_bang_slash
         | BANG IDENTIFIER                    -> regex_bang_plain

    select_list: select_list "," select_item   -> merge_select
               | select_item                  -> single_select

    select_item: IDENTIFIER         -> select_include
               | NOT IDENTIFIER     -> select_exclude
               | STAR               -> select_star

    where_expr: where_expr AND where_clause   -> merge_where
              | where_clause                 -> single_where

    where_clause: IDENTIFIER EQ_TEST QUOTED_STRING  -> where_quoted
                | IDENTIFIER EQ_TEST IDENTIFIER     -> where_ident
                | IDENTIFIER EQ_TEST NUMBER         -> where_number
                | IDENTIFIER EQ_TEST DATETIME       -> where_datetime

    sort_list: sort_list "," sort_item     -> merge_sort
             | sort_item                -> single_sort

    sort_item: IDENTIFIER             -> sort_asc
             | NOT IDENTIFIER         -> sort_desc

        #: Original GPT
    #: TOP: "top"i
    #: SELECT: "select"i
    #: WHERE: "where"i
    #: ORDER_BY: "order"i | "sort"i
    #: AND: "and"i
    #: FLAG: "recent"i | "verbose"i
    #: STAR: "*"
    #: TILDE: "~"
    #: BANG: "!"
    #: NOT: "-"
    #: EQ_TEST: "==" | "<=" | "<" | ">" | ">="

    #: DATETIME: /\d{4}-\d{2}(-\d{2})?( \d{2}:\d{2})?/
    #: NUMBER: /-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf/
    #: QUOTED_STRING: /"([^"\\]|\\.)*"|'([^'\\]|\\.)*'/
    #: IDENTIFIER: /[^\s~=,!][^\s~=,]*/
    #: REGEX_SLASHED: /\/([^\/\\]|\\.)*(\/|$)/

    #: %import common.WS
    #: %ignore WS

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

    # IDENTIFIER: /[^\s~/\<\>=,!][^\s~=\<\>,]*/

@v_args(inline=True)
class QuerexTransformer(Transformer):
    def start(self, clauses): return clauses
    def empty(self): return []

    def single_clause(self, clause): return [clause]
    def merge_clauses(self, lst, clause): return lst + [clause]

    def top(self, _, n): return ('top', int(n))
    def flag_list(self, *flags): return ('flags', {f.value: True for f in flags})

    def single_regex(self, r): return ('regexes', [r])
    def append_regex(self, lst, _, r): return ('regexes', lst[1] + [r])
    def regex_field(self, field, _, val): return (field.value, val.value)
    def regex_plain(self, field, _, val): return (field.value, val.value)
    def regex_bang_slash(self, _, val): return ('BANG', val.value)
    def regex_bang_plain(self, _, val): return ('BANG', val.value)

    def single_select(self, item): return item

    def merge_select(self, lst, _, item):
        for k, v in item.items():
            lst[k].extend(v)
        return lst

    def select_include(self, name): return {'include': [name.value], 'exclude': []}
    def select_exclude(self, name): return {'include': [], 'exclude': [name.value]}
    def select_star(self, _): return {'include': ['*'], 'exclude': []}

    def single_where(self, clause): return ('where', clause)
    def merge_where(self, w1, _, w2): return ('where', f"{w1[1]} and {w2}")
    def where_quoted(self, col, op, val): return f'{col.value} {op} {val}'
    def where_ident(self, col, op, val): return f'{col.value} {op} \"{val.value}\"'
    def where_number(self, col, op, val): return f'{col.value} {op} {val}'
    def where_datetime(self, col, op, val): return f'{col.value} {op} \"{val}\"'

    def single_sort(self, item): return ('sort', [item])
    def merge_sort(self, lst, _, item): return ('sort', lst[1] + [item])
    def sort_asc(self, field): return (field.value, True)
    def sort_desc(self, _, field): return (field.value, False)


def parse_test(text, debug=False):
    parser = Lark(lark_grammar,
                    parser="lalr",
                    lexer='auto',
                    transformer=QuerexTransformer(),
                    maybe_placeholders=False)
    try:
        result = parser.parse(text)
        if debug:
            print("Parsed result query spec\n==========================\n")
            pprint({k: v for k, v in result})
        return {k: v for k, v in result}
    except Exception as e:
        raise ValueError(f"Parsing Error: {e}")


def parser(text):
    return parse_test(text, debug=False)
