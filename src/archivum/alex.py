import os
import pandas as pd
from pyalex import Works, config
from typing import Dict, Any, Union, List
from tqdm import tqdm

# Initialize pyalex config globally
config.api_key = os.getenv("OPENALEX_API_KEY")
config.email = 'stephen.j.mildenhall@gmail.com'


class OpenAlex:
    """
    A wrapper for OpenAlex API calls using pyalex.
    Handles caching via unique 'tag' identifiers and provides
    extraction helpers for citation metrics.
    """

    def __init__(self):
        # Cache stores {tag: full_api_payload_dict}
        self._cache: Dict[str, Dict[str, Any]] = {}

    def query(self, data: Union[pd.Series, pd.DataFrame]) -> None:
        """
        Queries OpenAlex with progress tracking.
        Uses batching for DOIs and falls back to metadata search if needed.
        """
        if isinstance(data, pd.Series):
            self._query_single_row(data)
            return

        # 1. Filter out already cached items
        to_process = data[~data['tag'].astype(str).isin(self._cache.keys())].copy()
        if to_process.empty:
            return

        # 2. Batch DOI Processing
        has_doi = to_process[to_process['doi'].notnull()]
        doi_list = has_doi['doi'].unique().tolist()

        if doi_list:
            print(f"Batch processing {len(doi_list)} DOIs...")
            for i in tqdm(range(0, len(doi_list), 50), desc="DOI Batches"):
                chunk = doi_list[i : i + 50]
                try:
                    # 'per_page' matches chunk size to minimize pages
                    results = Works().filter(doi="|".join(chunk)).get(per_page=50)
                    for work in results:
                        raw_doi = work.get('doi', '').replace("https://doi.org/", "")
                        # Match tag using DOI fragments
                        tags = has_doi[has_doi['doi'].str.contains(raw_doi, na=False, case=False)]['tag'].tolist()
                        for t in tags:
                            self._cache[str(t)] = dict(work)
                except Exception:
                    continue

        # 3. Fallback for rows without DOI or batch misses
        still_needed = to_process[~to_process['tag'].astype(str).isin(self._cache.keys())]
        if not still_needed.empty:
            print(f"Running metadata fallback for {len(still_needed)} records...")
            for _, row in tqdm(still_needed.iterrows(), total=len(still_needed), desc="Metadata Search"):
                self._query_single_row(row)

    def _query_single_row(self, row: pd.Series) -> None:
        tag = str(row.get("tag"))
        if tag in self._cache: return

        # Try DOI first if passed as single row
        doi = getattr(row, "doi", None)
        title = getattr(row, "title", None)

        try:
            res = None
            if pd.notnull(doi):
                clean_doi = str(doi).strip()
                res = Works()[clean_doi if "doi.org" in clean_doi else f"https://doi.org/{clean_doi}"]
            elif pd.notnull(title):
                query = Works().search(str(title))
                author = getattr(row, "author", None)
                if pd.notnull(author):
                    surname = str(author).split(',')[0].split(' ')[-1].strip()
                    query = query.filter(authorships={"author": {"display_name": surname}})
                results = query.get()
                res = results[0] if results else None

            self._cache[tag] = dict(res) if res else {}
        except Exception:
            self._cache[tag] = {}

    def ref_counts(self) -> Dict[str, int]:
        """Returns a dict of {tag: cited_by_count}."""
        return {tag: payload.get("cited_by_count", 0)
                for tag, payload in self._cache.items()}

    def ref_details(self) -> Dict[str, Dict[str, Any]]:
        """Returns a dict of {tag: citation_metrics_dict}."""
        metrics_keys = [
            'counts_by_year',
            'citation_normalized_percentile',
            'cited_by_count',
            'cited_by_percentile_year'
        ]
        return {
            tag: {k: payload.get(k) for k in metrics_keys}
            for tag, payload in self._cache.items()
        }

    def details(self) -> Dict[str, Dict[str, Any]]:
        """Returns the full cached payloads keyed by tag."""
        return self._cache

    def semi_details(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns a subset of useful bibliographic fields including
        a formatted BibTeX-style author string (Last, First).
        """
        ans = {}
        # Corrected iteration over dictionary items
        for k, v in self._cache.items():
            if not v:
                ans[k] = {}
                continue

            # Map standard fields; use .get() for safety
            res = {
                'doi': v.get('doi', '').replace('https://doi.org/', ''),
                'alexid': v.get('id'),
                'title': v.get('title'),
                'year': v.get('publication_year')
            }

            # Parse authorships into "Last, First; Last, First"
            author_list = []
            for authorship in v.get('authorships', []):
                display_name = authorship.get('author', {}).get('display_name', '')
                if display_name:
                    # Logic: Split by spaces and reformat as "Last, First"
                    parts = display_name.split()
                    if len(parts) > 1:
                        formatted = f"{parts[-1]}, {' '.join(parts[:-1])}"
                    else:
                        formatted = display_name
                    author_list.append(formatted)

            res['authors'] = "; ".join(author_list)
            ans[k] = res

            # --- Extract Source (Journal or Publisher) ---
            source_info = v.get('primary_location', {}).get('source', {})
            if source_info:
                # Prioritize the display_name (Journal name)
                # Fallback to the publisher if display_name is missing or for books
                source_name = source_info.get('display_name')
                publisher = source_info.get('publisher')

                res['source'] = source_name or publisher or "Unknown Source"
            else:
                res['source'] = None

        df = pd.DataFrame(ans.values(), index=ans.keys())
        df['refs'] = df.index.map(self.ref_counts())
        return df

# --- Usage Example ---
# al = OpenAlex()
# al.query(df)  # Assuming df has 'tag', 'doi', 'title', 'author'
# counts = al.ref_counts()
# details = al.ref_details()
# full_data = al.deets()
