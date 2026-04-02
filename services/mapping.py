"""
ID mapping: AniList ID → TVDB ID

Lookup chain (in order):
1. DB cache (previously resolved mapping)
2. Sonarr search (queries TVDB via Sonarr)
3. Manual entry (user sets TVDB ID via UI)
"""

import re
import db


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
    tvdb_id, source, title = _sonarr_lookup(anime)
    if tvdb_id:
        return tvdb_id, source, title, existing

    return None, None, None, existing


def rescan_tvdb_id(anime):
    """Force a fresh lookup, ignoring cached results.
    Returns (tvdb_id, source, title) — title may be None if not found."""
    tvdb_id, source, title = _sonarr_lookup(anime)
    if tvdb_id:
        return tvdb_id, source, title
    return None, None, None


# --- Sonarr lookup (searches TVDB via Sonarr) ---

def _sonarr_lookup(anime):
    """Search Sonarr by title to find TVDB ID. Returns (tvdb_id, 'sonarr', title) or (None, None, None).
    Validates results: must be anime, year within range, and title must have meaningful overlap.
    Searches with base title (stripped of season/sequel suffixes) for better TVDB matches."""
    from services.sonarr import search_series
    from services.titleutil import strip_season_suffix

    titles = []
    if anime.get("title_english"):
        titles.append(anime["title_english"])
    for syn in (anime.get("synonyms") or []):
        if syn and syn not in titles:
            titles.append(syn)
    if anime.get("title_romaji"):
        titles.append(anime["title_romaji"])

    anime_year = anime.get("season_year")

    def _normalize(t):
        """Strip season/part suffixes, punctuation, and lowercase for word comparison."""
        t = strip_season_suffix(t).lower()
        t = re.sub(r'[^\w\s]', ' ', t)
        return set(t.split())

    def _compact(t):
        """Lowercase, strip punctuation and spaces for exact comparison.
        Handles compound words: 'Fan Club' == 'Fanclub'."""
        return re.sub(r'[^\w]', '', t).strip().lower()

    def _is_exact_match(result_title):
        """Check if the result title exactly matches any known title for this anime."""
        rt = _compact(result_title)
        return any(rt == _compact(t) for t in titles if t)

    def _title_matches(search_title, result_title):
        """Check if the result title has meaningful word overlap with the search title."""
        # First check compact match (handles compound words like Fanclub vs Fan Club)
        if _compact(search_title) == _compact(result_title):
            return True
        search_words = _normalize(search_title)
        result_words = _normalize(result_title)
        if not search_words or not result_words:
            return False
        overlap = len(search_words & result_words)
        min_len = min(len(search_words), len(result_words))
        # For short titles (1-2 words), require all words to match
        if min_len <= 2:
            return overlap >= min_len
        # For longer titles, require at least 50% overlap
        return overlap >= min_len * 0.5

    def _extract_base_titles(title):
        """Extract search variations: strip season suffix, strip subtitle after colon."""
        bases = []
        if not title:
            return bases
        stripped = strip_season_suffix(title)
        if stripped and stripped != title:
            bases.append(stripped)
        # Try part before colon for subtitled sequels (e.g., "BLEACH: Thousand-Year Blood War")
        if ':' in title:
            before_colon = title.split(':')[0].strip()
            if before_colon and before_colon not in bases:
                bases.append(before_colon)
        return bases

    # Build search queries: try base titles first, then full titles.
    # TVDB entries use the original series name, so "Bleach" finds results
    # but "BLEACH: Thousand-Year Blood War - The Calamity" does not.
    search_titles = []
    for title in [anime.get("title_english"), anime.get("title_romaji")]:
        if not title:
            continue
        for base in _extract_base_titles(title):
            if base not in search_titles:
                search_titles.append(base)
        if title not in search_titles:
            search_titles.append(title)
    search_titles = search_titles[:4]  # limit API calls

    is_sequel = anime.get("is_sequel") or (anime.get("season_number") or 1) > 1
    best = None  # (score, tvdb_id, title) — higher score wins
    seen_tvdb = set()

    for search_title in search_titles:
        results = search_series(search_title)
        for r in results:
            tvdb_id = r.get("tvdbId")
            if not tvdb_id or tvdb_id in seen_tvdb:
                continue
            seen_tvdb.add(tvdb_id)

            result_title = r.get("title") or ""
            exact = _is_exact_match(result_title)
            is_anime = r.get("isAnime") or r.get("isJapanese")

            # Must be anime/Japanese OR exact title match
            if not exact and not is_anime:
                continue

            # For new series (not sequels), reject ended series with stale lastAired
            status = (r.get("status") or "").lower()
            if not is_sequel and status == "ended":
                last_aired = r.get("lastAired") or ""
                if last_aired and anime_year:
                    try:
                        aired_year = int(last_aired[:4])
                        if abs(anime_year - aired_year) > 1:
                            continue
                    except (ValueError, IndexError):
                        pass

            # Title similarity check (exact matches pass automatically)
            if not exact:
                matched = any(_title_matches(t, result_title) for t in titles)
                if not matched:
                    continue

            # Score: prefer anime > non-anime, exact > fuzzy
            score = (2 if r.get("isAnime") else 0) + (1 if exact else 0)
            if best is None or score > best[0]:
                best = (score, tvdb_id, r.get("title"))
                if score >= 3:  # anime + exact — can't do better
                    return tvdb_id, "sonarr", r.get("title")

    if best:
        return best[1], "sonarr", best[2]
    return None, None, None
