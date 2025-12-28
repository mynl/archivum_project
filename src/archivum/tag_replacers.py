"""
Handle tag migration with ref bibtex migration.

v1 Gemini
"""

import re
import pandas as pd
from typing import Callable, Tuple


def create_tag_replacers(df: pd.DataFrame) -> Tuple[Callable[[str], str], Callable[[str], pd.DataFrame]]:
    """
    Creates closures to update Quarto citation tags and audit potential changes.
    
    Args:
        df: DataFrame with columns 'tag', 'proposed_tag', and 'title'.
        
    Returns:
        Tuple containing:
        1. update_tags(text) -> str: Replaces @tag with @proposed_tag.
        2. update_audit(text) -> pd.DataFrame: Report of changes found.
    """
    # 1. Precompute Mappings (O(N))
    # Filter only necessary changes to keep lookups fast
    mask = (df['tag'] != df['proposed_tag']) & df['tag'].notna() & df['proposed_tag'].notna()
    change_df = df.loc[mask].copy()

    # Normalize keys to include '@' for direct regex matching
    # Map: @old_tag -> @new_tag
    tag_map = {}
    # Map: @old_tag -> {'proposed': @new_tag, 'title': Title}
    info_map = {}

    for row in change_df.itertuples(index=False):
        # Handle cases where input might or might not have '@' prefix
        k_str = str(row.tag).strip()
        v_str = str(row.proposed_tag).strip()
        
        key = f"@{k_str}" if not k_str.startswith('@') else k_str
        val = f"@{v_str}" if not v_str.startswith('@') else v_str
        
        tag_map[key] = val
        info_map[key] = {'proposed_tag': val, 'title': row.title}

    # Compile regex once. Matches @ followed by word chars.
    # \\w matches Unicode word characters (including ñ, α, etc.).
    # Quarto citations: @citation-key
    tag_pattern = re.compile(r'@[\w]+')

    # 2. Define Closures
    def update_tags(text: str) -> str:
        """Finds all known tags in text and replaces them."""
        def _sub(match):
            found = match.group(0)
            return tag_map.get(found, found)
        return tag_pattern.sub(_sub, text)

    def update_audit(text: str) -> pd.DataFrame:
        """Returns a DataFrame report of tags found that differ from proposed."""
        # Find all candidates in text
        found_tokens = tag_pattern.findall(text)
        
        results = []
        # Filter for only those in our change map
        # Set comprehension for unique processing, but we count occurrences
        unique_tokens = set(found_tokens)
        
        for token in unique_tokens:
            if token in info_map:
                info = info_map[token]
                count = found_tokens.count(token)
                results.append({
                    'found_tag': token,
                    'proposed_tag': info['proposed_tag'],
                    'title': info['title'],
                    'count': count
                })
        
        if not results:
            return pd.DataFrame(columns=['found_tag', 'proposed_tag', 'title', 'count'])
            
        return pd.DataFrame(results).sort_values('found_tag')

    return update_tags, update_audit

 
