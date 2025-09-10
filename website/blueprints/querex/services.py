from functools import partial
import re
import pandas as pd

from rustfuzz import FuzzyMatcherMulti  # , FieldAwareFuzzy2 # , FieldAwareFuzzy
from rapidfuzz import process, fuzz # Used by fuzzy_search (old version)
from greater_tables import GT


fGT = partial(GT, show_index=False, year_cols='year')

# These will be initialized once per application lifecycle when the blueprint is registered
# or when the first request comes in that needs them.
# For a multi-threaded server, this might still require careful handling (e.g., locking)
# or initializing them within an application context. For now, keep as module-level.
_rf_matcher = None
_df_search = None # This will store the DataFrame for fuzzy searching



def _initialize_search_data(library_instance):
    global _df_search, _rf_matcher
    if _df_search is None:
        cols = ['tag', 'type', 'author', 'journal', 'title', 'path', 'year']
        _df_search = library_instance.database[cols].copy()
        _df_search['search_blob'] = _df_search.drop(columns='path').fillna('').agg(' '.join, axis=1)
        _rf_matcher = FuzzyMatcherMulti(_df_search['search_blob'].tolist())


def new_search(query, library):
    global _df_search, _rf_matcher
    top_k = 25
    if _df_search is None:
        _initialize_search_data(library)
    table_rows, scores = _rf_matcher.query(query, top_k)
    bit = _df_search.loc[table_rows, ['tag', 'author', 'title', 'year', 'journal']]
    paths = _df_search.loc[table_rows, 'path']
    bit.title = bit.title.str[1:-1]
    # page served from \s\library so strip off 10 chars
    bit.title = [f'<a href="http://192.168.4.43:19777{p[10:]}" target="_blank">{t}</a>' for t, p in zip(bit.title, paths)]
    bit['score'] = scores
    bit = bit.groupby('title').apply(lambda x: x.head(1))
    bit = bit.sort_values('score', ascending=False)
    f = fGT(bit)
    return f.html

def perform_rfuzz_search(query: str, library_instance) -> pd.DataFrame:
    _initialize_search_data(library_instance)

    idx, score = _rf_matcher.query(query, 250)
    good_score = score[0] * 0.75
    ans = _df_search.loc[idx].copy() # Use .copy() to avoid SettingWithCopyWarning
    ans['score'] = score
    ans = ans.drop(columns=['search_blob', 'tag', 'type']).query('score > @good_score')
    ans = ans.reset_index(drop=False)
    ans.index.name = 'n'
    ans = ans[['author', 'title', 'year', 'journal', 'score', 'index']]
    return ans

# Keep the original fuzzy_search if it's still needed, but your querex route uses rfuzz
def fuzzy_search(df: pd.DataFrame, query: str, top_k=50) -> pd.DataFrame:
    choices = df["search_blob"].tolist()
    matches = process.extract(
        query,
        choices,
        scorer=fuzz.token_set_ratio,
        limit=top_k
    )
    indices = [match[2] for match in matches]
    scores = [match[1] for match in matches]
    result_df = df.iloc[indices].copy()
    result_df.insert(0, "match_score", scores)
    return result_df
