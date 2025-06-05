"""
Query with pandas query-regex-SQL syntax


Derived from file-database query.py.
"""

import re

import pandas as pd

from . parser import parser


def querex_work(df: pd.DataFrame,
                expr: str,
                base_cols: list,
                bang_field: str,
                recent_field: str,
                debug=False) -> pd.DataFrame:
    """
    Run extended query parser.

    Usage: attach to an existing dataframe df as a method with base_cols set appropriately:

        from types import MethodType
        from functools import partial
        base_cols_for_df = ['tag', 'year', 'type', 'author', 'title']
        querex = partial(querex_work, base_cols=base_cols, debug=False)
        df.querex = MethodType(querex, df)

    MethodType means the df is passed in as the first parameter.

    :param df: the dataframe to query
    :param base_cols: list of columns to return by default, use select a, -b to adjust
    :bang_field: column that ! expands to in regex queries
    :recent_field: date column to use for recent queries


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

    Drops empty columns.

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
    df = df.copy()
    expr = expr.strip()
    # specification dictionary from query string
    try:
        spec = parser(expr, debug=False)
    except ValueError as e:
        print(e)
        raise e

    if debug:
        print(spec)

    # default values
    flags = spec['flags']
    recent = 'recent' in flags
    verbose = 'verbose' in flags

    top_n = spec['top']
    regex_filters = spec['regex']
    query_expr = spec['where']
    include_cols = spec['select'].get('include', [])
    if include_cols and include_cols[0] == '*':
        include_cols = list(df.columns)
    exclude_cols = spec['select'].get('exclude', [])

    # sort spec
    sort_cols = [i[0] for i in spec['sort']]
    sort_order = [i[1] for i in spec['sort']]

    # TODO - catch errors!!
    if query_expr:
        df = df.query(query_expr)

    # Apply regex filters
    for field, pattern in regex_filters:
        if field == 'BANG':
            field = bang_field
        if field in df.columns:
            try:
                df = df.loc[df[field].astype(str).str.contains(
                    pattern, regex=True, case=False, na=False)]
            except re.error:
                print(f'Regular expression error with {pattern}...ignoring.')
        else:
            raise ValueError(f"Unknown field for regex filtering: '{field}'")

    # Sort
    if recent:
        df = df.sort_values(by=recent_field, ascending=False)
    elif sort_cols:
        df = df.sort_values(by=sort_cols, ascending=sort_order)

    # if duplicates:
    #     df = df.loc[df.duplicated("hash", keep=False)]
    #     df['n'] = df['hash'].map(df['hash'].value_counts().get)
    # elif hardlinks:
    #     df = df.loc[df.duplicated("node", keep=False)]
    #     df['n'] = df['node'].map(df['node'].value_counts().get)

    # Top N and GT caption support, note df changes if top n...
    qx_unrestricted_len = len(df)
    if top_n > 0:
        # -1 is all rows, the default
        df = df.head(top_n)
    # prune fields
    # base cols plus select
    # do in two steps to avoid duplicating fields
    fields = [i for i in base_cols if i in df.columns]
    fields = fields + [
        i for i in include_cols if i in df.columns and i not in fields]
    # drop out the drop cols
    if exclude_cols:
        fields = [i for i in fields if i not in exclude_cols]
    if recent and recent_field not in fields:
        fields.insert(0, recent_field)
    df = df[fields]
    if 'title' in fields:
        df['title'] = df['title'].replace(r'\{|\}', '', regex=True)
    # if 'tag' in fields:
    #     df = df.set_index('tag')
    # drop empty columns. which means value '' in every column
    # Drop all columns where every entry is an empty string ('')
    # Step-by-step:
    # (df == '')       → DataFrame of booleans where each cell is True if it equals ''
    # .all()           → For each column, check if all values are True (i.e., all are '')
    # ~(...)           → Invert the boolean Series: True becomes False (keep these columns)
    # df.loc[:, ...]   → Select only the columns that are not all empty strings
    df = df.loc[:, ~(df == '').all()]

    # apply decorations last thing before returning
    df.qx_unrestricted_len = qx_unrestricted_len
    df.gt_caption = f'Query: {expr}, {df.qx_unrestricted_len}'
    if top_n > 0:
        df.gt_caption += f', showing top {top_n} of {qx_unrestricted_len} rows returned.'
    else:
        df.gt_caption += f', {qx_unrestricted_len} rows returned.'
    return df


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


def querex_help():
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
