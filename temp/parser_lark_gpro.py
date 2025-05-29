from pathlib import Path
from pprint import pprint
from lark import Lark, Transformer, v_args, Token

# Grammar would be loaded from arc_grammar.lark
# For this example, I'll embed it as a string, but using a .lark file is recommended.
grammar_text = r"""
?start: program

program: clause* -> build_spec

clause: top_clause
      | flags_clause
      | regexes_clause
      | select_clause
      | where_clause_main  // Renamed from 'clause' in SLY to avoid non-terminal name clash
      | order_by_clause

top_clause: TOP NUMBER
flags_clause: flags_list -> process_flags
flags_list: FLAG+
regexes_clause: regex_list -> process_regexes
regex_list: regex (AND regex)*
regex: IDENTIFIER TILDE REGEX_SLASHED   -> regex_col_slashed
     | IDENTIFIER TILDE plain_regex_id -> regex_col_plain
     | BANG REGEX_SLASHED             -> regex_bang_slashed
     | BANG plain_regex_id_bang       -> regex_bang_plain

plain_regex_id: IDENTIFIER
plain_regex_id_bang: IDENTIFIER

select_clause: SELECT select_list
select_list: _select_item_plus -> combine_select_items // Use an intermediate rule for non-empty list
_select_item_plus: select_item ("," select_item)*
select_item: IDENTIFIER                 -> select_identifier
           | NOT IDENTIFIER             -> select_not_identifier
           | STAR                       -> select_star

where_clause_main: WHERE where_clause_expression
where_clause_expression: _where_clause_plus -> join_where_clauses // Use an intermediate rule
_where_clause_plus: where_clause (AND where_clause)*
where_clause: IDENTIFIER EQ_TEST QUOTED_STRING -> where_quoted_string
            | col1:IDENTIFIER EQ_TEST col2:IDENTIFIER -> where_identifier_identifier
            | IDENTIFIER EQ_TEST NUMBER         -> where_number
            | IDENTIFIER EQ_TEST DATETIME       -> where_datetime

order_by_clause: ORDER_BY column_sort_list
column_sort_list: _column_sort_plus // Use an intermediate rule
_column_sort_plus: column_sort ("," column_sort)*
column_sort: IDENTIFIER        -> sort_identifier_asc
           | NOT IDENTIFIER    -> sort_identifier_desc

// Terminals
TOP: "top"i
SELECT: "select"i
ORDER_BY: "order"i | "sort"i
WHERE: "where"i
AND: "and"i
FLAG: "recent"i | "verbose"i // | "duplicates"i (as per your SLY lexer)

NOT: "-"
EQ_TEST: "==" | "<=" | "<" | ">" | ">="
STAR: "*"
TILDE: "~"
BANG: "!"

DATETIME: /((?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]) (?:[01][0-9]|2[0-3]):[0-5][0-9])|((?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]))|((?:19|20)\d{2}-(?:0[1-9]|1[0-2]))|((?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01]))|((?:[01][0-9]|2[0-3]):[0-5][0-9])/
NUMBER: /-?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:%|[eE][+-]?\d+)?|inf|-inf)/
_QUOTED_STRING_INNER: /(?:[^"'\\]|\\.)*/
QUOTED_STRING: "\"" _QUOTED_STRING_INNER "\"" | "'" _QUOTED_STRING_INNER "'"
_REGEX_SLASHED_INNER: /(?:[^\/\\]|\\.)*/
REGEX_SLASHED: "/" _REGEX_SLASHED_INNER "/" | "/" _REGEX_SLASHED_INNER

IDENTIFIER: /[^\s~=\<\>,!\/][^\s~<>,.]+/ // Added '.' to match SLY's IDENTIFIER a bit more, original was [a-z_][a-z0-9_\.]* this one from SLY [^\s~/!,][^\s~=<>,]*

%import common.WS
%ignore WS
// %ignore /\n+/ // SLY explicitly counted newlines, Lark's WS usually includes \n
"""


