"""
Finding duplicates, enhancing records, etc.
"""
from collections import namedtuple
import json
import logging
from difflib import SequenceMatcher
from functools import reduce
from typing import Dict, List, Any, Callable, Tuple, Optional
from pathlib import Path
import re

import fitz  # PyMuPDF
import networkx as nx
import numpy as np
import pandas as pd
from rapidfuzz import process, fuzz
from tqdm import tqdm

from .config import Configurator

logger = logging.getLogger(__name__)

# --- Data Structures ---
# The container for all results of the enhancement process
Ans = namedtuple("Ans", [
    "ans_df",           # The final cleaned Reference DataFrame
    "ref_doc_df",       # The updated Ref-Doc mapping (with preferred flag)
    "work_df",          # Intermediate DF with cluster/source IDs
    "dropped_df",       # Rows removed during deduplication
    "pairs_df",         # The raw duplicates pairs found
    "cluster_id_map",   # Dict[tag -> cluster_id]
    "source_id_map",    # Dict[tag -> survivor_tag]
    "title_map",        # Dict[tag -> best_title]
    "G"                 # The NetworkX graph object
])

# =================REFERENCES=========================
# --- 1. Strategy Registry ---
def strategy_longest(series: pd.Series) -> Any:
    """Returns the longest string value."""
    valid = series[series.notna() & (series != '')].astype(str)
    if valid.empty: return None
    return valid.loc[valid.str.len().idxmax()]


def strategy_mode(series: pd.Series) -> Any:
    """Returns the most frequent value."""
    valid = series.dropna()
    valid = valid[valid != '']
    if valid.empty: return None
    return valid.mode().iloc[0]


def strategy_coalesce(series: pd.Series) -> Any:
    """Returns the first non-null value (Survivor bias)."""
    valid = series.dropna()
    valid = valid[valid != '']
    if valid.empty: return None
    return valid.iloc[0]


def strategy_first_valid_doi(series: pd.Series) -> Any:
    """Returns the first value resembling a valid DOI."""
    # Simplified DOI regex
    doi_pattern = r'^10\.\d{4,9}/[-._;()/:A-Z0-9]+$'
    clean = series.astype(str).str.replace(r'^doi:\s*', '', regex=True, flags=re.I).str.strip()
    for val in clean:
        if re.match(doi_pattern, val, re.I):
            return val
    return None


# used to map string function names, from config, to the actual functions
STRATEGY_REGISTRY: Dict[str, Callable] = {
    'longest': strategy_longest,
    'mode': strategy_mode,
    'coalesce': strategy_coalesce,
    'doi_check': strategy_first_valid_doi
}


# --- 2. String Analysis Logic (Safety & Beauty) ---
def analyze_diff(t1: str, t2: str) -> str:
    """
    Returns 'DISTINCT' if difference involves numbers/keywords.
    Returns 'SAFE' if difference is just punctuation/typos.
    """
    if t1 == t2: return "SAFE"

    matcher = SequenceMatcher(None, t1, t2)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal': continue

        diff_text = t1[i1:i2] + " " + t2[j1:j2]

        # Guards
        if re.search(r'\d', diff_text): return "DISTINCT"
        if re.search(r'\b(I|II|III|IV|V|VI|VII|VIII|IX|X)\b', diff_text): return "DISTINCT"
        if re.search(r'\b(Vol|Volume|Part|Pt)\b', diff_text, re.IGNORECASE): return "DISTINCT"

    return "SAFE"


