"""
ID mapping: AniList ID → TVDB ID

Resolution order:
1. Manual override (user-set TVDB ID — never overwritten)
2. DB cache (previously resolved mapping)
3. Sonarr search (queries TVDB via Sonarr), results graded by _grade()
"""

import re
import difflib

import db
from services.sonarr import search_series
from services.titleutil import strip_season_suffix


def resolve_tvdb_id(anime, existing=None, skip_sonarr=False):
    """Try to resolve TVDB ID for an anime entry.
    Chain: DB cache → Sonarr.
    Returns (tvdb_id, source, title, existing_record) — existing_record is the DB row or None.
    Pass existing DB record to avoid redundant query.
    Set skip_sonarr=True for fast-path resolution (DB cache only)."""
    anilist_id = anime["anilist_id"]

    if existing is None:
        existing = db.get_anime_by_anilist_id(anilist_id)

    # 0. Manual overrides are always preserved — user intent takes priority
    if existing and existing.get("mapping_source") == "manual":
        return existing["tvdb_id"], "manual", existing.get("tvdb_title"), existing

    # 1. Already resolved and cached in DB (from a previous scan)
    if existing and existing.get("tvdb_id"):
        return existing["tvdb_id"], existing.get("mapping_source", "cached"), existing.get("tvdb_title"), existing

    if skip_sonarr:
        return None, None, None, existing

    # 2. Sonarr search (queries TVDB via Sonarr — returns title too)
    tvdb_id, source, title = sonarr_lookup(anime)
    if tvdb_id:
        return tvdb_id, source, title, existing

    return None, None, None, existing


def rescan_tvdb_id(anime):
    """Force a fresh lookup, ignoring cached results.
    Returns (tvdb_id, source, title) — title may be None if not found."""
    tvdb_id, source, title = sonarr_lookup(anime)
    if tvdb_id:
        return tvdb_id, source, title
    return None, None, None


# --- Sonarr lookup (searches TVDB via Sonarr) ---

# Tokens that carry no identifying weight (Japanese particles, English articles),
# so a title sharing only these isn't a meaningful match.
_STOPWORDS = {"no", "na", "ni", "wa", "wo", "to", "ga", "the", "a", "of", "and"}

# Minimum grade for a result to be accepted as a match. Calibrated against the
# live catalogue: true anime matches grade ~1.3+ (title sim + the 0.5 anime
# bonus), while coincidental hits stay well below.
_MATCH_THRESHOLD = 1.10

_YEAR_RE = re.compile(r"\((\d{4})\)")


def _tokens(title):
    """Lowercased word set with season suffix and punctuation removed."""
    t = re.sub(r"[^\w\s]", " ", strip_season_suffix(title).lower())
    return [w for w in t.split() if w]


def _compact(title):
    """Lowercase with all non-word chars removed — 'Fan Club' == 'Fanclub'."""
    return re.sub(r"[^\w]", "", title).strip().lower()


def _compact_base(title):
    """_compact ignoring season/sequel suffixes, so a sequel matches the base
    TVDB entry ('... Season 3' == 'The 100 Girlfriends...')."""
    return _compact(strip_season_suffix(title))


def _tokens_match(a, b):
    """Two tokens match if equal, or one is a >=4-char prefix of the other
    ('dodge'/'dodgeball', 'danpei'/'danpeii') — bridges transliteration drift and
    English/romaji word-boundary differences that exact comparison misses."""
    return a == b or (len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)))


def _title_similarity(known_titles, result):
    """Best similarity in [0, 1] between any known AniList title and any title the
    TVDB result is known by (primary + alternate titles). 1.0 for an exact match
    (ignoring case/punctuation/season suffix); otherwise the stronger of a
    prefix-aware token-set ratio and a character-level ratio. The token ratio is
    discounted when only stopwords are shared."""
    candidates = [result.get("title") or ""] + list(result.get("alternateTitles") or [])
    best = 0.0
    for known in known_titles:
        kc, kb, kt = _compact(known), _compact_base(known), _tokens(known)
        for cand in candidates:
            if not cand:
                continue
            if _compact(cand) == kc or _compact_base(cand) == kb:
                return 1.0
            rt = _tokens(cand)
            tset = 0.0
            if kt and rt:
                mk = sum(1 for x in kt if any(_tokens_match(x, y) for y in rt))
                mr = sum(1 for y in rt if any(_tokens_match(x, y) for x in kt))
                distinct = sum(1 for x in kt if x not in _STOPWORDS
                               and any(_tokens_match(x, y) for y in rt))
                tset = min(min(mk, mr) / (min(len(kt), len(rt)) or 1), 1.0)
                if distinct == 0:
                    tset = min(tset, 0.3)
            char = difflib.SequenceMatcher(None, kc, _compact(cand)).ratio()
            best = max(best, tset, char)
    return min(best, 1.0)


