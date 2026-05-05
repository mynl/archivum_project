
import pandas as pd
import re
from pathlib import Path
from archivum.library import Library
from archivum.trie import Trie
from nameparser import HumanName

class NameRationalizer:
    def __init__(self, lib: Library):
        self.lib = lib
        self.trie = Trie()
        self.trie_norm = Trie()
        self.raw_to_norm = {}
        self.norm_to_raw = {}
        self.build_tries()

    def normalize(self, name):
        """
        Normalize name for matching: 
        1. HumanName for Last, First Middle format.
        2. Remove dots.
        3. Space initials.
        """
        if not name or pd.isna(name):
            return ""
        
        # Use HumanName to get consistent Last, First Middle
        hn = HumanName(name)
        # Normalize to "Last, First Middle"
        norm = f"{hn.last}, {hn.first}"
        if hn.middle:
            norm += f" {hn.middle}"
        
        # Remove dots and extra spaces
        norm = norm.replace(".", " ")
        norm = " ".join(norm.split())
        return norm

    def build_tries(self):
        # 1. Get all unique authors (exploded)
        authors = []
        if not self.lib.ref_df.empty:
            # We use a set to avoid duplicates immediately
            raw_authors = set()
            for s in self.lib.ref_df.author.dropna():
                for a in s.split(" and "):
                    raw_authors.add(a.strip())
        
        print(f"Found {len(raw_authors)} unique raw author strings.")
        
        # 2. Insert into Tries
        for raw in raw_authors:
            # Insert raw into raw trie (standard prefix match)
            self.trie.insert(raw)
            
            # Insert normalized version into normalized trie
            norm = self.normalize(raw)
            if norm:
                # We want the Trie to return the RAW name as the value
                # But if multiple raws map to one norm, we want the longest/best raw.
                if norm in self.norm_to_raw:
                    if len(raw) > len(self.norm_to_raw[norm]):
                        self.norm_to_raw[norm] = raw
                else:
                    self.norm_to_raw[norm] = raw
                
                self.trie_norm.insert(norm, value=norm)

    def rationalise_single_name(self, name):
        if not name: return name
        name = name.strip()
        
        # Strategy 1: Raw Trie extension
        try:
            ext = self.trie.longest_unique_completion(name, strict=False)
            if ext != name and len(ext) > len(name):
                return ext
        except:
            pass
            
        # Strategy 2: Normalized Trie extension
        norm = self.normalize(name)
        if norm:
            try:
                ext_norm = self.trie_norm.longest_unique_completion(norm, strict=False)
                if ext_norm in self.norm_to_raw:
                    ext = self.norm_to_raw[ext_norm]
                    if len(ext) > len(name):
                        return ext
            except:
                pass
        
        return name

    def rationalise_author_string(self, author_str):
        if not author_str or pd.isna(author_str):
            return author_str
            
        parts = author_str.split(" and ")
        new_parts = []
        changed = False
        
        for p in parts:
            res = self.rationalise_single_name(p)
            if res != p.strip():
                changed = True
            new_parts.append(res)
            
        if changed:
            return " and ".join(new_parts)
        return author_str

    def get_proposed_changes(self):
        print("Analyzing references for name rationalization...")
        df = self.lib.ref_df[['tag', 'author']].copy()
        df['proposed'] = df['author'].apply(self.rationalise_author_string)
        
        diff = df[df['author'] != df['proposed']].copy()
        return diff

def main():
    # Example usage:
    # lib = Library("YourLibraryName")
    # rat = NameRationalizer(lib)
    # changes = rat.get_proposed_changes()
    # print(changes)
    pass

if __name__ == "__main__":
    import sys
    lib_name = sys.argv[1] if len(sys.argv) > 1 else ""
    lib = Library(lib_name)
    rat = NameRationalizer(lib)
    changes = rat.get_proposed_changes()
    
    if changes.empty:
        print("No changes proposed.")
    else:
        print(f"Proposed {len(changes)} changes:")
        # Display first 20 for preview
        pd.set_option('display.max_colwidth', None)
        print(changes.head(20))
        
        # Check specifically for Laeven if it exists
        laeven = changes[changes['author'].str.contains("Laeven", na=False)]
        if not laeven.empty:
            print("\nSpecific changes for 'Laeven':")
            print(laeven)