def pick_best_title(t1: str, t2: str) -> str:
    """Deterministically selects the higher quality title."""
    bad_endings = ('.', ',', ';', ':')

    # 1. Brace Protection
    t1_braced = t1.strip().startswith('{') and t1.strip().endswith('}')
    t2_braced = t2.strip().startswith('{') and t2.strip().endswith('}')
    if t1_braced and not t2_braced: return t1
    if t2_braced and not t1_braced: return t2

    # both braced or unbraced

    # 2. Clean Edge
    t1_clean = not t1.strip().endswith(bad_endings)
    t2_clean = not t2.strip().endswith(bad_endings)
    if t1_clean and not t2_clean: return t1
    if t2_clean and not t1_clean: return t2

    # 3. Academic Standard (: vs -)
    t1_has_colon = ': ' in t1
    t2_has_colon = ': ' in t2
    t1_has_dash = ' - ' in t1
    t2_has_dash = ' - ' in t2
    if t1_has_colon and t2_has_dash: return t1
    if t2_has_colon and t1_has_dash: return t2

    # 4. Info Density (Internal Hyphens)
    c1_hyphens = t1.count('-')
    c2_hyphens = t2.count('-')
    if c1_hyphens > c2_hyphens and not t1_has_dash: return t1
    if c2_hyphens > c1_hyphens and not t2_has_dash: return t2

    # 5. Casing
    def get_cap_score(s):
        if s.isupper() and len(s) > 4: return -1
        return sum(1 for c in s if c.isupper())

    if get_cap_score(t1) > get_cap_score(t2): return t1
    if get_cap_score(t2) > get_cap_score(t1): return t2

    # 6. Length Tie-Breaker
    return t1 if len(t1) >= len(t2) else t2


# --- 3. Duplicate Identification (Blocking & Matching) ---
def find_tag_duplicates(ref_df: pd.DataFrame, config: Configurator) -> pd.DataFrame:
    """
    Identifies potential duplicate references within 'AuthorYYYY' blocks.

    Creates pairs_df with columns base_tag, tag_1, tag_2, score_mean.

    Possible duplicates must have mean score >= config.enhancement_cutoff_score
    """
    df_block = ref_df.copy()
    df_block['base_tag'] = df_block['tag'].str.replace(config.enhancement_tag_regex, '', regex=True)

    results = []
    grouped = df_block.groupby('base_tag')

    for base_tag, group in grouped:
        if len(group) < 2: continue

        titles = group['title'].tolist()
        tags = group['tag'].tolist()
        n = len(titles)

        # 3 Scorer Ensemble
        m_ratio = process.cdist(titles, titles, scorer=fuzz.ratio, workers=1)
        m_token = process.cdist(titles, titles, scorer=fuzz.token_sort_ratio, workers=1)
        m_partial = process.cdist(titles, titles, scorer=fuzz.partial_ratio, workers=1)

        m_final = (m_ratio + m_token + m_partial) / 3.0

        rows, cols = np.triu_indices(n, k=1)
        mask = m_final[rows, cols] >= config.enhancement_cutoff_score

        for r, c in zip(rows[mask], cols[mask]):
            results.append({
                'base_tag': base_tag,
                'tag_1': tags[r],
                'tag_2': tags[c],
                'score_mean': round(m_final[r, c], 2)
            })

    if not results:
        return pd.DataFrame(columns=['tag_1', 'tag_2', 'score_mean'])
    return pd.DataFrame(results)


# --- 4. Cluster Resolution & Merging ---
def resolve_cluster_titles(cluster_tags: list, ref_map: dict) -> List[dict]:
    """
    Sub-clusters a raw list into safe groups and picks a Title Winner for each.

    For example, if the cluster is Analysis I and Analysis II that is split into
    two. If it is Analysis and Analysis. that is one cluster with title Analysis.

    cluster_tags = tags of members of the cluster
    ref_map = tags to title

    returns a list of dicts {members, source_id, mapped_title}
    """
    titles = [ref_map[t] for t in cluster_tags]
    sub_groups: List[List[Tuple[str, str]]] = [] # List of lists of (tag, title)

    # A. Safety Check (Sub-clustering)
    for tag, title in zip(cluster_tags, titles):
        placed = False
        # compare to all existing subgroups
        for group in sub_groups:
            rep_title = group[0][1]
            if analyze_diff(title, rep_title) == "SAFE":
                # add to "this" group
                group.append((tag, title))
                placed = True
                break
        # note, first time round the title is placed in a new sub_group
        if not placed:
            # create new group
            sub_groups.append([(tag, title)])

    # B. Beauty Contest
    resolved = []
    for group in sub_groups:
        group_tags = [x[0] for x in group]
        group_titles = [x[1] for x in group]

        best_title = reduce(pick_best_title, group_titles)

        # Pick source_id (stable: tag with best title, or sorted first)
        candidates = [t for t, tit in group if tit == best_title]
        candidates.sort()
        # winning tag is alphabetically first from those with matching title
        winner_tag = candidates[0]

        resolved.append({
            'members': group_tags,
            'source_id': winner_tag,
            'mapped_title': best_title
        })
    return resolved


