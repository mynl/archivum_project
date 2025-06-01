"""Script experiments."""

from collections import namedtuple
from datetime import datetime, timedelta
from io import StringIO
import random
from random import choice, randint, uniform, sample

import re
from textwrap import wrap


from faker import Faker
import pandas as pd
import numpy as np

# Gemini



def analysis(f):
    """look at the text repr"""
    s = str(f)
    ans = list(map(len, s.split('\n')))
    if min(ans) != max(ans):
        print('Weird, cols not all the same size', min(ans), max(ans))
    print('Actual text table width by row  ', min(ans))
    ans = []
    s1 = s.split('\n')[1]
    print('top row                         ', s1)
    for i, c in enumerate(s1):
        # print(i,c)
        if c in ('│', '┃'):
            ans.append(i)
    ans = np.diff(np.array(ans))
    print('actual widths by column         ', ans)
    print('acutal total width              ', ans.sum() + 1)
    dcw = getattr(f, '_debug_col_widths', None)
    if dcw is not None:
        ans = np.array(ans)
        ans = ans - 4
        dcw['actual'] = list(ans) + [ans.sum() + 1]
        try:
            display(dcw.astype(int))
        except:
            display(dcw)
    print('formatted output')
    print(s)
