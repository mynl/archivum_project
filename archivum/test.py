"""
Various testers.
"""
import re
from typing import Optional, Dict, List, Tuple


# 1. book filename to authors and title
# 2. get meta data
# 3. get author title from first page


def gem_parse_filename(doc_path):
    """
    Heuristic parsing of filenames based on common ebook patterns.
    Slightly under performs gpt version
    GEMINI
    """
    name = doc_path.stem
    candidate = {"source": "filename"}

    # --- A. Clean Noise ---
    # Remove common "pirate" tags and file extensions in stem if any
    noise_patterns = [
        r"\(z-lib\.org\)",
        r"\(Z-Library\)",
        r"libgen\.li",
        r"\(auth\.\)",
        r"\(eds\.\)",
        r"\(ed\.\)",
        r"_crc",
        r"\(.*Springer.*\)",
        r"\(.*Cambridge.*\)",
        r"\(.*Wiley.*\)",
    ]
    clean_name = name
    for pat in noise_patterns:
        clean_name = re.sub(pat, "", clean_name, flags=re.IGNORECASE)

    clean_name = clean_name.replace("_", " ").strip()

    # --- B. Extract Year ---
    # Look for (YYYY) or start of string YYYY
    year_match = re.search(r"\((\d{4})\)|^\s*(\d{4})\s", clean_name)
    if year_match:
        candidate["year"] = year_match.group(1) or year_match.group(2)
        # Remove year from name to clean up title parsing
        clean_name = re.sub(r"\((\d{4})\)|^\s*(\d{4})\s", "", clean_name)

    # --- C. Structure Detection ---

    # Strategy 1: "Title by Author"
    if " by " in clean_name:
        parts = clean_name.split(" by ", 1)
        candidate["title"] = parts[0].strip()
        candidate["author"] = parts[1].strip()

    # Strategy 2: "Author - Title" (Hyphen separated)
    # Note: Many files have "Series - Author - Title".
    # We split by " - " (space hyphen space) to avoid hyphenated words.
    elif " - " in clean_name:
        segments = clean_name.split(" - ")
        # Heuristic: Title is usually the longest segment
        longest = max(segments, key=len)
        candidate["title"] = longest.strip()

        # If 2 parts: Author - Title OR Title - Author?
        # Usually Author - Title.
        if len(segments) >= 2:
            # If the longest is the second part, assume first is author
            if longest == segments[1]:
                candidate["author"] = segments[0].strip()
            # If longest is first part, assume second is author (less common but possible)
            elif longest == segments[0]:
                candidate["author"] = segments[1].strip()

    # Strategy 3: "Title (Author)"
    elif "(" in clean_name and clean_name.endswith(")"):
        # Last parentheses often contain Author
        match = re.search(r"(.*)\((.*)\)$", clean_name)
        if match:
            candidate["title"] = match.group(1).strip()
            candidate["author"] = match.group(2).strip()

    # Fallback: Treat whole cleaned string as Title
    else:
        candidate["title"] = clean_name.strip()

    # Clean up authors (remove " et al", etc)
    if candidate.get("author"):
        candidate["author"] = re.sub(
            r" et al\.?", "", candidate["author"], flags=re.IGNORECASE
        )

    return candidate


