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
# Anything past this is skipped rather than downloaded — when no thumbnail exists.
_MAX_BYTES = 14_000_000
# Always download a server-side scaled thumbnail, never the original. On 2026-10-10 a
# 14MB PNG passed the byte cap but decoded to 282 megapixels, and Pillow's
# decompression-bomb guard killed the whole video. 1920px is one of Wikimedia's
# standard thumbnail widths (cached, not rate-limited like arbitrary sizes) and is
# already the long edge of the 1080x1920 frame.
THUMB_WIDTH = 1920
# Originals above this are skipped outright: Commons often cannot thumbnail them
# (huge PNG/TIFF scans), and they are archive scans, not photographs, anyway.
_MAX_ORIGINAL_PIXELS = 100_000_000

# UK place names are reused worldwide and are also breed/product names. A search for
# "Ipswich, Suffolk" returned Ipswich QUEENSLAND, Downtown Suffolk VIRGINIA and a
# Suffolk ram lamb — all of which reached a finished video about an Ipswich murder.
# Only applied to UK cases: a US or Australian case legitimately wants those places.
_WRONG_COUNTRY = re.compile(
    r"(queensland|australia|new south wales|victoria, au|"
    r"virginia|massachusetts|new hampshire|connecticut|vermont|"
    r", va|, ma|, nh|, ct|, ny|"
    r"new zealand|ontario|jamaica|barbados|south africa)",
    re.I,
)
# Applied to every case.
_WRONG_PLACE = re.compile(
    r"(ram lamb|ewe|sheep|breed|cattle|pig|poultry|"
    # Off-topic subjects that matched the place name and broke the mood of a murder
    # story (a blue tit and a white double-decker in the Nicola Bulley video, 2026-10).
    r"\bbird|\btit\b|cyanistes|parus|robin|finch|warbler|heron|swan|duck|goose|gull|"
    r"butterfly|moth|insect|bee\b|flower|orchid|fungus|fungi|lichen|"
    r"\bbus\b|buses|coach|stagecoach|arriva|locomotive|railway station|train|tram|"
    r"steam|fleet|livery|aircraft|airport|"
    r"football|cricket|rugby|stadium|festival|carnival|parade|wedding|"
    r"coat of arms|flag of|logo|diagram|"
    # Found in the 2026-10-12 Philadelphia video (two brown-rat photos) and in
    # Commons results for other places: animals, museum paintings, shops and hotels.
    r"\brats?\b|rattus|mouse|mice|squirrel|\bcats?\b|\bdogs?\b|horse|\bfox\b|deer|\bcow|"
    r"painting|museum|gallery|portrait of|\(\d{4}\s*-\s*\d{4}\)|\bc\.\s?\d{4}|"
    r"supermarket|morrisons|tesco|sainsbury|asda|aldi|lidl|\bibis\b|premier inn|travelodge|hotel|"
    # Real, identifiable people (2026-10-13: "President Biden speaks in Philadelphia",
    # a 1942 portrait of Field Marshal Montgomery). An unrelated person beside crime
    # narration is a defamation risk, so anything that looks like a person/event goes.
    # (No king/queen/prince: King's Cross, Queen Street, Princes Street are places.)
    r"president|senator|governor|mayor|congress|minister|"
    r"\bjr\b|\bsr\b|\bgen\.|general |speaks|speech|rally|campaign|\bvisits?\b|meets|ceremony|"
    r"inaugurat|funeral|protest|march on|selfie|headshot|\bportrait|wedding|graduation)",
    re.I,
)

# Words that appear in many place names and so prove nothing about the photo.
_GENERIC_PLACE_WORDS = {
    "road", "street", "lane", "avenue", "county", "city", "town", "village", "united",
    "kingdom", "north", "south", "east", "west", "upper", "lower", "great", "little",
    "new", "saint", "park", "the", "and", "near", "borough", "district", "england",
    "scotland", "wales", "state", "states", "america", "usa",
}


def place_tokens(place: str) -> list:
    """The distinctive words of a place name ('Hemdean Road, Caversham' -> hemdean, caversham)."""
    words = re.findall(r"[a-z][a-z'\-]+", (place or "").lower())
    return [w for w in words if len(w) >= 4 and w not in _GENERIC_PLACE_WORDS]


