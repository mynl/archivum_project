"""My version of Gemini's version of lark parser."""

from pathlib import Path
from pprint import pprint
import re

from lark import Lark, Transformer, v_args, Tree

# choice has minimal impact on speed for test cases
PARSER = 'lalr'
PARSER = 'earley'
GRAMMAR_FILE = Path(__file__).parent / "arc_grammar.lark"


def parse_test(qno, text, debug=False, show_tokens=False):
    """Convenience to test the grammar, run with a test text."""
    rt = repr(text)
    lt = max(80, len(rt) + 12)
    print('\n' + '=' * lt)
    print(f'QUERY {qno}:' + repr(text))
    print('=' * lt)

    parser = ArcParser(debug=debug)
    try:
        tree = parser.parse(text)
        if show_tokens:
            print("Tokens\n======")
            for token in Lark(ArcParser.grammar, start='query', parser=PARSER, lexer='auto', debug=True).lex(text):
                print(f"  {token.type:<15} {token.value!r}")
            # print('-' * 80)
        result = ArcTransformer().transform(tree)
        if debug:
            print("\nParsed AST")
            print("==========\n")
            print(tree.pretty().strip())
            # print('-' * 80)
        print("\nTransformed result query spec")
        print("=============================\n")
        pprint(result)
    except Exception as e:
        raise ValueError(f'Parsing Error: {e}')
    else:
        return result


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

    # EBNF grammar definition for Lark in arc_grammar.lark
    def __init__(self, debug=False):   # noqa
        self.debug = debug
        self.lark_parser = Lark.open(GRAMMAR_FILE,
                                     start='query',
                                     parser=PARSER,
                                     lexer='auto',
                                     debug=debug)

    def parse(self, text):   # noqa
        return self.lark_parser.parse(text)