class ArcTransformer(Transformer):
    def build_spec(self, items):
        # items[0] is a list of processed clauses from 'clause*'
        clauses = items[0] if items else []
        spec = {
            'select': {},  # SLY default if no SELECT clause
            'sort': [],
            'regexes': [],
            'where': None,
            'top': -1,
            'flags': {}
        }
        for clause_item in clauses:
            if clause_item is None: # Should not happen with current grammar if clauses are always (type,val)
                continue
            clause_type, clause_value = clause_item
            if clause_type == 'select':
                spec['select'] = clause_value
            elif clause_type == 'flags':
                spec['flags'].update(clause_value) # flags are merged
            elif clause_type == 'regexes':
                spec['regexes'].extend(clause_value) # regexes are appended to a list
            else:
                spec[clause_type] = clause_value
        return spec

    @v_args(inline=True)
    def top_clause(self, top_tok, num_tok):
        try:
            n = int(num_tok.value)
        except ValueError:
            n = 10
            print('Error parsing top nn, nn not an int') # Consider proper error handling
        return ('top', n)

    @v_args(inline=True)
    def process_flags(self, flag_list_items):
        # flag_list_items is a list of FLAG Tokens from FLAG+
        flags_dict = {}
        for flag_token in flag_list_items: # In SLY, FLAG+ results in a list of tokens
             flags_dict[flag_token.value] = True # Keep original casing as matched by "i"
        return ('flags', flags_dict)

    def flags_list(self, items): # items is list of FLAG tokens
        return items


    @v_args(inline=True)
    def process_regexes(self, regex_list_items):
         # regex_list_items is a list of processed regex tuples
        return ('regexes', regex_list_items)

    def regex_list(self, items): # items is list of processed regex
        return items


    # For IDENTIFIER token
    def plain_regex_id(self, items):
        return items[0] # Pass the token itself

    def plain_regex_id_bang(self, items):
        return items[0] # Pass the token itself


    @v_args(inline=True)
    def regex_col_slashed(self, identifier_tok, tilde_tok, regex_slashed_tok):
        # REGEX_SLASHED terminal now directly gives content for first capturing group
        # Or, if it gives the full /regex/ then strip here.
        # Based on: REGEX_SLASHED: "/" _REGEX_SLASHED_INNER "/"
        # _REGEX_SLASHED_INNER is regex_slashed_tok
        return (identifier_tok.value, regex_slashed_tok.value)


    @v_args(inline=True)
    def regex_col_plain(self, identifier_tok, tilde_tok, plain_regex_identifier_tok):
        return (identifier_tok.value, plain_regex_identifier_tok.value) # p.IDENTIFIER0, p.IDENTIFIER1

    @v_args(inline=True)
    def regex_bang_slashed(self, bang_tok, regex_slashed_tok):
        return ('BANG', regex_slashed_tok.value)

    @v_args(inline=True)
    def regex_bang_plain(self, bang_tok, plain_regex_identifier_tok):
        return ('BANG', plain_regex_identifier_tok.value)


    @v_args(inline=True)
    def select_clause(self, select_tok, select_list_result):
        return ('select', select_list_result)

    # select_list: _select_item_plus -> combine_select_items
    @v_args(inline=True)
    def combine_select_items(self, items_list): # items_list from _select_item_plus
        final_dict = {'include': [], 'exclude': []}
        if items_list: # items_list is the list of dicts from select_item
            for item_dict in items_list:
                final_dict['include'].extend(item_dict.get('include', []))
                final_dict['exclude'].extend(item_dict.get('exclude', []))
        return final_dict

    def _select_item_plus(self, items): # list of select_item results
        return items


    @v_args(inline=True)
    def select_identifier(self, identifier_tok):
        return {'include': [identifier_tok.value], 'exclude': []}

    @v_args(inline=True)
    def select_not_identifier(self, not_tok, identifier_tok):
        return {'include': [], 'exclude': [identifier_tok.value]}

    @v_args(inline=True)
    def select_star(self, star_tok):
        return {'include': ['*'], 'exclude': []}


    @v_args(inline=True)
    def where_clause_main(self, where_tok, where_expr_result):
        return ('where', where_expr_result)

    # where_clause_expression: _where_clause_plus -> join_where_clauses
    @v_args(inline=True)
    def join_where_clauses(self, where_clauses_list): # from _where_clause_plus
        return ' and '.join(where_clauses_list)

    def _where_clause_plus(self, items): # list of where_clause string results
        return items

    @v_args(inline=True)
    def where_quoted_string(self, identifier_tok, eq_test_tok, quoted_string_tok):
        # QUOTED_STRING terminal captures inner content.
        # Based on: QUOTED_STRING: "\"" _QUOTED_STRING_INNER "\""
        # _QUOTED_STRING_INNER is quoted_string_tok
        return f'{identifier_tok.value} {eq_test_tok.value} "{quoted_string_tok.value}"'

    @v_args(inline=True)
    def where_identifier_identifier(self, col1_tok, eq_test_tok, col2_tok):
        return f'{col1_tok.value} {eq_test_tok.value} "{col2_tok.value}"' # SLY quotes the second identifier

    @v_args(inline=True)
    def where_number(self, identifier_tok, eq_test_tok, number_tok):
        return f'{identifier_tok.value} {eq_test_tok.value} {number_tok.value}'

    @v_args(inline=True)
    def where_datetime(self, identifier_tok, eq_test_tok, datetime_tok):
        return f'{identifier_tok.value} {eq_test_tok.value} "{datetime_tok.value}"' # SLY quotes DATETIME


    @v_args(inline=True)
    def order_by_clause(self, order_by_tok, column_sort_list_result):
        return ('sort', column_sort_list_result)

    def column_sort_list(self, items): # items is list from _column_sort_plus
        return items[0] if items else [] # _column_sort_plus now directly gives the list

    def _column_sort_plus(self, items): # list of column_sort results
        return items


    @v_args(inline=True)
    def sort_identifier_asc(self, identifier_tok):
        return (identifier_tok.value, True)

    @v_args(inline=True)
    def sort_identifier_desc(self, not_tok, identifier_tok):
        return (identifier_tok.value, False)

    # Handle SLY's QUOTED_STRING and REGEX_SLASHED value processing:
    # Lark terminals QUOTED_STRING and REGEX_SLASHED are defined to capture only the inner content
    # by using an intermediate private rule for the content e.g. _QUOTED_STRING_INNER
    # So, their .value should be the stripped string.

    # The Token class itself can be passed if needed, access .type and .value
    def IDENTIFIER(self, token): # This is a terminal method if defined
        return token # Pass the token itself, or token.value if only value is needed by rules

    def NUMBER(self, token):
        return token

    def DATETIME(self, token):
        return token

    # For QUOTED_STRING and REGEX_SLASHED, Lark's parser will give the full match.
    # The defined regex captures groups, but Lark by default uses the whole match for the token value.
    # To get the behavior of SLY stripping quotes/slashes, we can either:
    # 1. Use a lexer callback (more complex setup with separate lexer).
    # 2. Process token values in the transformer when they are used (as SLY does, or as I started in regex_col_slashed).
    # 3. Define terminals so their value is the inner part. Lark has a feature for this.
    #    `TERMINAL: "/" REGEXP_CONTENT "/"` -> value of REGEXP_CONTENT.
    #    I've updated QUOTED_STRING and REGEX_SLASHED in the grammar_text to use this pattern.
    #    `QUOTED_STRING: "\"" _QUOTED_STRING_INNER "\"" | "'" _QUOTED_STRING_INNER "'"`
    #    `REGEX_SLASHED: "/" _REGEX_SLASHED_INNER "/" | "/" _REGEX_SLASHED_INNER` (fixed missing end / for non-greedy SLY REGEX_SLASHED)
    #    The transformer methods (like where_quoted_string) then receive _QUOTED_STRING_INNER as the token.
    #    Or, if it passes the full token, we check its type. Let's assume the _INNER tokens are passed to @v_args.
    #    The @v_args(inline=True) on `where_quoted_string` would get `_QUOTED_STRING_INNER` as its `quoted_string_tok` argument.
    #    This simplifies the transformer.

    # This catches any token not explicitly handled by a method above, good for debugging.
    # def __default__(self, data, children, meta):
    #    print(f"Default handler: data={data}, children={children}")
    #    if isinstance(data, str) and len(children) == 1 and isinstance(children[0], Token): # Terminal rule
    #        return children[0]
    #    return children if len(children) == 1 else children # Or some default construction


