"""Lexer and parser for reqex-SQL query language for achivum."""
# derived from file-database


from pathlib import Path
from pprint import pprint
import re

from aggregate.sly import Lexer, Parser


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


class ArcLexer(Lexer):
    """Lexer for file database query language."""

    tokens = {
        IDENTIFIER, NUMBER, QUOTED_STRING, REGEX_SLASHED,
        DATETIME, SELECT, ORDER_BY, WHERE, TOP, EQ_TEST, AND,
        FLAG, STAR, NOT, DATETIME, TILDE, BANG
    }

    ignore = ' \t'
    literals = {','}

    # longer more specific matches should come first
    SELECT = 'select|SELECT'
    ORDER_BY = 'order|ORDER|sort|SORT'
    WHERE = 'where|WHERE'
    TOP = 'top|TOP'
    AND = 'and|AND'    # just AND, otherwise into parens, order of ops etc.
    FLAG = 'recent|RECENT|verbose|VERBOSE'  # |duplicates|DUPLICATES'

    # to reverse sort order
    NOT = r'\-'
    EQ_TEST = r'==|<=|<|>|>='
    # DATETIME = r'\d{4}(-\d{2}(-\d{2})?)?(T|\s)?\d{2}:\d{2}(:\d{2})?'
    DATETIME = r'''
(?x)
    # Full date and time: YYYY-MM-DD HH:MM
    (19|20)\d{2}                         # Year
    -(0[1-9]|1[0-2])                     # Month
    -(0[1-9]|[12][0-9]|3[01])           # Day
    \ (?:[01][0-9]|2[0-3]):[0-5][0-9]    # Time HH:MM

  | # Full date: YYYY-MM-DD
    (19|20)\d{2}
    -(0[1-9]|1[0-2])
    -(0[1-9]|[12][0-9]|3[01])

  | # Year and month only: YYYY-MM
    (19|20)\d{2}
    -(0[1-9]|1[0-2])

  | # Month and day only: MM-DD
    (0[1-9]|1[0-2])
    -(0[1-9]|[12][0-9]|3[01])

  | # Time only: HH:MM
    (?:[01][0-9]|2[0-3])
    :[0-5][0-9]
'''

    # r = re.compile(DATETIME)
    # tests = ['2024-05-15 13:45',
    # '2024-05-15',
    # '2024-05',
    # '05-15',
    # '13:45',]
    # for t in tests:
    #     print(r.match(t))

    NUMBER = r'-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf'
    QUOTED_STRING = r'''"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''''
    REGEX_SLASHED = r'/([^/\\]|\\.)*(/|$)'
    # the look ahead here rejects pure strings, so this does not match
    # columns
    # REGEX_UNQUOTED = r'(?![a-zA-Z]+ )[^\s,~/!][^\s,]*'
    # IDENTIFIER = r'[a-z_][a-z0-9_\.]*'
    # REGEX_UNQUOTED = r'NOTHINGEVERMATCHES(?![a-zA-Z]+ )[^\s,~/!][^\s,]*'

    # matches very general, including regexes
    # used for column names, rhs of query, regex etc.
    IDENTIFIER = r'[^\s~/!,][^\s~=<>,]*'

    STAR = r'\*'
    TILDE = r'~'
    BANG = r'!'

    def QUOTED_STRING(self, t):
        t.value = t.value[1:-1]
        return t

    def REGEX_SLASHED(self, t):
        if t.value.endswith('/'):
            t.value = t.value[1:-1]
        else:
            t.value = t.value[1:]
        return t

    @_(r'\n+')
    def newline(self, t):
        self.lineno += t.value.count('\n')

    def error(self, t):
        print(f"Illegal character {t.value[0]!r}")
        self.index += 1