def create_golden_record(cluster: pd.DataFrame, survivor_idx: Any, config: Configurator) -> pd.Series:
    """
    Merges a cluster into one record based on config strategies.
    """
    # Start with Survivor to ensure valid baseline
    golden = cluster.loc[survivor_idx].copy()

    # put tag back
    golden['tag'] = survivor_idx

    ignored_fields = {'cluster_id', 'score', 'drop', 'source_id', 'mapped_title'}

    for col in cluster.columns:
        if col in ignored_fields or col not in golden.index:
            # note this does not remove the columns hence REMOVE step below
            continue

        # Determine Strategy, default to config's default if given else coalesce
        strat_name = config.enhancement_strategies.get(col, config.enhancement_strategies.get('default', 'coalesce'))
        strategy_func = STRATEGY_REGISTRY.get(strat_name)

        if not strategy_func:
            # Fallback if config names a non-existent function
            strategy_func = strategy_coalesce

        # Execute Strategy
        best_val = strategy_func(cluster[col])

        if pd.notna(best_val):
            golden[col] = best_val

    # 4. REMOVE: Explicitly remove the processing columns
    # We use errors='ignore' in case one of them is missing for some reason
    golden = golden.drop(labels=ignored_fields, errors='ignore')

    return golden


# --- 5. Main Pipeline ---
def process_reference_df(ref_df: pd.DataFrame, config: Configurator) -> Ans:
    """
    Full Pipeline:
    1. Find Duplicates -> 2. Graph Cluster -> 3. Resolve (Safe/Beauty) -> 4. Merge Fields

    ref_df = Library reference dataframe.

    If debug, return namedtuple

        pairs_df            df of base_tag (truncated) tag_1 tag_2 score_mean defining
                            clusters
        G                   graph, nodes = tags, edges from pairs_df
        cluster_id_map      tag -> cluster tag
        source_id_map       tag -> tag of
        title_map           tag -> selected title
        work_df             enhanced ref_df, adding cols cluster_id, source_id, mapped_title
        ans_df              one row per reference after de-duplication with best title
                            and enriched cols from other elements of the cluster. Has
                            len(df) - ... rows.

    else just return ans_df

    """
    # 1. Identify Pairs
    pairs_df = find_tag_duplicates(ref_df, config)

    # 2. Build Graph, edges connect potential duplicates
    G = nx.Graph()
    if not pairs_df.empty:
        for _, row in pairs_df.iterrows():
            G.add_edge(row['tag_1'], row['tag_2'], score=row['score_mean'])

    # Ensure all tags are nodes
    G.add_nodes_from(ref_df['tag'].unique())
    ref_map = ref_df.set_index('tag')['title'].to_dict()

    # 3. Resolve Clusters
    # Pre-calculate maps for dataframe enrichment
    cluster_id_map = {}       # tag to cluster ID
    source_id_map = {}        # tag to "leader" cluster ID
    title_map = {}            # tag to best title

    for component in nx.connected_components(G):
        members = list(component)
        distinct_groups = resolve_cluster_titles(members, ref_map)
        # distinct_groups dict with keys source_id, members and mapped_title
        for grp in distinct_groups:
            winner = grp['source_id']
            cid = f"CL_{winner}"

            # write into back to the id_mapping dicts
            for member in grp['members']:
                cluster_id_map[member] = cid
                source_id_map[member] = winner
                title_map[member] = grp['mapped_title']

    # Enrich original DF to prepare for merging
    work_df = ref_df.copy()
    work_df['cluster_id'] = work_df['tag'].map(cluster_id_map)
    work_df['source_id'] = work_df['tag'].map(source_id_map)
    work_df['mapped_title'] = work_df['tag'].map(title_map)

    # 4. Merge / Scavenger Hunt
    # Set index to 'tag' so lookup is O(1) and direct
    work_df = work_df.set_index('tag', drop=False)
    final_records = []

    for _, group in work_df.groupby('cluster_id'):
        # We already know the survivor's tag (source_id)
        # Since 'tag' is now the index, this IS the survivor_idx
        survivor_idx = group['source_id'].iloc[0]

        # Pass it directly.
        # create_golden_record uses .loc[survivor_idx], which now works perfectly
        golden = create_golden_record(group, survivor_idx, config)

        # Enforce the specific 'Beauty Contest' title
        golden['title'] = group['mapped_title'].iloc[0]
        golden['merge_count'] = len(group)

        final_records.append(golden)

    ans_df = pd.DataFrame(final_records).reset_index(drop=True)

    # audit: Filter for rows where the Tag is NOT the Winner
    dropped_df = work_df[work_df['tag'] != work_df['source_id']].copy()

    # Add the Survivor's title for comparison
    # Map the source_id to the title map we already built
    dropped_df['survivor_title'] = dropped_df['source_id'].map(title_map)

    # audit on number of entries
    ser = pd.Series(cluster_id_map)
    ser = ser.value_counts().value_counts()
    in_rows = (ser * ser.index).sum()
    out_rows = ser.sum()
    if in_rows == len(ref_df) and out_rows == len(ans_df):
        logger.info('All good - rows check out')
    else:
        logger.warning('Expected sizes do not match, input %s vs expected %s'
                       ' and output %s vs expected %s',
                       len(ref_df), in_rows,
                       len(ans_df), out_rows
        )

    return Ans(
        ans_df=ans_df, ref_doc_df=None, work_df=work_df, dropped_df=dropped_df,
        pairs_df=pairs_df, cluster_id_map=cluster_id_map, source_id_map=source_id_map,
        title_map=title_map, G=G)