# --- Parser setup and usage ---
# It's better to load from .lark file for complex grammars
# parser = Lark.open('arc_grammar.lark', parser='lalr', transformer=ArcTransformer(), debug=True)
# For embedded grammar:
arc_parser = Lark(grammar_text, parser='lalr', transformer=ArcTransformer(), debug=False, propagate_positions=False)

def parse_lark(text):
    try:
        result = arc_parser.parse(text)
        return result
    except Exception as e:
        raise ValueError(f'Lark Parsing Error: {e}')

# Example usage (similar to your parse_test)
if __name__ == '__main__':
    test_queries = [
        "TOP 10 SELECT name, size, -date WHERE type == 'file' AND name ~ /.*\.py/ order by -size, date",
        "recent verbose SELECT * WHERE ext == 'txt' order by name",
        "name ~ myregex and ! /anotherpattern/",
        "SELECT path, NOT ext WHERE size > 1024% AND modified >= 2023-01-01 order by -path",
        "SELECT name WHERE name == \"hello world\"",
        "SELECT name WHERE name == 'hello world'",
        "created_at == 2024-05-29 14:30", # Where clause only
        "", # Empty query
        "TOP 50",
        "SELECT name",
        "ORDER_BY -name",
        "path ~ /test/ and content ! unquoted_regex"
    ]

    for query_text in test_queries:
        print(f"Query: {query_text}")
        try:
            parsed_spec = parse_lark(query_text)
            print("Parsed Spec (Lark):")
            pprint(parsed_spec)
        except ValueError as ve:
            print(ve)
        print("-" * 40)