class ArcParser(Parser):
    """Parser for file database query language."""

    # comment out expected_shift_reduce during DEV!
    # expected_shift_reduce = 8
    tokens = ArcLexer.tokens
    # add parser.out.md during debug
    debugfile = None  # 'parser.out.md'

    def __init__(self, debug=False):
        self.debug = debug
        if debug:
            print(f'ArcParser created {debug = }')
        self.enhance_debugfile()

    def enhance_debugfile(self):
        """
        Put links in the parser.out debug file, if DEBUGFILE != ''.

        :param f_out: Path or filename of output. If "" then DEBUGFILE.html used.
        :return:
        """

        if self.debugfile is None:
            return

        f_out = Path(self.debugfile).with_suffix('.html')

        txt = Path(self.debugfile).read_text(encoding='utf-8')
        txt = txt.replace(
            'Grammar:\n', '<h1>Grammar:</h1>\n\n<pre>\n').replace('->', '<-')
        txt = re.sub(
            r'^Rule ([0-9]+)', r'<div id="rule_\1" />Rule \1', txt, flags=re.MULTILINE)
        txt = re.sub(
            r'^state ([0-9]+)$', r'<div id="state_\1" /><b>state \1</b>', txt, flags=re.MULTILINE)
        txt = re.sub(
            r'^    \(([0-9]+)\) ', r'    <a href="#rule_\1">Rule (\1)</a> ', txt, flags=re.MULTILINE)
        txt = re.sub(
            r'go to state ([0-9]+)', r'go to <a href="#state_\1">state (\1)</a>', txt, flags=re.MULTILINE)
        txt = re.sub(
            r'using rule ([0-9]+)', r'using <a href="#rule_\1">rule (\1)</a>', txt, flags=re.MULTILINE)
        txt = re.sub(
            r'in state ([0-9]+)', r'in <a href="#state_\1">state (\1)</a>', txt, flags=re.MULTILINE)

        f_out.write_text(txt + '\n</pre>', encoding='utf-8')

    def logger(self, msg, p, force=False):
        if not force and not self.debug:
            return
        nm = p._namemap
        sl = p._slice
        parts = [f"[{i}] {k}={v(sl, i)!r}" for i, (k, v)
                 in enumerate(nm.items())]
        nl = '\n\t'
        print(f"LOGGER: {msg:20s}\n\t{nl.join(parts)}")

    @_('clause_list')
    def query(self, p):
        self.logger('clause_list -> query', p, force=False)
        # default spec
        spec = {
            'select': {},
            'sort': [],
            'regexes': [],
            'where': None,
            'top': -1,
            'flags': {}
        }
        # update each term
        for clause in p.clause_list:
            spec[clause[0]] = clause[1]
        return spec

    @_('')
    def clause_list(self, p):
        # handle the empty input
        self.logger('none -> clause_list', p)
        return []

    @_('clause_list clause')
    def clause_list(self, p):
        self.logger('clause_list clause -> clause_list', p)
        return p.clause_list + [p.clause]

    @_('clause')
    def clause_list(self, p):
        self.logger('clause -> clause_list', p)
        return [p.clause]

    @_('TOP NUMBER')
    def clause(self, p):
        self.logger('TOP NUMBER -> clause', p)
        try:
            n = int(p.NUMBER)
        except ValueError:
            n = 10
            print('Error parsing top nn, nn not an int')
        return ('top', n)

    @_('flags')
    def clause(self, p):
        self.logger('flags -> clause', p)
        return ('flags', p.flags)

    @_('FLAG flags')
    def flags(self, p):
        self.logger('flags FLAG -> flags', p)
        p.flags.update({p.FLAG: True})
        return p.flags

    @_('FLAG')
    def flags(self, p):
        self.logger('FLAG -> flags', p)
        return {p.FLAG: True}

    @_('regexes')
    def clause(self, p):
        self.logger('regexes -> clause', p)
        return ('regexes', p.regexes)

    @_('regexes AND regex')
    def regexes(self, p):
        self.logger('regexes AND regex -> regexes', p)
        p.regexes.append(p.regex)
        return p.regexes

    @_('regex')
    def regexes(self, p):
        self.logger('regex -> regexes', p)
        return [p.regex]

    @_('IDENTIFIER TILDE REGEX_SLASHED')
    def regex(self, p):
        self.logger('IDENTIFIER TILDE REGEX_SLASHED -> regex', p)
        return (p.IDENTIFIER, p.REGEX_SLASHED)

    @_('IDENTIFIER TILDE IDENTIFIER')
    def regex(self, p):
        # fudge, because eg py is indistinguishable from a column
        # treat the column as a plain text regex
        self.logger('IDENTIFIER TILDE IDENTIFIER -> regex', p)
        return (p.IDENTIFIER0, p.IDENTIFIER1)

    @_('BANG REGEX_SLASHED')
    def regex(self, p):
        self.logger('BANG REGEX_SLASHED -> regex', p)
        return ('BANG', p.REGEX_SLASHED)

    @_('BANG IDENTIFIER')
    def regex(self, p):
        # fudge, because eg py is indistinguishable from a column
        # treat the column as a plain text regex
        self.logger('BANG IDENTIFIER -> regex', p)
        return ('BANG', p.IDENTIFIER)

    @_('SELECT select_list')
    def clause(self, p):
        self.logger('SELECT select_list -> clause', p)
        return ('select', p.select_list)

    @_('select_list "," select_item')
    def select_list(self, p):
        self.logger('select_list "," select_item -> select_list', p)
        for k, v in p.select_item.items():
            p.select_list[k].extend(v)
        return p.select_list

    @_('select_item')
    def select_list(self, p):
        self.logger('select_item -> select_list', p)
        return p.select_item

    @_('IDENTIFIER')
    def select_item(self, p):
        self.logger('IDENTIFIER -> select_item', p)
        return {'include': [p.IDENTIFIER], 'exclude': []}  # ('include', p.IDENTIFIER)

    @_('NOT IDENTIFIER')
    def select_item(self, p):
        self.logger('NOT IDENTIFIER -> select_item', p)
        return {'include': [], 'exclude': [p.IDENTIFIER]}  # ('exclude', p.IDENTIFIER)

    @_('STAR')
    def select_item(self, p):
        self.logger('STAR -> select_item', p)
        return {'include': ['*'], 'exclude': []}  # ('include', '*')

    @_('WHERE where_clause_expression')
    def clause(self, p):
        self.logger('WHERE where_clause_expression -> clause', p)
        return ('where', p.where_clause_expression)

    @_('where_clause_expression AND where_clause')
    def where_clause_expression(self, p):
        self.logger(
            'where_clause_expression AND where_clause -> where_clause_expression', p)
        return p.where_clause_expression + ' and ' + p.where_clause

    @_('where_clause')
    def where_clause_expression(self, p):
        self.logger('where_clause -> where_clause_expression', p)
        return p.where_clause

    @_('IDENTIFIER EQ_TEST QUOTED_STRING')
    def where_clause(self, p):
        self.logger('IDENTIFIER EQ_TEST QUOTED_STRING -> where_clause', p)
        return f'{p.IDENTIFIER} {p.EQ_TEST} "{p.QUOTED_STRING}"'

    @_('IDENTIFIER EQ_TEST IDENTIFIER')
    def where_clause(self, p):
        # avoid quotes
        self.logger('IDENTIFIER EQ_TEST IDENTIFIER -> where_clause', p)
        return f'{p.IDENTIFIER0} {p.EQ_TEST} "{p.IDENTIFIER1}"'

    @_('IDENTIFIER EQ_TEST NUMBER')
    def where_clause(self, p):
        # avoid quotes
        self.logger('IDENTIFIER EQ_TEST NUMBER -> where_clause', p)
        return f'{p.IDENTIFIER} {p.EQ_TEST} {p.NUMBER}'

    @_('IDENTIFIER EQ_TEST DATETIME')
    def where_clause(self, p):
        self.logger('IDENTIFIER EQ_TEST DATETIME -> where_clause', p)
        return f'{p.IDENTIFIER} {p.EQ_TEST} "{p.DATETIME}"'

    @_('ORDER_BY column_sort_list')
    def clause(self, p):
        self.logger('ORDER_BY column_sort_list -> clause', p)
        return ('sort', p.column_sort_list)

    @_('column_sort_list "," column_sort')
    def column_sort_list(self, p):
        self.logger('column_sort_list "," column_sort -> column_sort_list', p)
        return p.column_sort_list + [p.column_sort]

    @_('column_sort')
    def column_sort_list(self, p):
        self.logger('column_sort -> column_sort_list', p)
        return [p.column_sort]

    @_('IDENTIFIER')
    def column_sort(self, p):
        self.logger('IDENTIFIER -> column_sort', p)
        return (p.IDENTIFIER, True)

    @_('NOT IDENTIFIER')
    def column_sort(self, p):
        self.logger('NOT IDENTIFIER -> column_sort', p)
        return (p.IDENTIFIER, False)

    def error(self, p):
        if p:
            raise SyntaxError(f"Syntax error at token {p.type}: {p.value!r}")
        else:
            raise SyntaxError("Unexpected end of input")


