# """
# Organizer - filenames and hashing
# """
# import re
# import unicodedata
# from pathlib import Path

# import numpy as np
# import pandas as pd

# # A small, practical English stop-word set for titles.
# # Tune as you like (e.g., add domain-specific filler words).
# _DEFAULT_STOP_WORDS: frozenset[str] = frozenset(
#     {
#         "a", "an", "and", "are", "as", "at", "be", "but", "by",
#         "for", "from", "has", "have", "had", "he", "her", "hers",
#         "him", "his", "i", "if", "in", "into", "is", "it", "its",
#         "me", "my", "no", "not", "of", "on", "or", "our", "ours",
#         "she", "so", "such", "than", "that", "the", "their", "theirs",
#         "them", "then", "there", "these", "they", "this", "those",
#         "to", "too", "us", "was", "we", "were", "what", "when", "where",
#         "which", "who", "whom", "why", "with", "will", "you", "your", "yours",
#     }
# )


# def doc_merged_df(lib):
#     """
#     Make the merge for enhancing doc (filenames).

#     """
#     return pd.merge(
#                 pd.merge(lib.ref_df, lib.ref_doc_df, on='tag', how='right'),
#                 lib.doc_df, on='path', how='outer')


# def longest_n_words(words: list[str], n: int) -> list[str]:
#     """Return the longest n words, preserving original order among the selected words."""
#     if n <= 0:
#         return []
#     idx = sorted(range(len(words)), key=lambda i: len(words[i]), reverse=True)[:n]
#     keep = set(idx)
#     return [w for i, w in enumerate(words) if i in keep]


# def short_title(
#     title: str,
#     n_words: int,
#     *,
#     stop_words: set[str] | frozenset[str] = _DEFAULT_STOP_WORDS,
#     keep_numbers: bool = True,
#     use_longest: bool = True
# ) -> str:
#     """
#     Convert a title into a short title:
#     - removes punctuation (treated as separators)
#     - removes stop words
#     - truncates to the first n_words remaining tokens or
#       longest n_words, retaining order (longer words are
#       more meaningful?!)

#     Parameters
#     ----------
#     title:
#         Input title string.
#     n_words:
#         Maximum number of words to keep (<= 0 yields "").
#     stop_words:
#         Stop-word set; compared case-insensitively.
#     keep_numbers:
#         If False, drops tokens that are purely numeric.
#     use_longest:
#         If True, pick the longest n_words

#     Returns
#     -------
#     str
#         Shortened title as a space-separated string.
#     """
#     # Handle trivial cases early.
#     if not title or n_words <= 0:
#         return ""

#     # Accents are no longer “inside” the letters;
#     # they are separate combining characters
#     # K = compatible, Decomposed
#     cleaned = unicodedata.normalize("NFKD", title)

#     # Remove punctuation-ish characters.
#     # Keep alphanumerics and space; everything else becomes "".
#     cleaned = re.sub(r"[^0-9A-Za-z \-]+", "", cleaned)

#     # Split into candidate tokens.
#     tokens = [t for t in cleaned.lower().split() if t]

#     # Filter stop words and (optionally) pure numbers.
#     stop = {w.casefold() for w in stop_words}
#     kept: list[str] = []
#     for tok in tokens:
#         # Drop pure numbers if requested.
#         if (not keep_numbers) and tok.isdigit():
#             continue
#         # Drop stop words (case-insensitive).
#         if tok.casefold() in stop:
#             continue
#         kept.append(tok)
#         # Truncate as soon as we hit n_words.
#         if len(kept) >= n_words:
#             break

#     if use_longest and len(kept) > n_words:
#         kept = longest_n_words(kept, n_words)

#     return " ".join(kept)


# _WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
#     {"CON", "PRN", "AUX", "NUL"}
#     | {f"COM{i}" for i in range(1, 10)}
#     | {f"LPT{i}" for i in range(1, 10)}
# )


# def short_author(
#     author_field: str,
#     max_authors: int = 3,
# ) -> str:
#     """
#     Convert a BibTeX-style author field like:
#         "Last, First and van Helsing, Abraham and Curie, Marie"
#     into a short author slug:
#         "last-van-helsing-curie"

#     Rules
#     -----
#     - Removes '{', '}', and '!' everywhere.
#     - Splits on the BibTeX author separator "and" (case-insensitive, whitespace tolerant).
#     - If an author chunk contains a comma, takes the family name as the substring before the first comma.
#     - If no comma appears in the chunk, treats the chunk as a title-like string and falls back to:
#         paper_title_to_short_title(chunk, 3)
#       then dash-joins those words.
#     - De-unicodes (NFKD + ASCII ignore) and slugifies conservatively.
#     - Returns at most `max_authors` family-name tokens, joined by "-".
#     """
#     def _deunicode_ascii(x: str) -> str:
#         x_norm = unicodedata.normalize("NFKD", x)
#         return x_norm.encode("ascii", "ignore").decode("ascii")

#     # Remove BibTeX braces and '!' globally.
#     raw = (author_field or "").replace("{", "").replace("}", "").replace("!", "").strip()
#     if not raw or max_authors <= 0:
#         return ""

#     # Split on BibTeX "and".
#     parts = [p.strip() for p in re.split(r"\s+\band\b\s+", raw, flags=re.IGNORECASE) if p.strip()]

#     family_tokens: list[str] = []
#     for part in parts:
#         if len(family_tokens) >= max_authors:
#             break