# =================DOCUMENTS=========================
# --- 1. File Scanner & Cache (Physical Layer) ---
class FileFeatureCache:
    """
    Manages persistent caching of PDF features to avoid expensive re-scanning.
    Stores data in 'file_features.json' inside the library config directory.
    """

    def __init__(self, config_path: Path):
        self.cache_dir = config_path / "_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "doc_features.json"
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.dirty = False
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load file feature cache: {e}. Starting fresh.")
                self.cache = {}

    def save(self):
        if self.dirty:
            try:
                with open(self.cache_file, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, indent=2)
                self.dirty = False
            except OSError as e:
                logger.error(f"Failed to save file feature cache: {e}")

    def get_features(self, file_hash: str) -> Optional[Dict[str, Any]]:
        return self.cache.get(file_hash)

    def set_features(self, file_hash: str, features: Dict[str, Any]):
        self.cache[file_hash] = features
        self.dirty = True


def scan_doc_features(path: Path) -> Dict[str, Any]:
    """
    Opens a PDF to inspect internal quality indicators.
    """
    features = {
        'is_valid_pdf': False,
        'page_count': 0,
        'has_bookmarks': False,
        'has_text': False
    }
    # force path
    path = Path(path)

    # Short-circuit unsupported formats to save time/noise
    # fitz cannot handle these, so don't even try.
    if path.suffix.lower() in {'.dvi', '.ps', '.djvu', '.djv'}:
        return features

    try:
        with fitz.open(path) as doc:
            features['is_valid_pdf'] = True
            features['page_count'] = len(doc)

            # Check for Outlines (Bookmarks) - strong indicator of quality/structure
            # get_toc works for EPUB/MOBI too!
            toc = doc.get_toc(simple=True)
            features['has_bookmarks'] = len(toc) > 0

            # Check for text on first page (sanity check, though user said most are OCR'd)
            # get_text works for EPUB/MOBI too!
            if len(doc) > 0:
                # Text check is fast; load max 512 bytes to test presence
                text = doc[0].get_text(flags=fitz.TEXT_PRESERVE_LIGATURES)[0:512]
                features['has_text'] = len(text.strip()) > 50

    except Exception as e:
        logger.debug(f"Could not scan PDF {path}: {e}")

    return features