# Regions that place names get reused across. A title naming one of these that the
# case's own location does not name is the wrong place: the Brianna Maitland video
# (Montgomery, VERMONT, 2026-10-12) showed Montgomery, Alabama for 8 of 12 scenes,
# including three copies of a "Greetings from Montgomery" postcard, and a ballpark in
# Vermont, ILLINOIS. Those two US videos had the month's worst watch time (12-15%).
_REGIONS = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|"
    "hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|"
    "massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|"
    "new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|"
    "oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|"
    "texas|utah|vermont|virginia|west virginia|wisconsin|wyoming|"
    "queensland|new south wales|tasmania|ontario|quebec|alberta|british columbia|"
    "australia|canada|new zealand|ireland|scotland|wales|england|germany|france|spain|"
    "mallorca|italy|sweden|norway|denmark|netherlands|belgium|austria|switzerland|"
    "jamaica|mexico|south africa|india"
)
# Washington, Georgia and Jordan are left out: they are common street names.
_REGION_RE = re.compile(rf"\b({_REGIONS})\b", re.I)
_UK_NATIONS = {"england", "scotland", "wales", "ireland"}


def wrong_region(title: str, case_place: str, uk: bool = False) -> bool:
    """True when the title names a region the case's location does not."""
    own = {m.lower() for m in _REGION_RE.findall(case_place or "")}
    if uk:
        own |= _UK_NATIONS
    named = {m.lower() for m in _REGION_RE.findall(title.replace("_", " "))}
    # "West Virginia" also contains "Virginia"; only count the longer name.
    named = {n for n in named if not any(n != o and n in o for o in named)}
    return bool(named - own) if (own or named) else False


_US_ABBR = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca",
    "colorado": "co", "connecticut": "ct", "delaware": "de", "florida": "fl", "hawaii": "hi",
    "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt",
    "virginia": "va", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}


def names_region(title: str, case_place: str) -> bool:
    """For a non-UK case whose location names a region: the title must name it too.

    Commons full-text search matches descriptions, so a bare town name pulls in
    namesakes and PEOPLE: "Montgomery Vermont" returned a 1942 portrait of Field
    Marshal Montgomery and a photo of Martin Luther King Jr. in Montgomery, Alabama.
    An unrelated real person in a crime video is a defamation risk, not just off-mood.
    Requiring the region in the title ("Montgomery, Vermont", "@Montgomery,VT")
    leaves fewer photos but right ones; the scene falls back to stock otherwise."""
    regions = {m.lower() for m in _REGION_RE.findall(case_place or "")}
    if not regions:
        return True
    t = title.replace("_", " ")
    for r in regions:
        if re.search(rf"\b{re.escape(r)}\b", t, re.I):
            return True
        ab = _US_ABBR.get(r)
        if ab and re.search(rf"(,|\s|@)\s?{ab}\b", t, re.I):
            return True
    return False


# Private property. The 21 Oct 2026 Kitandwe video showed "House and driveway, Wayford"
# (a Somerset cottage, nowhere near the London street in the case) with a legible
# number plate, under the word "MOTIVE". Viewers read any house beside murder narration
# as the crime scene or a suspect's home: a privacy and defamation risk for owners who
# have nothing to do with the case. There is no free, reliable plate blur, so homes and
# vehicles are dropped. Street signs, stations, churches, parks and courts are fine.
_PRIVATE_PROPERTY = re.compile(
    r"\b(houses?|homes|cottages?|farmhouses?|bungalows?|driveways?|gardens?|"
    r"residences?|residential|home of|cars parked|parked cars?|cars?|vans?|vehicles?)\b",
    re.I,
)


def private_property(title: str) -> bool:
    """True when a photo title points at a private home or a vehicle."""
    return bool(_PRIVATE_PROPERTY.search((title or "").replace("_", " ")))


# A case location that is a street. A bare street stem is not a place: "Wayford Street"
# matched "Wayford", a village 150 miles away, so a street needs its full name or its
# stem plus the case's own locality ("Wayford ... Battersea").
_STREET_SUFFIX = ("street", "road", "lane", "avenue", "drive", "close", "crescent", "terrace",
                  "grove", "gardens", "mews", "row", "walk", "way", "place")
_STREET_ABBR = {"street": "st", "road": "rd", "avenue": "ave", "lane": "ln", "drive": "dr",
                "crescent": "cres", "terrace": "tce"}
