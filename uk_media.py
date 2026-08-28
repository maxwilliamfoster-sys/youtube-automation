"""
Verified UK imagery — real faces and real places, with the person↔image link proven.

The point of this module is that a face shown next to "gang member" narration MUST be
the right person. Getting it wrong is defamatory, and UK defamation law is claimant-
friendly, so the link is never inferred from a filename or a search hit. It comes from
Wikidata's P18 ("image") property: a structured statement, on the person's own entity,
that this file depicts them. If that statement does not exist, no face is shown.

What is and is not available, measured rather than assumed:

  * Free-licensed photographs of UK criminals barely exist — 1 of 8 well-known names
    tested had a Wikidata image. Press photos are agency copyright (Getty/PA) and
    police custody images stay under force copyright: they are licensed to media only
    "in connection with the conviction and sentencing", must remain contemporaneous,
    and any later reuse needs prior written permission from that force. So mugshots
    cannot be used here, and this module never touches them.

  * Real UK PLACES are plentiful and free. Commons returned 64 usable high-resolution
    CC/CC0 images across 8 location queries — the actual street, the actual Old Bailey.
    That is what carries a UK crime story visually when no lawful portrait exists.

Every image carries its licence and attribution, because CC-BY-SA requires credit.
"""

import re
import time

import requests

COMMONS_API  = "https://commons.wikimedia.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
UA = "BuriedCasefiles/1.0 (https://github.com/maxwilliamfoster-sys/youtube-automation)"

# Licences that permit reuse. Anything not on this list is skipped rather than risked.
_FREE_LICENCE = ("cc-by", "cc0", "cc-sa", "pd", "public")

# Commons is an archive, not a photo library: the same search returns book scans,
# PDFs, maps and Victorian engravings alongside photographs. A Dickens journal page
# is not "real UK footage", so only actual photographs are accepted.
_PHOTO_EXT = (".jpg", ".jpeg", ".png")
_NOT_A_PHOTO = re.compile(
    r"\.(pdf|djvu|svg|tif|tiff|ogv|webm)$"
    r"|\b(IA |archive\.org|journal|magazine|newspaper|manuscript|engraving|lithograph|woodcut|etching|map of|plan of|coat of arms|title page|frontispiece|illustration from)",
    re.I,
)
# Wikimedia serves originals, and some are enormous (a 95MB scan appeared in testing).
# Anything past this is skipped rather than downloaded.
_MAX_BYTES = 14_000_000

# UK place names are reused worldwide and are also breed/product names. A search for
# "Ipswich, Suffolk" returned Ipswich QUEENSLAND, Downtown Suffolk VIRGINIA and a
# Suffolk ram lamb — all of which reached a finished video about an Ipswich murder.
_WRONG_PLACE = re.compile(
    r"(queensland|australia|new south wales|victoria, au|"
    r"virginia|massachusetts|new hampshire|connecticut|vermont|"
    r", va|, ma|, nh|, ct|, ny|"
    r"new zealand|ontario|jamaica|barbados|south africa|"
    r"ram lamb|ewe|sheep|breed|cattle|pig|poultry|"
    r"coat of arms|flag of|logo|diagram)",
    re.I,
)

_session = None
_last = 0.0
MIN_INTERVAL = 0.4          # Wikimedia throttles shared cloud IPs hard


def _get(url: str, params: dict) -> dict:
    global _session, _last
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = UA
    gap = time.time() - _last
    if gap < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - gap)
    r = _session.get(url, params={**params, "format": "json"}, timeout=30)
    _last = time.time()
    r.raise_for_status()
    return r.json()


def _image_details(titles: list, min_width: int = 900) -> list:
    """URL, size, licence and attribution for Commons files. Free licences only."""
    if not titles:
        return []
    data = _get(COMMONS_API, {
        "action": "query", "titles": "|".join(titles[:40]),
        "prop": "imageinfo", "iiprop": "url|size|extmetadata",
    })
    out = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = (meta.get("License", {}).get("value", "") or "").lower()
        if not any(k in licence for k in _FREE_LICENCE):
            continue
        if info.get("width", 0) < min_width:
            continue
        name = page.get("title", "")[5:]
        if not name.lower().endswith(_PHOTO_EXT) or _NOT_A_PHOTO.search(name):
            continue
        if info.get("size", 0) > _MAX_BYTES:
            continue
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "") or "").strip()
        out.append({
            "title":   page.get("title", "")[5:],
            "url":     info.get("url", ""),
            "width":   info.get("width", 0),
            "height":  info.get("height", 0),
            "licence": meta.get("LicenseShortName", {}).get("value", licence),
            "credit":  artist[:80] or "Wikimedia Commons",
        })
    return out


