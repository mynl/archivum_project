"""Various utilities for archivum."""

from collections import defaultdict
from functools import partial
import re
import unicodedata

import pandas as pd

from greater_tables import GT


def safeyear(s):
    """
    Safe format of s as a year for greater_tables.

    By default s may be interpreted as a float so str(x) give 2015.0
    which is not wanted. Hence this function is needed.
    """
    try:
        return f'{int(s)}'
    except ValueError:
        if s == '':
            return '9999'
        else:
            return s


fGT = partial(GT, large_ok=True, formatters={'index': str, 'year': safeyear})


def df_to_str(df, tablefmt):
    """Nice prepped df as string for printing."""
    f = fGT(df)
    df = f.df
    dfa = [i[4:] for i in f.df_aligners]
    colw = {c: 15 for c in df.columns}
    for c in ['dir', 'path', 'hash']:
        if c in df:
            colw[c] = min(40, df[c].str.len().max())
    for c in ['name']:
        if c in df:
            colw[c] = min(60, df[c].str.len().max())
        return df.to_markdown(
            index=False,
            colalign=dfa,
            tablefmt=tablefmt,
            maxcolwidths=[colw.get(i) for i in df.columns])


def rinfo(ob):
    """
    Generically export all reasonable data from ob
    store in DataFrame as a ROW and set reasonable data types.

    Non-callable items only. From great2.
    :param ob:
    :param index_attribute:
    :param timestamp: add a timestamp column
    """
    d = {}
    for i in dir(ob):
        if i[0] != '_':
            try:
                a = getattr(ob, i, None)
                if a is not None and not callable(a):
                    d[i] = [a]
            except Exception as e:
                d[i] = 'ERROR:' + str(type(e))
    index = str(type(ob))
    df = pd.DataFrame(d, index=[index])
    df.index.name = 'id'
    return df


def info(x):
    return rinfo(x).T


def remove_accents(s: str) -> str:
    """Remove accents from a string."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )


def accent_mapper_dict(names, verbose=False):
    """Make dict mapper for name -> accented name from list of names."""
    # both versions of the name must be in names
    # not 100% reliable!
    canonical = defaultdict(set)

    for name in names:
        key = remove_accents(name)
        canonical[key].add(name)
    if verbose:
        mapper = {k: sorted(v) for k, v in canonical.items() if len(v) > 1}
    else:
        mapper = {k: sorted(v)[-1] for k, v in canonical.items() if len(v) > 1}
    return mapper


def suggest_filename(s):
    """Clean file name for windows."""
    pass


class KeyAllocator:    # noqa

    def __init__(self, existing: set[str]):
        """Class to determine the next key (@AuthorYYYY) given a list of existing keys."""
        self.existing = set(existing)
        self.pattern = re.compile(r'^([A-Za-z][^0-9]+)(\d{4})([a-z]?)$')
        self.allocators = defaultdict(self._make_iter)

    def _make_iter(self):
        def gen():
            yield ''  # first without suffix
            for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ':
                yield c
        return gen()

    def next_key(self, tag) -> str:
        """Return the next available tag matching the input tag = NameYYYY format."""
        # name: str, year: int
        m = self.pattern.match(tag)
        try:
            name = m[1]
            year = m[2]
        except TypeError:
            # m - none, no match found
            return tag
        else:
            base = f"{name}{year}"
            it = self.allocators[(name, year)]
            while True:
                suffix = next(it)
                candidate = base + suffix
                if candidate not in self.existing:
                    self.existing.add(candidate)
                    return candidate

    __call__ = next_key
