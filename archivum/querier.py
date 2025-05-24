"""Query archivum Library database."""
# derived from file-database

import re

import pandas as pd

from . parser import parser, ArcLexer, ArcParser


def query_ex(df: pd.DataFrame, expr: str) -> pd.DataFrame:
    """
    Run extended query parser.

    Supports optional 'top N' prefix, regex with '~', and pandas query().
    Also supports 'sort by col1, col2' at the end.

    Query parser: supports

        optional 'top N' prefix,
        regex with '~', and
        pandas query().

    Examples:
    "top 50 name ~ 'report' and suffix == 'csv'"
    "path ~ 'archive/2023' and mod > '2024-01'"
    "top 10 mod > '2024-03-01'"
    ""  # returns all rows

    Do not need quotes around dates?

    Ordering
        verbose: turns on verbose mode to debug how query is parsed
        recent : automatically sort by mod date
        top n  : top n resutls only
        select comma sep list of fields : in addition to the default ones
        regex clauses: separated by and, regex does not need to be quoted
        sql clause: sent directly to df.query, string literals must be quoted
        order|sort by

    OR in place of regex and sql clauses just ! *.py
    (for a regex applied to the name just name ~ regex)

    dates and sql: stunningly, mod.dt.day == 13 works: modified on the 13th!

    an empty string returns all rows.

    Piping output handled by the cli.

    Look aheads: ^(?!.*[ae]+).*' matches names with no a or e...

    Always case insenstive...TODO: !!

    """
    # default returned columns
    base_cols = ['tag', 'year', 'type', 'author', 'title']

    df = df.copy()
    expr = expr.strip()
    # specification dictionary from query string
    try:
        spec = parser(expr, debug=False)
    except ValueError as e:
        print(e)
        raise e

    print(spec)

    # default values
    flags = spec['flags']
    recent = flags.get('recent', False)
    verbose = flags.get('verbose', False)
    hardlinks = flags.get('hardlinks', False)
    duplicates = flags.get('duplicates', False)

    top_n = spec['top']
    regex_filters = spec['regexes']
    query_expr = spec['where']
    include_cols = spec['select'].get('include', [])
    exclude_cols = spec['select'].get('exclude', [])

    # sort spec
    sort_cols = [i[0] for i in spec['sort']]
    sort_order = [i[1] for i in spec['sort']]

    # TODO - catch errors!!
    if query_expr:
        df = df.query(query_expr)

    # Apply regex filters
    for field, pattern in regex_filters:
        if field in df.columns:
            try:
                df = df[df[field].astype(str).str.contains(
                    pattern, regex=True, case=False, na=False)]
            except re.error:
                print(f'Regular expression error with {pattern}...ignoring.')
        else:
            raise ValueError(f"Unknown field for regex filtering: '{field}'")

    # Sort
    if recent:
        df = df.sort_values(by='year', ascending=False)
    elif sort_cols:
        df = df.sort_values(by=sort_cols, ascending=sort_order)

    # if duplicates:
    #     df = df.loc[df.duplicated("hash", keep=False)]
    #     df['n'] = df['hash'].map(df['hash'].value_counts().get)
    # elif hardlinks:
    #     df = df.loc[df.duplicated("node", keep=False)]
    #     df['n'] = df['node'].map(df['node'].value_counts().get)

    # Top N
    unrestricted_len = len(df)
    if top_n > 0:
        # -1 is all rows, the default
        df = df.head(top_n)

    # prune fields
    # base cols plus select
    fields = [i for i in base_cols if i in df.columns] + [
        i for i in include_cols if i in df.columns]
    # drop out the drop cols
    if exclude_cols:
        fields = [i for i in fields if i not in exclude_cols]
    if duplicates or hardlinks:
        fields.insert(0, 'n')
    if recent and 'year' not in fields:
        fields.insert(0, 'year')
    print(fields)
    df = df[fields]
    if 'title' in fields:
        df.title = df.title.str.replace(r'\{|\}', '', regex=True)
    if 'tag' in fields:
        df = df.set_index('tag')
    return df, unrestricted_len


def _parse_sort_fields(spec: str):
    """Parse comma sep list of field names with optional -|! prefix into list and list of ascending."""
    fields = []
    orders = []
    for field in spec.split(','):
        field = field.strip()
        if field.startswith('-'):
            fields.append(field[1:])
            orders.append(False)
        else:
            fields.append(field)
            orders.append(True)
    return fields, orders


def query_help():
    return """
Help on querex function
=======================

Query syntax
------------
All rows optional. Must be in order shown.
An empty query returns all rows.

verbose
recent
top n
select (!|-)field1[, fields]
regex [and regex]
sql
order|sort by [-]field[, fields]

* if recent an age column is added
* select field, prefix by ! or - to drop a base column, eg select !dir drops (long) directory
* regex not quoted,  ! ~ something or field ~ something
* sql clause quoted for passing to df.query
* -field is descending order, else ascending.

Query return fields
-------------------
name
dir
mod
size
suffix
...plus selected columns

Database fields
---------------
name
dir
drive
path
mod
create
node
size,
suffix
vol_serial
drive_model
drive_serial
hash

"""