def verified_person_image(person: str) -> dict:
    """
    A photograph that Wikidata states depicts `person` — or {} if none exists.

    The chain is deliberately strict: article title -> that article's Wikidata entity
    -> the entity's P18 image. Every step is an explicit link maintained by editors,
    so the returned file is asserted to depict this specific person. A name search
    against Commons would be far more productive and is exactly what must not be done:
    it would happily return a different person who shares a name.
    """
    try:
        page = _get(WIKIPEDIA_API, {
            "action": "query", "prop": "pageprops", "titles": person, "redirects": 1,
        })
        p = next(iter(page.get("query", {}).get("pages", {}).values()), {})
        qid = p.get("pageprops", {}).get("wikibase_item")
        if not qid:
            return {}

        ent = _get(WIKIDATA_ENTITY.format(qid), {})
        claims = ent.get("entities", {}).get(qid, {}).get("claims", {})
        if "P18" not in claims:
            return {}
        filename = claims["P18"][0]["mainsnak"]["datavalue"]["value"]

        # Portraits are shown as an inset card, not full-bleed, so a smaller file is
        # fine. At the 900px location threshold the single lawful portrait found in
        # testing (810px, CC BY 3.0) was being thrown away.
        details = _image_details([f"File:{filename}"], min_width=500)
        if not details:
            return {}
        found = details[0]
        found["depicts"] = person
        found["qid"] = qid
        found["verified_by"] = f"wikidata:{qid}#P18"
        return found
    except Exception as e:
        print(f"[UKMedia] portrait lookup failed for {person!r}: {e}")
        return {}


def location_images(place: str, limit: int = 6) -> list:
    """
    Free-licensed photographs of a real UK place.

    Locations are the visual backbone here, because lawful portraits are rare. A story
    set in Peckham can show Peckham — which is both true and far more specific than
    the interchangeable stock footage this channel used before.
    """
    try:
        # Anchor the search to the UK. Without it Commons happily returns the
        # same place name in Australia or the United States.
        found = _get(COMMONS_API, {
            "action": "query", "list": "search",
            "srsearch": f"{place} United Kingdom",
            "srnamespace": 6, "srlimit": limit * 4,
        })
        titles = [h["title"] for h in found.get("query", {}).get("search", [])]
        details = _image_details(titles)
        # Then drop anything the anchor did not catch — wrong country, or a breed
        # of sheep that happens to share the county's name.
        clean = [d for d in details if not _WRONG_PLACE.search(d["title"])]
        return clean[:limit]
    except Exception as e:
        print(f"[UKMedia] location search failed for {place!r}: {e}")
        return []


def clean_place(raw: str) -> list:
    """
    Turn a messy location string into searchable place names, best first.

    The research step returns prose, not a place: "Doncaster, South Yorkshire (last
    seen at King's Cross, London)". Commons finds nothing for that whole string, so
    it is split into candidates — the primary town, then the county, then any place
    named in the parenthetical, which is often where the case actually unfolded.
    """
    if not raw:
        return []
    out = []
    # Places mentioned inside brackets are usually the second location in the story.
    bracketed = re.findall(r"\(([^)]*)\)", raw)
    main = re.sub(r"\([^)]*\)", " ", raw)

    def _parts(text):
        # Split on commas and slashes; drop narrative fragments like "last seen at".
        for chunk in re.split(r"[,/;]| and ", text):
            chunk = re.sub(r"(last seen at|body found in|near|found in|in)", " ", chunk, flags=re.I)
            chunk = re.sub(r"\s+", " ", chunk).strip(" .-")
            # A place name is short and has no digits.
            if 2 < len(chunk) <= 40 and not re.search(r"\d", chunk):
                yield chunk

    out.extend(_parts(main))
    for b in bracketed:
        out.extend(_parts(b))

    seen, uniq = set(), []
    for p in out:
        k = p.lower()
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq[:4]
