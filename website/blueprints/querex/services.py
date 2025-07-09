import re
import pandas as pd
from rustfuzz import FuzzyMatcherMulti, FieldAwareFuzzy, FieldAwareFuzzy2
from rapidfuzz import process, fuzz # Used by fuzzy_search (old version)

# These will be initialized once per application lifecycle when the blueprint is registered
# or when the first request comes in that needs them.
# For a multi-threaded server, this might still require careful handling (e.g., locking)
# or initializing them within an application context. For now, keep as module-level.
_rf_matcher = None
_df_search = None # This will store the DataFrame for fuzzy searching



def _initialize_search_data(library_instance):
    global _df_search, _rf_matcher
    if _df_search is None:
        cols = ['tag', 'type', 'author', 'journal', 'title', 'year']
        _df_search = library_instance.ref_df[cols].copy()
        _df_search['search_blob'] = _df_search.fillna('').agg(' '.join, axis=1)
        _rf_matcher = FuzzyMatcherMulti(_df_search['search_blob'].tolist())

def subset(blob, p):
    # take out low quality matches with score below p * max(scores)
    rex = re.compile(r'<td>([0-9]+)</td></tr>')
    ans = [blob[0]]
    mx = rex.search(blob[0])[1]
    threshold = int(mx) * p
    for b in blob[1:]:
        s = rex.search(b)[1]
        if int(s) >= threshold:
            ans.append(b)
        else:
            break
    return ans

def wrap(table_rows):
    # convert to html table
    ans = ['<table>']
    ans.append('''<colgroup>
    <col style="width: 25%;">
    <col style="width: 45%;">
    <col style="width: 20%;">
    <col style="width: 5%;">
    <col style="width: 5%;">
</colgroup>''')

    ans.append('<thead>')
    ans.append('<tr><th>Author</th><th>Title</th><th>Journal</th><th>Year</th><th>Score</th></tr>'
              )
    ans.append('</thead>')
    ans.append('<tbody>')
    ans.extend(table_rows)
    ans.append('</tbody>')
    ans.append('</table>')
    return '\n'.join(ans)

def new_search(query, library):
    global _df_search, _rf_matcher
    top_k = 100
    if _df_search is None:
        df = library.ref_df
        rows = list(zip(
            df.author.fillna(""),
            [i[1:-1] for i in df.title.fillna("")],  # strip out containing {}
            df.journal.fillna(""),
            df.year.fillna("").astype(str),
        ))
        _rf_matcher = FieldAwareFuzzy2(rows)
    table_rows = _rf_matcher.query_html(query, top_k)
    return wrap(subset(table_rows, 0.8))


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
