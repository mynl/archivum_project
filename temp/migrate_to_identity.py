"""
One time run to convert from path to hash based indexing.

Feb 2026
"""

import pandas as pd
from pathlib import Path
import shutil
import datetime as dt
import os

def migrate_library(lib_path: Path):
    lib_path = Path(lib_path)
    doc_path = lib_path / "doc.feather"
    ref_doc_path = lib_path / "ref-doc.feather"
    
    if not doc_path.exists() or not ref_doc_path.exists():
        print(f"Error: Required feather files not found in {lib_path}")
        return

    # 1. Load Data
    print(f"Loading data from {lib_path.name}...")
    df_doc = pd.read_feather(doc_path)
    df_ref_doc = pd.read_feather(ref_doc_path)
    
    # 2. Assign Versions in doc_df (0-Indexed)
    print("Assigning versions to document paths (0-indexed)...")
    # Sort to ensure deterministic versioning
    df_doc = df_doc.sort_values(['hash', 'path'])
    df_doc['version'] = df_doc.groupby('hash').cumcount()
    
    # 3. Migrate ref-doc links
    print("Migrating ref-doc links from path to (hash, version)...")
    # We join on path to bring in the hash and version assigned above
    merged = df_ref_doc.merge(df_doc[['path', 'hash', 'version']], on='path', how='left')
    
    # Check for any failures (links pointing to paths not in doc_df)
    missing = merged[merged['hash'].isna()]
    if not missing.empty:
        print(f"Warning: {len(missing)} links in ref-doc have no corresponding entry in doc.feather.")
    
    cols_to_keep = ['tag', 'hash', 'version']
    if 'preferred' in df_ref_doc.columns:
        cols_to_keep.append('preferred')
        
    df_ref_doc_new = merged[cols_to_keep].copy()
    
    # Ensure types are consistent (integers for version)
    df_doc['version'] = df_doc['version'].astype(int)
    # Fill missing versions with -1 for now so we don't have NaNs in the link table
    df_ref_doc_new['version'] = df_ref_doc_new['version'].fillna(-1).astype(int)

    # 4. Backup and Save
    if 0:
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = lib_path / "path-to-hash-backup"
        backup_dir.mkdir(exist_ok=True)

        print(f"Backing up originals to {backup_dir}...")
        shutil.copy2(doc_path, backup_dir / f"doc_pre_identity_{timestamp}.feather")
        shutil.copy2(ref_doc_path, backup_dir / f"ref_doc_pre_identity_{timestamp}.feather")

        print("Saving updated feather files...")
        df_doc.to_feather(doc_path)
        df_ref_doc_new.to_feather(ref_doc_path)
    
    print("Migration complete!")
    return df_doc, df_ref_doc_new

if __name__ == "__main__":
    # Detect Local AppData for convenience
    p = Path("c:\\s\\appdata\\archivum\\libraries\\test-library")
    assert p.exists()
    migrate_library(p)
