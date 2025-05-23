"""Various utilities for archivum."""

from collections import defaultdict
import re
import unicodedata

import pandas as pd


def rinfo(ob):
    """
    Generically export all reasonable data from ob
    store in DataFrame as a ROW and set reasonable data types

    non-callable items only
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


class KeyAllocator:

    def __init__(self, existing: set[str]):
        """Class to determine the next key (@AuthorYYYY) given a list of existing keys."""
        self.existing = set(existing)
        self.pattern = re.compile(r'^([A-Z][^0-9]+)(\d{4})([a-z]?)$')
        self.allocators = defaultdict(self._make_iter)

    def _make_iter(self):
        def gen():
            yield ''  # first without suffix
            for c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ':
                yield c
        return gen()

    def next_key(self, tag) -> str:
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
