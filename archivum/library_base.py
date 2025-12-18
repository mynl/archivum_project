
from pathlib import Path 

import pandas as pd

class LibraryBase:
    """Some base class functions for Library."""
    def refs_no_docs(self):
        """Return tags to refs with no entries in ref_doc, indices missing an afile."""
        if self.ref_df.empty: return pd.DataFrame()
        idx = sorted(set(self.ref_df.tag) - set(self.ref_doc_df.tag))
        return self.ref_df.query('tag in @idx')

    def docs_no_refs(self):
        """Return docs with no associated refs."""
        if self.doc_df.empty: return pd.DataFrame()
        paths = set(self.doc_df.path) - set(self.ref_doc_df.path)
        df = self.doc_df.loc[self.doc_df.path.isin(paths)].copy()
        df['filename'] = df.path.map(lambda x: Path(x).name)
        df['parent'] = df.path.map(lambda x: Path(x).parent.name)
        df = df[['name', 'filename', 'parent', 'path', 'mod', 'create', 'access', 'node', 'links', 'size',
                       'suffix', 'hash']]
        return df

    def stats(self):
        """
        Statistics about refs (tags), docs (paths).

        For refs, figures how many
        """
        if self.ref_df.empty: return pd.DataFrame()

        data = {}

        # Configuration for the two rows
        views = [
            ('references', len(self.ref_df), 'tag'),
            ('documents', len(self.doc_df), 'path')
        ]

        for name, total, group_col in views:
            # Calculate counts per group
            counts = self.ref_doc_df.groupby(group_col).size()

            # Frequency distribution of those counts
            dist = counts.value_counts()

            # specific row data
            row = {
                'objects': total,
                'no children': total - len(counts),
                'children': len(counts)
            }

            # Dynamically add columns for each count found (1, 2, 3, 4, 5...)
            for num_children, freq in dist.items():
                label = f"{num_children} child{'ren' if num_children != 1 else ''}"
                row[label] = freq

            data[name] = row

        # Create DataFrame
        df = pd.DataFrame.from_dict(data, orient='index').fillna(0).astype(int)

        # Sort columns: fixed headers first, then numerical distribution
        fixed_cols = ['objects', 'no children', 'children']
        dist_cols = sorted(
            [c for c in df.columns if c not in fixed_cols],
            key=lambda x: int(str(x).split()[0])
        )

        return df[fixed_cols + dist_cols].T

    def stats_ref_fields(self):
        """Statistics on distinct values by field."""
        if self.ref_df.empty: return pd.DataFrame()
        ans = {}
        for c in self.ref_df.columns:
            vc = self.ref_df[c].value_counts()
            if c == 'arc-citations':
                ans[c] = [len(vc), vc.get(0, 0)]
            else:
                ans[c] = [len(vc), vc.get('', 0)]

        stats = pd.DataFrame(ans.values(),
                             columns=[ 'distinct', 'missing'],
                             index=ans.keys())
        return stats

    def distinct_values_by_field(self):
        """Statistics on distinct values by field."""
        if self.ref_df.empty: return pd.DataFrame()
        ans = {}
        for c in self.ref_df.columns:
            vc = self.ref_df[c].value_counts()
            if c == 'arc-citations':
                ans[c] = [len(vc), vc.get(0, 0)]
            else:
                ans[c] = [len(vc), vc.get('', 0)]

        stats = pd.DataFrame(ans.values(),
                             columns=[ 'distinct', 'missing'],
                             index=ans.keys())
        # c: len(self.distinct(c)) for c in self.ref_df.columns
        # }, index=['Value']).T
        return stats

    def distinct_value_counts(self, field):
        """Return the top 20 distinct value counts for field."""
        return (None
                if field not in self.database else
                    self.database[field]
                        .value_counts()
                        .to_frame('count')
                        .sort_values('count', ascending=False)
                        .head(20)
                )

