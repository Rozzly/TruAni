"""Shared title utilities — season suffix detection, stripping, and extraction."""

import re

# Patterns that indicate a season suffix (order matters — longer patterns first)
_SEASON_PATTERNS = [
    # "4th Season", "2nd Season", "3rd Season" (with optional subtitle after colon)
    (r'\s+\d+(?:st|nd|rd|th)\s+Season\b(?:\s*:.*)?$', None),
    # "Season 4", "Season 2" (with optional subtitle after colon)
    (r'\s+Season\s+\d+\b(?:\s*:.*)?$', None),
    # "Part 3", "Cour 2"
    (r'\s+(?:Part|Cour)\s+\d+\s*$', None),
    # "act II -Second Season-", "act II: Second Season"
    (r'\s+act\s+[IVX]+\b.*$', None),
    # Trailing Roman numerals: "Title III", "Title IV"
    (r'\s+(?:II|III|IV|V|VI|VII|VIII|IX|X)\s*$', None),
    # Trailing number: "Title 2", "Title 4"
    (r'\s+\d+\s*$', None),
]

# Compiled combined pattern for stripping
_STRIP_RE = re.compile(
    '|'.join(f'(?:{p})' for p, _ in _SEASON_PATTERNS),
    re.IGNORECASE
)

# Extraction patterns (return a season number)
_ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
             "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10}
_ROMAN = {"II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
          "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def strip_season_suffix(title):
    """Remove season/sequel suffixes from a title. Returns the base title.
    'Dorohedoro Season 2' -> 'Dorohedoro'
    'Dr. STONE SCIENCE FUTURE Cour 3' -> 'Dr. STONE SCIENCE FUTURE'
    'Overlord IV' -> 'Overlord'
    'Title 2' -> 'Title'
    """
    if not title:
        return title
    result = _STRIP_RE.sub('', title).strip()
    return result if result else title


def extract_season_number(titles):
    """Extract season number from a list of title variants. Returns int (1 = new, 2+ = sequel).
    Tries each title and returns the first match found."""
    for title in titles:
        if not title:
            continue
        t = title.strip()

        # "Xth Season", "Xnd Season"
        m = re.search(r'(\d+)(?:st|nd|rd|th)\s+Season', t, re.IGNORECASE)
        if m:
            return int(m.group(1))

        # "Season X"
        m = re.search(r'Season\s+(\d+)', t, re.IGNORECASE)
        if m:
            return int(m.group(1))

        # "Part X" (>= 2)
        m = re.search(r'Part\s+(\d+)', t, re.IGNORECASE)
        if m and int(m.group(1)) >= 2:
            return int(m.group(1))

        # "Cour X" (>= 2)
        m = re.search(r'Cour\s+(\d+)', t, re.IGNORECASE)
        if m and int(m.group(1)) >= 2:
            return int(m.group(1))

        # Trailing Roman numeral
        m = re.search(r'\b(X{0,3}(?:IX|IV|V?I{1,3}))\s*$', t)
        if m and m.group(1).upper() in _ROMAN:
            return _ROMAN[m.group(1).upper()]

        # Ordinal at end: "Title 2nd"
        for word, num in _ORDINALS.items():
            if t.lower().endswith(word):
                return num

    # Has a prequel but couldn't parse — assume season 2
    return 2


def display_title(title):
    """Strip season suffix from a title for cleaner table display.
    The season number is shown in a separate column, so we don't need it in the title."""
    return strip_season_suffix(title)