# Decorator affects the signatures of the methods: rather than
# in a list they are "spread out" in a vector
@v_args(inline=True)
class ArcTransformer(Transformer):
    """Transforms Lark parse tree into desired dictionary structure."""

    def __init__(self):   # noqa
        super().__init__()
        self.spec = {
            'select': {'include': [], 'exclude': []},
            'sort': [],
            'regex': [],
            'where': None,
            'top': -1,
            'flags': []
        }

    def parse(self, text):   # noqa
        print(f'calling parse with {text = }')
        if not text:
            text = ''
        return self.lark_parser.parse(text)

    def empty(self):   # noqa
        return self.spec

    def query(self, clause_list):   # noqa
        # The clause_list is already processed by its own transformer rule
        # No specific action here beyond returning the accumulated spec
        return self.spec

    def clause_list(self, *clauses):   # noqa
        # print(f'clause_list passed {clauses = }')
        for clause in clauses:
            if isinstance(clause, Tree):
                clause_type, value = clause.children[0]
            else:
                clause_type, value = clause
            if clause_type == 'select':
                self.spec['select']['include'].extend(value['include'])
                self.spec['select']['exclude'].extend(value['exclude'])
            elif clause_type == 'sort':
                self.spec['sort'].extend(value)
            elif clause_type == 'regex':
                self.spec['regex'].extend(value)
            elif clause_type == 'where':
                # For 'where', we expect only one, so replace or combine if logic allows.
                # Here, assuming the last 'where' clause overrides or they are 'AND'ed
                # This needs careful consideration based on desired semantic.
                # For simplicity, if multiple 'where' clauses, they are ANDed together here.
                if self.spec['where'] is None:
                    self.spec['where'] = value
                else:
                    self.spec['where'] += ' and ' + value  # Simple concatenation
            elif clause_type == 'top':
                self.spec['top'] = value
            elif clause_type == 'flags':
                self.spec['flags'].extend(value)
        return self.spec

    # Clause transformations
    def top_clause(self, _top_token, number_token):   # noqa
        try:
            return 'top', int(number_token.value)
        except ValueError:
            print(f'Error parsing top nn, {number_token.value} not an int, defaulting to top 10')
            return ('top', 10)

    def flags(self, *flags):   # noqa
        return ('flags', flags)
        # return ('flags', {f: True for f in flags})

    def regex_clause(self, regex_list):   # noqa
        return ('regex', regex_list)

    # def select_list(self, *all_items):   # noqa
    #     include = []
    #     exclude = []
    #     for item in all_items:
    #         include.extend(item.get('include', []))
    #         exclude.extend(item.get('exclude', []))
    #     return ('select', {'include': include, 'exclude': exclude})

    def select_clause(self, _select_token, select_list):   # noqa
        return ('select', select_list)

    def where_clause(self, _where_token, where_expr):   # noqa
        return ('where', where_expr)

    def order_by_clause(self, _order_by_token, column_sort_list):   # noqa
        return ('sort', column_sort_list)

    def regexes(self, head, *rest):   # noqa
        # May only be one, hence signature
        # rest is a tuple of ('AND', (field reged), 'and', ()...)
        # return [head] + list(rest)
        return [head] + [r for r in rest if isinstance(r, tuple)]

    def regex_ident(self, ident, _, regex):   # noqa
        return (ident, regex)

    def regex_bang(self, _, regex):   # noqa
        return ('BANG', regex)

    # select_list
    def select_list(self, head_item, *rest_items):   # noqa
        combined_select = {'include': [], 'exclude': []}
        for item in [head_item] + list(rest_items):
            combined_select['include'].extend(item['include'])
            combined_select['exclude'].extend(item['exclude'])
        return combined_select

    def select_include_identifier(self, ident):   # noqa
        return {'include': [ident], 'exclude': []}

    def select_exclude_identifier(self, _, ident):   # noqa
        return {'include': [], 'exclude': [ident]}

    def select_all(self, star_token):   # noqa
        # star_token will be Token('STAR', '*')
        return {'include': ['*'], 'exclude': []}

    def where_clause_list(self, head, *rest):   # noqa
        # rest is list of (AND, clause); rest is optional
        # rest is ["and", <where clause>, "AND" <where2>, ...]
        exprs = [head] + [r for r in rest if r.lower() != 'and']
        return ' and '.join(exprs)

    def where_quoted_string(self, ident, eq_test, value):   # noqa
        """This and like functions have value already set by TOKEN functions."""
        return f'{ident} {eq_test} {value}'

    def where_identifier(self, ident, eq_test, value):   # noqa
        return f'{ident} {eq_test} "{value}"'

    def where_number(self, ident, eq_test, value):   # noqa
        try:
            val_str = float(value)
        except ValueError:
            raise ValueError(f'Invalid number: {value}')
        return f'{ident} {eq_test} {val_str}'

    def where_datetime(self, ident, eq_test, value):   # noqa
        return f'{ident} {eq_test} {value}'

    # column_sort_list
    def column_sort_list(self, head, *rest):   # noqa
        return [head] + list(rest)

    def column_sort_asc(self, ident):   # noqa
        return (ident, True)

    def column_sort_desc(self, _, ident):   # noqa
        return (ident, False)

    # Token transformations (optional, can be handled by parser)
    def NUMBER(self, n):   # noqa
        return n

    def QUOTED_STRING(self, s):   # noqa
        """leave quote in, no point taking it out to put it in again!."""
        return s

    def REGEX_SLASHED(self, r):   # noqa
        """Trailing / is NO LONGER optional at end of string."""
        return r[1:-1]   # if r.endswith('/') else r[1:]

    def IDENTIFIER(self, i):   # noqa
        return i.value

    def DATETIME(self, d):   # noqa
        return d.value

    # For keywords, just return their string value or leave them to be ignored by rules
    def __default_token__(self, token):   # noqa
        return token.value

    # def TOP(self, t):
    #     return t.value

    # def SELECT(self, t):
    #     return t.value

    # def WHERE(self, t):
    #     return t.value

    # def ORDER_BY(self, t):
    #     return t.value

    # def AND(self, t):
    #     return t.value

    # def NOT(self, t):
    #     return t.value

    # def EQ_TEST(self, t):
    #     return t.value

    # def TILDE(self, t):
    #     return t.value

    # def BANG(self, t):
    #     return t.value

    # def STAR(self, t):
    #     return t.value