def _grade(anime, result, known_titles):
    """Continuous match grade for a TVDB lookup result — higher is better.

    Additive signals: title similarity (0-1); a strong anime bonus (every source
    title is anime, so this outweighs an exact title on a same-named live-action
    entry); a smaller Japanese-origin bonus; a nudge for TVDB's top-ranked hits;
    and a release-year term that disambiguates same-named remakes, which TVDB
    suffixes '(YEAR)'. Non-sequels matched to an ended series airing well outside
    the anime's year are penalised (sequels legitimately reuse an old base entry)."""
    grade = _title_similarity(known_titles, result)
    if result.get("isAnime"):
        grade += 0.50
    if result.get("isJapanese"):
        grade += 0.10
    rank = result.get("rank", 99)
    grade += 0.12 if rank == 0 else (0.05 if rank == 1 else 0.0)

    year = anime.get("season_year")
    m = _YEAR_RE.search(result.get("title") or "")
    if m and year:
        result_year = int(m.group(1))
        if result_year == year:
            grade += 0.20
        elif abs(result_year - year) > 1:
            grade -= 0.30

    is_sequel = anime.get("is_sequel") or (anime.get("season_number") or 1) > 1
    if not is_sequel and (result.get("status") or "").lower() == "ended":
        last_aired = result.get("lastAired") or ""
        if last_aired and year:
            try:
                if abs(year - int(last_aired[:4])) > 1:
                    grade -= 0.50
            except (ValueError, IndexError):
                pass
    return grade


def _search_terms(anime):
    """Ordered list of titles to query Sonarr with: base forms (season suffix
    stripped, subtitle before a colon) before full titles, drawn from the English
    title, synonyms, romaji, then the native (Japanese) title. TVDB indexes the
    original series name, so base forms find sequels the full season-marked title
    would miss; synonyms cover romaji-only AniList entries whose English name
    lives only in a synonym; and the native title resolves entries TVDB has
    indexed under their Japanese name where the romaji search ranks poorly."""
    def base_titles(title):
        bases = []
        stripped = strip_season_suffix(title)
        if stripped and stripped != title:
            bases.append(stripped)
        if ":" in title:
            before = title.split(":")[0].strip()
            if before and before not in bases:
                bases.append(before)
        return bases

    terms = []
    sources = ([anime.get("title_english")] + (anime.get("synonyms") or [])
               + [anime.get("title_romaji"), anime.get("title_native")])
    for title in sources:
        if not title:
            continue
        for base in base_titles(title):
            if base not in terms:
                terms.append(base)
        if title not in terms:
            terms.append(title)
    return terms[:5]


def sonarr_lookup(anime):
    """Search Sonarr (which queries TVDB) and return the best-matching series as
    (tvdb_id, 'sonarr', title), or (None, None, None) if nothing grades highly
    enough. Every candidate is scored by _grade(); the highest-graded result at or
    above _MATCH_THRESHOLD wins. Replaces the old binary word-overlap filter,
    which rejected correct matches whose TVDB title is an English translation
    (sharing few words with the romaji) and mis-ranked same-named remakes."""
    known_titles = []
    if anime.get("title_english"):
        known_titles.append(anime["title_english"])
    for syn in (anime.get("synonyms") or []):
        if syn and syn not in known_titles:
            known_titles.append(syn)
    if anime.get("title_romaji"):
        known_titles.append(anime["title_romaji"])

    best = None       # (grade, tvdb_id, title)
    seen = {}         # tvdb_id -> best grade so far (dedupe across search terms)
    for term in _search_terms(anime):
        for result in search_series(term):
            # The source catalogue is anime-only, so ignore entries that are
            # neither animation nor Japanese (e.g. a same-named Western show).
            if not (result.get("isAnime") or result.get("isJapanese")):
                continue
            tvdb_id = result.get("tvdbId")
            if not tvdb_id:
                continue
            grade = _grade(anime, result, known_titles)
            if tvdb_id in seen and seen[tvdb_id] >= grade:
                continue
            seen[tvdb_id] = grade
            if best is None or grade > best[0]:
                best = (grade, tvdb_id, result.get("title"))

    if best and best[0] >= _MATCH_THRESHOLD:
        return best[1], "sonarr", best[2]
    return None, None, None