def gpt_guess_from_filename(
    doc_path,
) -> Tuple[Optional[str], Optional[str], Optional[str], float, List[str]]:
    """
    Best-effort guess of (title, author, year) from the filename.
    Returns a tuple (title, author, year, confidence, diagnostics).
    All fields may be None if no reasonable guess is possible.
    """
    diagnostics: List[str] = []
    stem = doc_path.stem

    # Normalize underscores and whitespace.
    name = stem.replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()

    # Strip trailing source tags like (z-lib.org), (Z-Library), (libgen.li).
    name2 = re.sub(
        r"\s*\((z-lib\.org|Z-Library|libgen\.li)\)\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    if name2 != name:
        diagnostics.append("removed trailing source tag")
        name = name2

    # Strip trailing copy indices like (1), (2).
    name2 = re.sub(r"\s*\(\d+\)\s*$", "", name)
    if name2 != name:
        diagnostics.append("removed trailing copy index")
        name = name2

    # Strip leading series information in parentheses.
    name2 = re.sub(r"^\([^)]*\)\s*", "", name)
    if name2 != name:
        diagnostics.append("removed leading series information")
        name = name2

    # If nothing useful remains, bail out.
    if not name or len(name) < 5:
        diagnostics.append("filename too short or empty after cleaning")
        return None, None, None, 0.0, diagnostics

    # Find a plausible year (prefer the last one if multiple).
    year: Optional[str] = None
    year_matches = re.findall(r"\b(19\d{2}|20\d{2})\b", name)
    if year_matches:
        year = year_matches[-1]
        diagnostics.append(f"found year {year} in filename")
        name = re.sub(rf"\b{year}\b", "", name)
        name = re.sub(r"\s+", " ", name).strip(" -_,")

    title: Optional[str] = None
    author: Optional[str] = None
    confidence = 0.0

    # Pattern 1: "Title by Author"
    by_match = re.search(r"\sby\s", name, flags=re.IGNORECASE)
    if by_match:
        raw_title = name[: by_match.start()].strip(" -_,")
        raw_author = name[by_match.end() :].strip(" -_,")
        # Drop trailing parenthetical noise from author.
        raw_author = re.sub(r"\s*\([^)]*\)\s*$", "", raw_author).strip(" -_,")
        if raw_title and raw_author:
            title = raw_title
            author = raw_author
            confidence = 0.9
            diagnostics.append("matched pattern 'title by author'")

    # Pattern 2: "Title (Author)" at the end.
    if title is None:
        paren_match = re.search(r"\(([^()]*)\)\s*$", name)
        if paren_match:
            possible_author = paren_match.group(1).strip()
            # Heuristic: looks like a name if it has at least one space and no digits.
            if " " in possible_author and not re.search(r"\d", possible_author):
                raw_title = name[: paren_match.start()].strip(" -_,")
                if raw_title:
                    title = raw_title
                    author = possible_author
                    confidence = max(confidence, 0.75)
                    diagnostics.append("matched pattern 'title (author)'")

    # Pattern 3: "Author - Title - ..."
    if title is None:
        parts = re.split(r"\s-\s", name)
        if len(parts) >= 2:
            candidate_author = re.sub(r"\s*\([^)]*\)\s*$", "", parts[0]).strip(" -_,")
            remainder = " - ".join(parts[1:]).strip(" -_,")
            # Basic name-like check: letters present, very few digits.
            if re.search(r"[A-Za-z]", candidate_author) and not re.search(
                r"\d", candidate_author
            ):
                author = candidate_author
                title = remainder
                confidence = max(confidence, 0.8)
                diagnostics.append("matched pattern 'author - title'")

    # Pattern 4: "YYYY Title" (only if we still do not have a title).
    if title is None:
        m = re.match(r"^(19\d{2}|20\d{2})[\s_]+(.+)$", stem)
        if m:
            if year is None:
                year = m.group(1)
                diagnostics.append(f"year {year} from prefix")
            title = m.group(2).replace("_", " ")
            title = re.sub(r"\s+", " ", title).strip(" -_,")
            confidence = max(confidence, 0.5)
            diagnostics.append("matched pattern 'YYYY title'")

    # Final fallback: treat remaining cleaned name as title only.
    if title is None:
        title = name.strip(" -_,")
        confidence = max(confidence, 0.3)
        diagnostics.append("fallback: treated filename as title only")

    # Normalize whitespace for title and author.
    if title:
        title = re.sub(r"\s+", " ", title).strip(" -_,")
    if author:
        author = re.sub(r"\s+", " ", author).strip(" -_,")

    # Basic informativeness check for title.
    def _informative(s: str) -> bool:
        letters = len(re.findall(r"[A-Za-z]", s))
        return bool(letters) and letters >= len(s) / 3

    if not title or len(title) < 5 or not _informative(title):
        diagnostics.append(f"title '{title}' deemed uninformative")
        title = None

    # Adjust confidence based on what we actually have.
    if title is None and author is None and year is None:
        return None, None, None, 0.0, diagnostics

    if title is not None:
        confidence += 0.1
    if author is not None:
        confidence += 0.1
    if year is not None:
        confidence += 0.1

    confidence = max(0.0, min(1.0, confidence))

    return title, author, year, confidence, diagnostics