_STREET_RE = re.compile(rf"^(.+?)\s+({'|'.join(_STREET_SUFFIX)})$", re.I)


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9']+", " ", (text or "").lower())).strip()


def street_parts(place: str):
    """('wayford', 'street') for 'Wayford Street, Battersea'; None when it isn't a street."""
    first = _norm_title((place or "").split(",")[0])
    m = _STREET_RE.match(first)
    if not m:
        return None
    return m.group(1), m.group(2)


def names_place(title: str, place: str, locality: str = "") -> bool:
    """True when the file title names the place. Commons full-text search also matches
    descriptions, which is how 'Philadelphia' returned Amman (its ancient name) and
    rat photos whose description mentions the city.

    A street needs its full name ("Wayford Street"/"Wayford St"), or its stem plus a
    word from the case's locality. `locality` is the case's whole location string;
    when `place` itself has commas ("Wayford Street, Battersea") the rest is used."""
    street = street_parts(place)
    if street:
        stem, suffix = street
        t = _norm_title(title)
        phrases = [f"{stem} {suffix}"] + ([f"{stem} {_STREET_ABBR[suffix]}"] if suffix in _STREET_ABBR else [])
        if any(re.search(rf"\b{re.escape(p)}\b", t) for p in phrases):
            return True
        rest = ", ".join([locality or ""] + (place or "").split(",")[1:])
        stem_toks = set(place_tokens(stem))
        loc = [tok for tok in place_tokens(rest) if tok not in stem_toks]
        return bool(loc) and re.search(rf"\b{re.escape(stem)}\b", t) is not None \
            and any(re.search(rf"\b{re.escape(tok)}\b", t) for tok in loc)
    toks = place_tokens(place)
    if not toks:
        return True
    t = title.lower()
    return any(tok in t for tok in toks)

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
        "iiurlwidth": THUMB_WIDTH,
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
        if info.get("width", 0) * info.get("height", 0) > _MAX_ORIGINAL_PIXELS:
            continue
        thumb = info.get("thumburl", "")
        # Without a thumbnail we would be downloading the original: keep the old cap.
        if not thumb and info.get("size", 0) > _MAX_BYTES:
            continue
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "") or "").strip()
        if not artist and "by" in licence:
            # CC BY / BY-SA need the author credited; "Wikimedia Commons" is not the
            # author, and the fallback made publish_checks' attribution check dead.
            continue
        out.append({
            "title":   page.get("title", "")[5:],
            "url":     thumb or info.get("url", ""),
            "width":   info.get("thumbwidth") or info.get("width", 0),
            "height":  info.get("thumbheight") or info.get("height", 0),
            "licence": meta.get("LicenseShortName", {}).get("value", licence),
            "credit":  artist[:80] or "public domain",
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


def location_images(place: str, limit: int = 6, uk: bool = True, must_name: str = "",
                    case_place: str = "") -> list:
    """
    Free-licensed photographs of a real UK place.

    Locations are the visual backbone here, because lawful portraits are rare. A story
    set in Peckham can show Peckham — which is both true and far more specific than
    the interchangeable stock footage this channel used before.
    """
    try:
        # Anchor the search to the UK. Without it Commons happily returns the
        # same place name in Australia or the United States.
        # Non-UK cases (the channel went worldwide 2026-10-09) search the place as
        # given, which already names its country.
        found = _get(COMMONS_API, {
            "action": "query", "list": "search",
            "srsearch": f"{place} United Kingdom" if uk else place,
            "srnamespace": 6, "srlimit": limit * 4,
        })
        titles = [h["title"] for h in found.get("query", {}).get("search", [])]
        details = _image_details(titles)
        # Then drop anything the anchor did not catch — wrong country, or a breed
        # of sheep that happens to share the county's name.
        clean = [d for d in details if not _WRONG_PLACE.search(d["title"])
                 and not private_property(d["title"])
                 and not (uk and _WRONG_COUNTRY.search(d["title"]))
                 and names_place(d["title"], must_name or place, locality=case_place)
                 and not wrong_region(d["title"], case_place or place, uk)
                 and (uk or names_region(d["title"], case_place or place))]
        # Geograph is a project to photograph every grid square of Britain: plain,
        # documentary street photos, which is exactly the look these stories want.
        clean.sort(key=lambda d: "geograph" not in d["title"].lower())
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
