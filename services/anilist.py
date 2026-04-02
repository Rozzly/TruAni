import time
import requests
from datetime import datetime

ANILIST_API_URL = "https://graphql.anilist.co"
_session = requests.Session()

SEASONS = {1: "WINTER", 2: "WINTER", 3: "WINTER",
           4: "SPRING", 5: "SPRING", 6: "SPRING",
           7: "SUMMER", 8: "SUMMER", 9: "SUMMER",
           10: "FALL", 11: "FALL", 12: "FALL"}


SEASON_ORDER = ["WINTER", "SPRING", "SUMMER", "FALL"]


def current_season():
    now = datetime.now()
    return SEASONS[now.month], now.year


def next_season():
    """Return the next upcoming season and year."""
    season, year = current_season()
    idx = SEASON_ORDER.index(season)
    if idx == 3:
        return SEASON_ORDER[0], year + 1
    return SEASON_ORDER[idx + 1], year


def fetch_seasonal_anime(season, year):
    """Fetch all TV and ONA anime for a given season/year from AniList.
    Excludes shorts (episode duration <= 15 minutes when known)."""
    query = """
    query ($page: Int, $season: MediaSeason, $seasonYear: Int) {
        Page(page: $page, perPage: 50) {
            pageInfo {
                hasNextPage
                currentPage
            }
            media(
                season: $season,
                seasonYear: $seasonYear,
                type: ANIME,
                format_in: [TV, ONA],
                countryOfOrigin: "JP",
                isAdult: false,
                sort: POPULARITY_DESC
            ) {
                id
                title {
                    romaji
                    english
                    native
                }
                synonyms
                format
                episodes
                duration
                description(asHtml: false)
                genres
                averageScore
                siteUrl
                coverImage {
                    large
                    extraLarge
                }
                seasonYear
                status
                relations {
                    edges {
                        relationType
                    }
                }
            }
        }
    }
    """

    all_media = []
    page = 1
    has_next = True

    while has_next:
        variables = {"page": page, "season": season.upper(), "seasonYear": year}
        data = _request(query, variables)

        page_data = data["data"]["Page"]
        all_media.extend(page_data["media"])
        has_next = page_data["pageInfo"]["hasNextPage"]
        page += 1

    from services.titleutil import extract_season_number

    results = []
    for m in all_media:
        # Skip shorts: exclude if duration is known and <= 15 minutes
        duration = m.get("duration")
        if duration is not None and duration <= 15:
            continue
        has_prequel = _has_prequel(m)
        titles = [m["title"].get("english") or "", m["title"].get("romaji") or ""]
        season_num = extract_season_number(titles) if has_prequel else 1

        results.append({
            "anilist_id": m["id"],
            "title_english": m["title"]["english"],
            "title_romaji": m["title"]["romaji"],
            "title_native": m["title"].get("native"),
            "synonyms": m.get("synonyms") or [],
            "format": m["format"],
            "episodes": m["episodes"],
            "description": (m.get("description") or "")[:500],
            "genres": ",".join(m.get("genres") or []),
            "score": m.get("averageScore"),
            "anilist_url": m.get("siteUrl"),
            "cover_url": m["coverImage"]["large"] if m["coverImage"] else None,
            "cover_url_lg": (m["coverImage"].get("extraLarge") or m["coverImage"]["large"]) if m["coverImage"] else None,
            "season": season.upper(),
            "season_year": year,
            "is_sequel": 1 if has_prequel else 0,
            "season_number": season_num,
        })

    return results


def _has_prequel(media):
    """Check if a media entry has a PREQUEL or PARENT relation."""
    edges = (media.get("relations") or {}).get("edges") or []
    return any(e.get("relationType") in ("PREQUEL", "PARENT") for e in edges)




def _request(query, variables, retries=3):
    for attempt in range(retries):
        try:
            resp = _session.post(
                ANILIST_API_URL,
                json={"query": query, "variables": variables},
                timeout=30,
            )
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            print(f"[AniList] Rate limited, waiting {retry_after}s...")
            time.sleep(retry_after)
            continue

        if resp.status_code == 500 and attempt < retries - 1:
            time.sleep(5)
            continue

        resp.raise_for_status()
        data = resp.json()

        if data.get("errors"):
            msgs = [e.get("message", "Unknown") for e in data["errors"]]
            raise Exception(f"AniList GraphQL errors: {', '.join(msgs)}")

        return data

    raise Exception("AniList API: max retries exceeded")