def enhance_library(library_obj, ans = None) -> Ans:
    """
    Main entry point.
    1. Deduplicates References (Metadata).
    2. Migrates File Links (Hostile Takeover).
    3. Elects Best File (Physical).

    Args:
        library_obj: The library instance containing .ref_df, ._doc_read_df, .ref_doc_df, .config
    """
    # Unpack library context
    ref_df = library_obj.ref_df
    ref_doc_df = library_obj.ref_doc_df
    # need to trigger read of _doc_read_df
    _ = library_obj.doc_df
    doc_df = library_obj._doc_read_df
    config = library_obj.config
    config_path = library_obj.config_path # Path to library root

    # --- Phase 1: Metadata Deduplication ---
    # allow quicker round tripping for debug
    ans = ans or process_reference_df(ref_df, config)
    logger.info(f"Metadata Dedupe: {len(ref_df)} refs -> {len(ans.ans_df)} unique refs.")

    # --- Phase 2: The Hostile Takeover (Link Migration) ---
    # Update ref_doc_df: Map old 'tag' to new 'source_id' (Survivor)
    # If a tag isn't in source_id_map, it implies it wasn't in ref_df (orphan),
    # but we map what we can.

    new_ref_doc = ref_doc_df.copy()

    # Map tags to their survivor. If no mapping exists (singleton), use original tag.
    # source_id_map contains ALL tags processed, so this covers everything in ref_df.
    new_ref_doc['tag'] = new_ref_doc['tag'].map(ans.source_id_map).fillna(new_ref_doc['tag'])

    # Filter out links that now point to tags that don't exist in our final ans_df
    # (Clean up any pre-existing orphans)
    valid_tags = set(ans.ans_df['tag'])
    new_ref_doc = new_ref_doc[new_ref_doc['tag'].isin(valid_tags)]

    # --- Phase 3: Logical Hash Deduplication ---
    # Join with doc_df to get Hash and metadata
    # new_ref_doc columns: [tag, path]
    # doc_df columns: [path, hash, size, create, ...]
    # Inner join: we only care about files that actually exist in the read index
    merged_docs = new_ref_doc.merge(doc_df, on='path', how='inner')

    # Dedupe: (Tag + Hash) should be unique.
    # If Tag A points to Path 1 (Hash X) and Path 2 (Hash X), we only need one link.
    # We prefer the one with the 'better' path (e.g., shortest length or alphabetic)
    merged_docs['path_len'] = merged_docs['path'].astype(str).str.len()
    merged_docs = merged_docs.sort_values(['tag', 'hash', 'path_len'])

    unique_links = merged_docs.drop_duplicates(subset=['tag', 'hash'], keep='first')

    # --- Phase 4: Feature Scan & Caching ---
    feature_cache = FileFeatureCache(config_path)

    # We only scan distinct hashes found in our unique links
    unique_hashes = unique_links['hash'].unique()

    # Bulk update cache
    features_list = []
    for h in tqdm(unique_hashes, desc="Scanning file features"):
        feats = feature_cache.get_features(h)
        if not feats:
            # We need a path to scan. Find one from doc_df associated with this hash.
            # (merged_docs guarantees we have at least one path)
            sample_path = unique_links[unique_links['hash'] == h]['path'].iloc[0]
            feats = scan_doc_features(sample_path)
            feature_cache.set_features(h, feats)

        # Flatten for dataframe
        feats['hash'] = h
        features_list.append(feats)

    feature_cache.save()

    feat_df = pd.DataFrame(features_list)

    # Join features back to unique_links
    candidates = unique_links.merge(feat_df, on='hash', how='left')

    # --- Phase 5: The Election (Scoring) ---
    # We define the sort order for "Best File":
    # 1. is_valid_pdf (True > False)
    # 2. has_bookmarks (True > False)
    # 3. size (Ascending - prefer compact/latex over bloated)
    # 4. create (Descending - tie-breaker: newest file)

    candidates = candidates.sort_values(
        by=['tag', 'is_valid_pdf', 'has_bookmarks', 'size', 'create'],
        ascending=[True, False, False, True, False]
    )

    # Create the 'preferred' flag
    # The first row in each tag group is the winner
    candidates['preferred'] = 0
    # Mark the first entry of each tag group as preferred
    # Using head(1) index matching
    best_indices = candidates.groupby('tag').head(1).index
    candidates.loc[best_indices, 'preferred'] = 1

    # --- Phase 6: Finalize ---
    # Reconstruct clean ref_doc_df with just [tag, path, preferred]
    final_ref_doc = candidates[['tag', 'path', 'preferred']].copy()

    logger.info(f"File Election: Mapped {len(final_ref_doc)} file links. "
                f"Selected {final_ref_doc['preferred'].sum()} preferred files.")

    # Return new Ans tuple with the updated ref_doc_df
    return ans._replace(ref_doc_df=final_ref_doc)