def grammar():
    """
    Write the grammar at the top of the file as a docstring

    To work with multi-rules enter them on one line, like so::

        @_('builtin_agg PLUS expr', 'builtin_agg MINUS expr')

    :param add_to_doc: add the grammar to the docstring
    :param save_to_fn: save the grammar to a file
    """

    pout = Path('grammar.md')

    # get the grammar from the top of the file
    txt = Path(__file__).read_text(encoding='utf-8')
    stxt = txt.split('@_')
    ans = {}
    # 3:-3 get rid of junk at top and bottom (could change if file changes)
    for it in stxt[3:-3]:
        if it.find('# def') >= 0:
            # skip rows with a comment between @_ and def
            pass
        else:
            b = it.split('def')
            b0 = b[0].strip()[2:-2]
            # check if multirule
            if ', ' in b0:
                b0 = [i.replace("'", '') for i in b0.split(', ')]
            else:
                b0 = [b0]
            try:
                b1 = b[1].split("(self, p):")[0].strip()
            except:
                logger.error(f'Unexpected multirule behavior {it}')
                exit()
            if b1 in ans:
                ans[b1] += b0
            else:
                ans[b1] = b0
    s = 'GRAMMAR\n=======\n\n'
    for k, v in ans.items():
        s += f'{k:<20s}\t::= {v[0]:<s}\n'
        for rhs in v[1:]:
            s += f'{" "*20}\t | {rhs:<s}\n'
        s += '\n'

    # finally add the language words
    # this is a bit manual, but these shouldnt change much...
    # lang_words = '\n\nlanguage words go here\n\n'
    lang_words = """

TOKENS
======
        IDENTIFIER, NUMBER, QUOTED_STRING, REGEX_SLASHED,
        DATETIME, SELECT, ORDER_BY, WHERE, TOP, EQ_TEST, AND,
        FLAG, STAR, NOT, DATETIME, TILDE, BANG

DEFINITIONS
===========
    SELECT = 'select|SELECT'
    ORDER_BY = 'order|ORDER|sort|SORT'
    WHERE = 'where|WHERE'
    TOP = 'top|TOP'
    AND = 'and|AND'    # just AND, otherwise into parens, order of ops etc.
    FLAG = 'recent|RECENT|verbose|VERBOSE'

    STAR = r'\b\*\b'
    TILDE = r'\b~\b'
    BANG = r'\b!\b'
    # to reverse sort order
    NOT = r'\-'
    EQ_TEST = r'==|<=|<|>|>='
    DATETIME complex regex
    NUMBER = r'-?(\d+(\.\d*)?|\.\d+)(%|[eE][+-]?\d+)?|inf|-inf'
    QUOTED_STRING = r'''"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\''''
    IDENTIFIER = r'[a-z_][a-z0-9_]*'
    REGEX_SLASHED = r'/([^/\\]|\\.)*/'
"""

    s += lang_words
    pout.write_text(s, encoding='utf-8')
    return s