#         if "," in part:
#             tok = part.split(",", 1)[0].strip()
#             tok = _deunicode_ascii(tok)
#             if tok:
#                 family_tokens.append(tok)
#         else:
#             # Not in "Last, First" form: treat as a title-like string.
#             tok = short_title(part, 3)
#             if tok:
#                 family_tokens.append(tok)

#     return "-".join(family_tokens)


# def sanitize(
#     s: str,
#     *,
#     default: str = "untitled",
#     max_len: int = 180,
#     lowercase: bool = False,
# ) -> str:
#     """
#     Sanitize a string into a Windows-friendly filename:
#     - replaces Unicode non-ASCII with nearest ASCII equivalent (diacritics stripped)
#     - removes Windows-invalid filename characters: <>:"/\\|?* and control chars
#     - collapses multiple "-" into one
#     - trims trailing spaces and dots (Windows disallows)
#     - avoids Windows reserved device names (CON, PRN, AUX, NUL, COM1.., LPT1..)
#     - truncates to max_len (and re-trims trailing dots/spaces after truncation)

#     Parameters
#     ----------
#     s:
#         Input string.
#     default:
#         Fallback if the result becomes empty.
#     max_len:
#         Maximum output length in characters.
#     lowercase:
#         If True, lowercases the slug.

#     Returns
#     -------
#     str
#         A Windows-safe filename slug (no extension is added/removed).
#     """
#     # Normalize to decomposed form, then strip diacritics by encoding to ASCII.
#     # This is dependency-free and yields a reasonable "nearest equivalent" for Latin scripts.
#     s_norm = unicodedata.normalize("NFKD", s or "")
#     s_ascii = s_norm.encode("ascii", "ignore").decode("ascii")

#     # Remove Windows-invalid characters and control characters.
#     # Invalid set: < > : " / \ | ? * plus ASCII control 0-31.
#     s_ascii = re.sub(r'[<>:"/\\\\|?*]', "", s_ascii)
#     s_ascii = "".join(ch for ch in s_ascii if ord(ch) >= 32)

#     # Keep a conservative character set: letters, digits, hyphen, dot.
#     # Replace everything else with hyphen as a separator.
#     s_ascii = re.sub(r"[^A-Za-z0-9. ]+", "-", s_ascii)

#     # Collapse multiple hyphens, then trim hyphens.
#     s_ascii = re.sub(r"-{2,}", "-", s_ascii).strip("-")

#     # Optional case normalization.
#     if lowercase:
#         s_ascii = s_ascii.lower()

#     # Windows forbids trailing spaces and dots in filenames.
#     s_ascii = s_ascii.rstrip(" .")

#     # Avoid empty result.
#     if not s_ascii:
#         s_ascii = default

#     # Avoid reserved device names (case-insensitive), both bare and before an extension.
#     # Example: "con.txt" is also invalid.
#     base = s_ascii.split(".", 1)[0]
#     if base.upper() in _WINDOWS_RESERVED_NAMES:
#         s_ascii = f"{s_ascii}-file"

#     # Enforce max length, then re-trim forbidden trailing chars.
#     if max_len is not None and max_len > 0 and len(s_ascii) > max_len:
#         s_ascii = s_ascii[:max_len].rstrip(" .-")

#     # Final fallback if truncation nuked everything.
#     if not s_ascii:
#         s_ascii = default

#     return s_ascii


# def robust_str_convert(df, column, default="Unknown"):
#     # Convert to string first to catch numeric types
#     # np.where handles vectorization; .isna() catches None/NaN
#     s = df[column].astype(str)
#     df[column] = np.where(
#         (df[column].isna()) | (s == "nan") | (s == "None") | (s == ""),
#         default,
#         s
#     )
#     return df


# def title_from_path(path: str):
#     """Guess a title from path string."""
#     title = ' '.join(i for i in re.split(r'[ \-_,]', Path(path).stem)
#                      if i.isalpha())
#     return title or "Unknown"


# def canonical_name(doc_hash: str,
#                    author: str,
#                    title: str,
#                    year: str,
#                    file_name: str,
#                    hash_len: int = 10, max_authors: int = 3,
#                    n_title_words: int = 10):
#     """Canonical doc name from ingredients. Assumes row has reasonable defaults."""
#     # guess possible title from filename if missing
#     if title == "Unknown" and file_name != "":
#         title = title_from_path(file_name)

#     return ('_'.join([
#         doc_hash[:hash_len],
#         str(year)[:4],   # just to be careful
#         short_author(author, max_authors),
#         sanitize(short_title(title, n_title_words))
#         ])
#     )


# def canonical_name_from_row(row):
#     return canonical_name(row.hash,
#                           row.author,
#                           row.title,
#                           row.year,
#                           row.path)


# def path_from_row(row, base_dir):
#     original = Path(row.path)
#     fn = canonical_name_from_row(row)
#     return str((base_dir / fn[:2] / fn).with_suffix(original.suffix).as_posix())


# def save_from_row(row):
#     """Do the "renaming" work: create new hardlink to the original file."""
#     base_dir = Path('\\tmp\\docs')
#     original = Path(row.path)
#     fn = canonical_name_from_row(row)
#     path = (base_dir / fn[:2] / fn).with_suffix(original.suffix)
#     if path.exists():
#         return 'exists'
#     path.parent.mkdir(parents=True, exist_ok=True)
#     try:
#         path.hardlink_to(original)
#     except OSError as e:
#         print(f'OS error for {fn}\n{e}')
#         print('continuing')
#     return str(path)
