"""Parse free-text requests (English, French, Darija) into a slip request."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
import unicodedata

from slipbot.leagues import LEAGUES

DEFAULT_COUNT = 5
MAX_COUNT = 20

NUMBER_WORDS = {
    1: 'one un une wa7ed wahed wahd 1 واحد',
    2: 'two deux jouj zouj joj zoj juj 2 جوج زوج اثنين',
    3: 'three trois tlata tlta tlat 3 ثلاثة تلاتة',
    4: 'four quatre rb3a reb3a rab3a arba3a 4 اربعة أربعة ربعة',
    5: 'five cinq khamsa 5msa khmsa 5 خمسة',
    6: 'six setta stta sitta 6 ستة',
    7: 'seven sept sb3a seb3a sab3a 7 سبعة',
    8: 'eight huit tmnya tmanya tmenya 8 ثمانية تمنية',
    9: 'nine neuf ts3od tes3od tis3a 9 تسعة',
    10: 'ten dix 3chra 3achra 3shra 10 عشرة',
}
WORD_TO_NUMBER = {w: n for n, words in NUMBER_WORDS.items() for w in words.split()}
MATCH_WORDS = r'(?:matche?s?|matchat|matshat|games?|picks?|selections?|matchs|ماتشات|مباريات|مباراة|ماتش)'
WEEKDAYS = {
    0: 'monday lundi letnin tnin', 1: 'tuesday mardi tlat', 2: 'wednesday mercredi larb3',
    3: 'thursday jeudi lkhmis khmis', 4: 'friday vendredi jem3a jom3a',
    5: 'saturday samedi sebt ssebt', 6: 'sunday dimanche l7ed lhed 7ed',
}
TODAY = r'\b(?:today|tonight|aujourd\'?hui|ce soir|lyoum|lyouma|lyom|lioum)\b|اليوم'
TOMORROW = r'\b(?:tomorrow|demain|ghedda|ghda|ghadda|ghedwa)\b|غدا|غدوة'


@dataclass(frozen=True)
class Request:
    count: int
    leagues: tuple[str, ...]   # league codes asked for; empty = search all supported leagues
    day: date | None           # None: not given (the app then uses today)


def _normalise(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    return text.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))


def _count(text, default=DEFAULT_COUNT):
    tokens = re.findall(r'[\w\']+', text)
    # Prefer a number directly before a match word: "4 matchat", "five games".
    for i, token in enumerate(tokens[:-1]):
        if token in WORD_TO_NUMBER and re.fullmatch(MATCH_WORDS, tokens[i + 1]):
            return WORD_TO_NUMBER[token]
        if token.isdigit() and re.fullmatch(MATCH_WORDS, tokens[i + 1]):
            return int(token)
    no_dates = re.sub(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|ligue\s*1|serie a|liga portugal 1|\b\d{1,2}h\d*', ' ', text)
    digits = [int(d) for d in re.findall(r'\b\d{1,2}\b', no_dates)]
    if digits:
        return digits[0]
    # 'un'/'une' are articles too often to trust without a match word next to them.
    words = [WORD_TO_NUMBER[t] for t in re.findall(r'[\w\']+', no_dates)
             if t in WORD_TO_NUMBER and not t.isdigit() and t not in ('un', 'une')]
    return words[0] if words else default


def _leagues(text):
    """Explicitly requested competitions; empty means "search widely"."""
    found = [code for code, league in LEAGUES.items()
             if any(re.search(rf'(?<!\w){re.escape(alias)}(?!\w)', text) for alias in league.aliases)]
    # "Austrian Bundesliga" names Austria, not Germany.
    if 'AT1' in found and 'BL1' in found and not re.search(r'germany|german|allemagne|almania', text):
        found.remove('BL1')
    return tuple(found)


def _day(text, today):
    if m := re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text):
        return date(*map(int, m.groups()))
    if m := re.search(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b', text):
        day, month, year = int(m[1]), int(m[2]), m[3]
        year = int(year) + (2000 if year and len(year) == 2 else 0) if year else today.year
        parsed = date(year, month, day)
        return parsed if m[3] or parsed >= today else date(year + 1, month, day)
    if re.search(TOMORROW, text):
        return today + timedelta(days=1)
    if re.search(TODAY, text):
        return today
    for weekday, words in WEEKDAYS.items():
        if any(re.search(rf'\b{w}\b', text) for w in words.split()):
            return today + timedelta(days=(weekday - today.weekday()) % 7)
    return None


def parse_count(answer):
    """Answer to "How many matches?" -> count, or None if it contains no number."""
    text = _normalise(answer or '')
    if not text.strip():
        return None
    count = _count(text, default=None)
    return None if count is None else max(1, min(MAX_COUNT, count))


def parse_date(answer, today):
    """Answer to "Date?" -> date. Empty means today. Accepts today/tomorrow (also in French
    and Darija), DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY and YYYY-MM-DD. None if unrecognised."""
    text = _normalise(answer or '').strip()
    if not text:
        return today
    text = re.sub(r'\b(\d{1,2})[.-](\d{1,2})[.-](\d{4})\b', r'\1/\2/\3', text)
    try:
        return _day(text, today)
    except ValueError:      # e.g. 31/02/2026
        return None


def parse(text, today=None):
    """Turn a request like '3tini wr9a fiha 4 matchat' into a Request."""
    text = _normalise(text or '')
    today = today or datetime.now().astimezone().date()
    count = max(1, min(MAX_COUNT, _count(text)))
    try:
        day = _day(text, today)
    except ValueError:
        day = None
    return Request(count=count, leagues=_leagues(text), day=day)
