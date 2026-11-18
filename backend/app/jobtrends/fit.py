from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.jobtrends.ats import SOURCE_ATS, SOURCE_REMOTE
from app.jobtrends.himalayas import SOURCE_HIMALAYAS
from app.jobtrends.models import AtsJob, DiscoveredBoard, FitDecision

FIT_SOURCES = (SOURCE_ATS, SOURCE_REMOTE, SOURCE_HIMALAYAS, "commute_shed", "adzuna")
MIN_COMP_MAX = 150_000
MAX_POSTING_AGE_DAYS = 30
SMALL_TEAM = 100

_ROLE = re.compile(r"\b(engineer|engineering|developer|architect)\b", re.I)
_EXCLUDED_ROLE = re.compile(
    r"\b(intern|internship|junior|jr|new grad|graduate|manager|director|head of|vp"
    r"|recruit\w*|sales|account|support|qa|test|tester|mechanical|electrical|hardware"
    r"|firmware|embedded|ios|android|mobile|network|civil|structural|field|clinical"
    r"|security|leader|customer|solutions|sales engineer)\b",
    re.I,
)
_EXCLUDED_LANGUAGE = re.compile(
    r"(\b(go|golang|rust|scala|elixir|php|kotlin|haskell|clojure|ocaml)\b|c\+\+)",
    re.I,
)
_COMMUTE = re.compile(r"\b(fort collins|cheyenne|laramie|loveland|windsor, co)\b", re.I)
_REMOTE = re.compile(r"\b(remote|anywhere|distributed|work from home|wfh)\b", re.I)
_US_STATES = (
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|"
    r"new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|"
    r"pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|"
    r"utah|vermont|virginia|washington|west virginia|wisconsin|wyoming"
)
_US = re.compile(
    r"\bu\.s\.|\b(us|usa|united states|americas|north america|worldwide|"
    + _US_STATES
    + r")\b",
    re.I,
)


def title_fits(title: str) -> bool:
    return bool(
        _ROLE.search(title)
        and not _EXCLUDED_ROLE.search(title)
        and not _EXCLUDED_LANGUAGE.search(title)
    )


def location_fits(location: str | None) -> bool:
    text = location or ""
    if _COMMUTE.search(text):
        return True
    if not _REMOTE.search(text):
        return False
    if _US.search(text):
        return True
    return not re.sub(r"[^a-z]+", "", _REMOTE.sub("", text.lower()))


CLEARANCE_REQUIRED = "required"
CLEARANCE_SPONSORED = "sponsored"

_CLEARANCE_TERM = re.compile(
    r"\b(?:(?:(?:active|current|interim|dod|dow)\s+)?(?:top\s+)?secret\s+(?:security\s+)?clearances?"
    r"|(?:active|current|interim|dod|dow)\s+(?:top\s+)?secret"
    r"|top secret"
    r"|ts/sci(?:\s+(?:security\s+)?clearances?)?"
    r"|public trust(?:\s+clearances?)?"
    r"|(?:security\s+)?clearances?)\b",
    re.I,
)
_CLAUSE = re.compile(
    r"\.(?=\s|$)|[;\n\u2022\u25cf\u25aa]|\s[-\u2013\u2014]\s|,(?!\s*or\b)"
)
_NEG_BEFORE = re.compile(
    r"(\b(no|without)\s+(?:[\w/-]+\s+){0,3}"
    r"|\b(?:do|does|will|did)\s+not\s+(?:need|require|have to have)\s+(?:[\w/-]+\s+){0,3}"
    r"|n't\s+(?:need|require)\s+(?:[\w/-]+\s+){0,3}"
    r"|\bnot\s+(?:required|needed|necessary|expected)\s+to\s+(?:have|possess|hold|maintain)\s+(?:[\w/-]+\s+){0,3})$",
    re.I,
)
_NEG_AFTER = re.compile(
    r"^[^.;]{0,35}?(\bnot (?:required|needed|necessary|a prerequisite)\b|\bnone)", re.I
)
_OBT_BEFORE = re.compile(
    r"(?<!visa )\b(obtain\w*|acquire|be granted|eligib\w*|sponsor\w*|willing\w*|future)\b[^.;]{0,50}$",
    re.I,
)
_OBT_AFTER = re.compile(
    r"^(\W*or\b[^.;]{0,40}?\b(obtain|eligib\w*|able to|willing\w*|acquire)"
    r"|\W*(?:required|requirement|level(?:\s+must\s+be)?)?\W*(?:the\s+)?(able|ability|eligib\w*)"
    r"\s+to\s+(obtain|acquire|get)"
    r"|\W*(post-start|after start|once hired|after hire)\b"
    r"|\s*,?\s*or\s+(?:be\s+able\s+to\s+|the\s+ability\s+to\s+|be\s+willing\s+to\s+)?"
    r"(?:pass|complete|undergo|obtain|acquire|qualify|submit)\b"
    r"|[^.;]{0,20}?\bmay be (?:required|needed)\b"
    r"|[^.;]{0,30}?(?<!visa )\bsponsor\w*)",
    re.I,
)
_OR_ALTERNATIVE = re.compile(
    r"^\s*,?\s*or\s+(?!(?:an?\s+)?(?:active|current|top secret|ts/sci|ts\b|secret|dod|interim"
    r"|higher|above|greater|better|equivalent|comparable|similar)\b)",
    re.I,
)
_STRICT_BEFORE = re.compile(
    r"\b(must|required|requires?|minimum|currently hold|currently possess)\b[^.;]{0,60}$",
    re.I,
)
_STRICT_AFTER = re.compile(
    r"^[^.;]{0,25}?\b(required|requires?|requirement|must|mandatory)\b", re.I
)
_ACTIVE_BEFORE = re.compile(r"\b(active|current|existing)\b[^.;]{0,35}$", re.I)
_ACTIVE_AFTER = re.compile(r"^[^.;]{0,25}?\((?:active|current)\)", re.I)
_SOFT_AFTER = re.compile(
    r"^\s*(?:or\s+[\w/-]+\s+)?(?:\([^)]{0,30}\)\s*)?(?:is|are|would be)?\s*(?:a\s+|strongly\s+|highly\s+)?"
    r"(preferred|desired|a plus|plus|bonus|nice[- ]to[- ]have|optional|advantageous)\b",
    re.I,
)
_SOFT_BEFORE = re.compile(
    r"\b(ideally|preferably|preference (?:for|given to)|nice if)\b[^.;]{0,50}$", re.I
)
_SOFT_LABEL_BEFORE = re.compile(
    r"\b(nice[- ]to[- ]have|preferred|bonus|desired|pluses)\b[^:]{0,30}:[^:]{0,45}$",
    re.I,
)
_BEFORE = 60
_LABEL = re.compile(
    r"^\s*((?:level|requirements?|required|type|status)\b[^.;:\n]{0,30})?:\s*", re.I
)
_LABEL_NONE = re.compile(
    r"^\s*(?:[Nn]one|NONE|N/A|n/a|[Nn]ot [Rr]equired|No|NO)(?![a-z])"
)
_LABEL_OBTAIN = re.compile(r"\b(obtain\w*|eligib\w*|sponsor\w*|acquire)\b", re.I)
_LABEL_HARD = re.compile(
    r"\b(active|current|must|required|requires?|requirement|minimum|possess|hold)\b|\((?:active|current)\)",
    re.I,
)


def _label_status(label: str, rest: str) -> str | None:
    if _LABEL_NONE.search(rest):
        return None
    head = f"{label} {rest[:80]}"
    if _LABEL_OBTAIN.search(head):
        return CLEARANCE_SPONSORED
    if _SOFT_AFTER.search(rest[:40]) or _SOFT_AFTER.search(f" {label}"):
        return CLEARANCE_SPONSORED
    if _LABEL_HARD.search(f"{label} {rest[:40]}"):
        return CLEARANCE_REQUIRED
    return None


def _clause_status(clause: str) -> str | None:
    status: str | None = None
    for m in _CLEARANCE_TERM.finditer(clause):
        before = clause[max(0, m.start() - _BEFORE) : m.start()]
        after = clause[m.end() : m.end() + 60]
        label = _LABEL.match(clause[m.end() :])
        if label:
            labelled = _label_status(
                label.group(1) or "", clause[m.end() + label.end() :]
            )
            if labelled == CLEARANCE_REQUIRED:
                return CLEARANCE_REQUIRED
            status = status or labelled
            continue
        if _NEG_BEFORE.search(before) or _NEG_AFTER.search(after):
            continue
        if _OBT_BEFORE.search(before) or _OBT_AFTER.search(after):
            status = status or CLEARANCE_SPONSORED
            continue
        if (
            _OR_ALTERNATIVE.search(after)
            and not _STRICT_BEFORE.search(before)
            and not _STRICT_AFTER.search(after)
        ):
            status = status or CLEARANCE_SPONSORED
            continue
        if (
            _SOFT_AFTER.search(after)
            or _SOFT_LABEL_BEFORE.search(before)
            or _SOFT_BEFORE.search(before)
        ) and not _STRICT_AFTER.search(after):
            status = status or CLEARANCE_SPONSORED
            continue
        term_and_before = before + m.group(0)
        if (
            _STRICT_BEFORE.search(before)
            or _STRICT_AFTER.search(after)
            or _ACTIVE_BEFORE.search(term_and_before)
            or _ACTIVE_AFTER.search(after)
        ):
            return CLEARANCE_REQUIRED
    return status


_SOFT_HEADING = re.compile(
    r"\b(preferred (?:qualifications|skills|experience|requirements)"
    r"|nice[- ]to[- ]haves?(?:\s+for\s+this\s+role)?\s*:"
    r"|bonus points|pluses|desired (?:skills|qualifications|experience)|what would be nice"
    r"|preferred\s*:)",
    re.I,
)
_HARD_HEADING = re.compile(
    r"\b(required (?:qualifications|skills|experience)|minimum qualifications|basic qualifications"
    r"|requirements\s*:|what you(?:'ll| will) need|must[- ]haves?|who you are|what you bring)",
    re.I,
)
_SOFT_LINE = re.compile(
    r"^\s*(nice[- ]to[- ]haves?|preferred|bonus|pluses|desired)(?:\s+for\s+this\s+role)?\s*:?\s*$",
    re.I | re.M,
)
_REQUIRED_LINE = re.compile(r"^\s*required\s*:", re.I | re.M)
_HEADING = re.compile(_SOFT_HEADING.pattern + "|" + _HARD_HEADING.pattern, re.I)
_EXPLICIT_REQUIRED = re.compile(
    r"\b(required|requires?|must|minimum|mandatory)\b", re.I
)


def _clause_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _CLAUSE.finditer(body):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(body)))
    return spans


def _soft_scope_marks(body: str) -> list[tuple[int, bool]]:
    marks = [(m.end(), True) for m in _SOFT_HEADING.finditer(body)]
    marks += [(m.end(), False) for m in _HARD_HEADING.finditer(body)]
    marks += [(m.end(), False) for m in _REQUIRED_LINE.finditer(body)]
    marks += [(m.end(), True) for m in _SOFT_LINE.finditer(body)]
    return sorted(marks)


def _in_soft_scope(marks: list[tuple[int, bool]], position: int) -> bool:
    current = False
    for at, soft in marks:
        if at > position:
            break
        current = soft
    return current


def clearance_status(text: str | None) -> str | None:
    if not _CLEARANCE_TERM.search(text or ""):
        return None
    body = re.sub(r"\bu\.s\.", "US", text or "", flags=re.I)
    body = _HEADING.sub(lambda m: "\n" + m.group(0), body)
    marks = _soft_scope_marks(body)
    result: str | None = None
    for start, end in _clause_spans(body):
        clause = body[start:end]
        status = _clause_status(clause)
        if status == CLEARANCE_REQUIRED:
            if _in_soft_scope(marks, end) and not _EXPLICIT_REQUIRED.search(clause):
                status = CLEARANCE_SPONSORED
            else:
                return CLEARANCE_REQUIRED
        result = result or status
    return result


_OTHER_LANGUAGE = re.compile(
    r"\b(rust|golang|kotlin|scala|haskell|elixir|clojure|ocaml|erlang|php)\b"
    r"|c\+\+",
    re.I,
)
_GO = re.compile(
    r"(?<=\bin )Go\b|(?<=\bwith )Go\b|(?<=\busing )Go\b|(?<=\bour )Go\b|(?<=\bof )Go\b"
    r"|(?<=, )Go\b|(?<=/)Go\b|(?<=\()Go\b"
    r"|\bGo\b(?=\s*(?:,|/|\)|or\b|developer|engineer|experience|expertise|skills?\b"
    r"|proficiency|knowledge|development|services?\b|code\b|programming|microservices))"
)
_MY_LANGUAGE = re.compile(
    r"(?<![\w.])(python|typescript|javascript|java|node\.?js|react|swift|c#|learnablelang)(?!\w)"
    r"|(?<!\w)\.net\b",
    re.I,
)
_STRICT_CUE = re.compile(
    r"\b(strong|deep|expert\w*|proficien\w*|fluen\w*|production\w*|professional|required"
    r"|requires?|must|years|primary|minimum)\b",
    re.I,
)
_ABILITY = re.compile(
    r"\b(experience\w*|expertise|proficien\w*|fluen\w*|skills?|years|knowledge|background"
    r"|programming|develop\w*|writ\w*|coding|build\w*|primary language|comfortable)\b",
    re.I,
)
_SOFT_CUE = re.compile(
    r"\b(nice to have|nice-to-have|bonus|a plus|plus|preferred|ideally|familiarity|exposure"
    r"|not required|optional|desirable|desired|helpful|advantageous|beneficial)\b",
    re.I,
)
_ANY_LANGUAGE = re.compile(
    r"\b(comparable|similar|equivalent|any modern|another|other)\s+"
    r"(?:[\w-]+\s+){0,2}languages?\b",
    re.I,
)
_LIST_GAP = re.compile(
    r"^[\s,/]*(?:[a-z][a-z-]*\s+){0,2}(?:,\s*)?"
    r"(?:(?:and/or|or|and)\b[\s,/]*(?:[a-z][a-z-]*\s+){0,3})?$",
    re.I,
)
_LIST_LEAD = re.compile(
    r"\b(such as|one of|at least one|any of|either)\b[^.;]{0,30}$", re.I
)
_WAIVER = re.compile(
    r"(\b(no|not|without)\b|n't\b)[^,;.]{0,40}\b(experience|required|necessary|needed|expected"
    r"|essential|mandatory|a must|a prerequisite|a requirement)\b"
    r"|\b(will|we'll|we will) teach\b|\bno prior\b",
    re.I,
)
_INTEGRATION = re.compile(
    r"\b(integrat\w*|interfac\w*|interoperat\w*|talk\w* to|consum\w*|migrat\w*)\b", re.I
)
_SENTENCE = re.compile(r"\.(?=\s|$)|[;\n\u2022]|\s-\s|\s\u2014\s")
_MODIFIER = re.compile(
    r"\s*(while|though|although|but|which|if|when|is|are|was|not|ideally|preferably)\b",
    re.I,
)
_COMMA = re.compile(r",(?!\s*or\b)")


def _other_language_mentions(text: str) -> int:
    return len(_OTHER_LANGUAGE.findall(text)) + len(_GO.findall(text))


_LEARN_CUE = re.compile(
    r"\b(?:desire|willing(?:ness)?|eager(?:ness)?|open|happy|excited|keen|able)\s+to\s+learn\b",
    re.I,
)
_LEARNABLE_TOKEN = "learnablelang"
_LEARN_END = re.compile(
    r"\s*(?:$|[.,;:!?)\n]|(?:and|or)\b|on the job\b|as needed\b|if needed\b|quickly\b"
    r"|is a plus\b|a plus\b)",
    re.I,
)
_NEXT_BULLET = re.compile(r"\s+[A-Z][a-z]+\s+[a-z]")
_HARD_CUE = r"(?:must|require|required|requires|minimum|mandatory|\d+\+?\s*years?)"
_STRICT_LANGUAGE_CUE = re.compile(
    r"\b(?:production|strong|deep|expert\w*|proficien\w*|years|minimum|required|must)\b",
    re.I,
)
_EXPERIENCE_NOUN = re.compile(
    r"\b(?:experience|expertise|proficiency|skills?|years|background|fluency)\b", re.I
)


def _language_pattern(language: str) -> str:
    if language in ("go", "golang"):
        return r"(?:\bGo\b|\b[Gg]olang\b)"
    return r"(?<![\w+])" + re.escape(language) + r"(?![\w+])"


def _language_flags(language: str) -> int:
    return 0 if language in ("go", "golang") else re.I


def _phrase_languages(body: str, cue: re.Match[str]) -> list[tuple[str, int, int]]:
    window = re.split(r"[.;\n]", body[cue.end() : cue.end() + 40], maxsplit=1)[0]
    found = [
        (x.group(0).lower(), cue.end() + x.start(), cue.end() + x.end())
        for x in _OTHER_LANGUAGE.finditer(window)
    ]
    for go in re.finditer(r"\b(?:Go|[Gg]olang)\b", window):
        found.append(("go", cue.end() + go.start(), cue.end() + go.end()))
    found.sort(key=lambda item: item[1])
    phrase: list[tuple[str, int, int]] = []
    previous = cue.end()
    for language, start, stop in found:
        gap = body[previous:start]
        if phrase and not _BARE_GAP.match(gap):
            break
        if not phrase and (
            not re.match(
                r"^\s+(?:(?!(?:but|and|or|while|however|though)\b)[\w-]+\s+){0,2}$",
                gap,
                re.I,
            )
            or _MY_LANGUAGE.search(gap)
        ):
            break
        phrase.append((language, start, stop))
        previous = stop
    return phrase


def _neutralize_learning_phrases(body: str) -> tuple[str, set[str]]:
    learnable: set[str] = set()
    pieces: list[str] = []
    cursor = 0
    for cue in _LEARN_CUE.finditer(body):
        for language, start, stop in sorted(
            _phrase_languages(body, cue), key=lambda x: x[1]
        ):
            if start < cursor:
                continue
            rest = body[stop : stop + 30]
            if _LEARN_END.match(rest) or _NEXT_BULLET.match(rest):
                learnable.add(language)
            pieces.append(body[cursor:start])
            pieces.append(_LEARNABLE_TOKEN)
            cursor = stop
    pieces.append(body[cursor:])
    return "".join(pieces), learnable


def _explicitly_required(body: str, language: str) -> bool:
    lang = _language_pattern(language)
    flags = _language_flags(language)
    if re.search(r"(?i:" + _HARD_CUE + r")[^.;\n]{0,60}" + lang, body, flags):
        return True
    if re.search(
        lang + r"[^.;\n]{0,40}(?i:\b(?:required|is a must|mandatory)\b)", body, flags
    ):
        return True
    for m in re.finditer(lang, body, flags):
        before = re.split(r"[.;\n]", body[max(0, m.start() - 20) : m.start()])[-1]
        after = re.split(r"[.;\n]", body[m.end() : m.end() + 20])[0]
        around = body[max(0, m.start() - 40) : m.end() + 40]
        if _EXPERIENCE_NOUN.search(
            before + " " + after
        ) and _STRICT_LANGUAGE_CUE.search(around):
            return True
    return False


def _neutralize_learnable(body: str) -> str:
    body, learnable = _neutralize_learning_phrases(body)
    for language in learnable:
        if not _explicitly_required(body, language):
            body = re.sub(
                _language_pattern(language),
                _LEARNABLE_TOKEN,
                body,
                flags=_language_flags(language),
            )
    return body


def _list_gap(gap: str) -> bool:
    cleaned = re.sub(r"[()]", " ", gap)
    cleaned = re.sub(r"\bc\s*/\s*$", " ", cleaned, flags=re.I)
    return bool(_LIST_GAP.match(cleaned))


def _language_spans(text: str) -> list[tuple[int, int, bool]]:
    spans = [(m.start(), m.end(), False) for m in _OTHER_LANGUAGE.finditer(text)]
    spans += [(m.start(), m.end(), False) for m in _GO.finditer(text)]
    spans += [(m.start(), m.end(), True) for m in _MY_LANGUAGE.finditer(text)]
    return sorted(spans)


def _language_groups(
    sentence: str,
) -> list[tuple[list[tuple[int, int, bool]], list[str]]]:
    spans = _language_spans(sentence)
    groups: list[tuple[list[tuple[int, int, bool]], list[str]]] = []
    index = 0
    while index < len(spans):
        group = [spans[index]]
        gaps: list[str] = []
        while index + 1 < len(spans):
            gap = sentence[group[-1][1] : spans[index + 1][0]]
            if not _list_gap(gap):
                break
            gaps.append(gap)
            index += 1
            group.append(spans[index])
        index += 1
        groups.append((group, gaps))
    return groups


_BARE_GAP = re.compile(
    r"^[\s,/()]*(?:(?:and/or|or|and)\b[\s,/()]*)?"
    r"(?:(?:modern|idiomatic|recent|contemporary|typed|native|functional|object-oriented)\s+){0,2}$",
    re.I,
)


def _is_conjunction(gap: str) -> bool:
    text = gap.lower().replace("and/or", "or")
    return bool(re.search(r"\band\b", text)) and not re.search(r"\bor\b|/", text)


def _group_excused(
    sentence: str, group: list[tuple[int, int, bool]], gaps: list[str]
) -> bool:
    lead = sentence[max(0, group[0][0] - 60) : group[0][0]]
    in_lead = bool(_LIST_LEAD.search(lead))
    segments: list[tuple[list[bool], bool]] = [([group[0][2]], in_lead)]
    for gap, span in zip(gaps, group[1:], strict=True):
        if in_lead and _BARE_GAP.match(gap):
            segments[-1] = (segments[-1][0] + [span[2]], True)
            continue
        in_lead = False
        if _is_conjunction(gap):
            segments.append(([span[2]], False))
            continue
        members, has_or = segments[-1]
        alternative = has_or or bool(re.search(r"\bor\b|/", gap, re.I))
        segments[-1] = (members + [span[2]], alternative)
    for members, has_or in segments:
        if all(members):
            continue
        if not (any(members) and has_or):
            return False
    return True


def _offers_familiar_alternative(sentence: str) -> bool:
    blocked_groups = [
        (group, gaps)
        for group, gaps in _language_groups(sentence)
        if not all(mine for *_, mine in group)
    ]
    return bool(blocked_groups) and all(
        _group_excused(sentence, group, gaps) for group, gaps in blocked_groups
    )


def _integration_only(clause: str) -> bool:
    for m in _INTEGRATION.finditer(clause):
        tail = clause[m.end() : m.end() + 40]
        if _other_language_mentions(tail):
            return True
    return False


def language_blocks(text: str | None) -> bool:
    body = _neutralize_learnable((text or "").replace("\u2019", "'"))
    mine = bool(_MY_LANGUAGE.search(body))
    marks = _soft_scope_marks(body)
    mandatory = 0
    start = 0
    spans: list[tuple[int, int]] = []
    for m in _SENTENCE.finditer(body):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(body)))
    for begin, end in spans:
        sentence = body[begin:end]
        if not _other_language_mentions(sentence):
            continue
        if _in_soft_scope(marks, end):
            continue
        if _ANY_LANGUAGE.search(sentence):
            continue
        if _offers_familiar_alternative(sentence):
            continue
        clauses = _COMMA.split(sentence)
        for index, clause in enumerate(clauses):
            mentions = _other_language_mentions(clause)
            if not mentions:
                continue
            scope = clause
            for following in (
                [] if _EXPLICIT_REQUIRED.search(clause) else clauses[index + 1 :]
            ):
                if _other_language_mentions(following) or not _MODIFIER.match(
                    following
                ):
                    break
                scope += "," + following
            if _SOFT_CUE.search(scope) or _WAIVER.search(scope):
                continue
            if _integration_only(clause) or _offers_familiar_alternative(clause):
                continue
            if not _ABILITY.search(clause):
                mandatory += mentions
                continue
            if _STRICT_CUE.search(clause) or not mine:
                return True
            mandatory += mentions
    return mandatory >= 2 and not mine


def posting_fresh(posted_at: datetime | None, oldest: datetime) -> bool:
    return posted_at is None or posted_at >= oldest


def comp_fits(comp_max: int | None, currency: str | None) -> bool:
    if comp_max is None:
        return True
    if currency not in (None, "USD"):
        return False
    return comp_max >= MIN_COMP_MAX


@dataclass(frozen=True)
class FitRole:
    source: str
    company_name: str
    title: str
    location: str | None
    url: str | None
    comp_min: int | None
    comp_max: int | None
    team_size: int | None
    first_seen: datetime
    clearance: str | None = None
    posted_at: datetime | None = None
    traceable: bool = True

    @property
    def listed_at(self) -> datetime:
        return self.posted_at or self.first_seen


def rank_key(role: FitRole) -> tuple[int, int, float, int]:
    small = 0 if role.team_size is not None and role.team_size <= SMALL_TEAM else 1
    untraceable = 0 if role.traceable else 1
    return (small, untraceable, -role.listed_at.timestamp(), -(role.comp_max or 0))


FIRST_PARTY_SOURCES = (SOURCE_ATS, "commute_shed")
_COMPANY_NOISE = re.compile(
    r"\b(inc|incorporated|llc|llp|plc|gmbh|ltd|limited|corp|corporation|co|company"
    r"|technologies|technology|the)\b"
)


def normalize_company(name: str) -> str:
    text = re.sub(r"(?<=\w)\.(?=\w)|\.$", "", name.lower()).replace("&", " and ")
    text = _COMPANY_NOISE.sub(" ", re.sub(r"[^a-z0-9&]+", " ", text))
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_title(title: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9+#]+", " ", title.lower()).split())


def first_party_index(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for company, title in pairs:
        index.setdefault(normalize_company(company), set()).add(normalize_title(title))
    return index


def is_traceable(
    source: str, company: str, title: str, index: dict[str, set[str]]
) -> bool:
    if source != SOURCE_HIMALAYAS:
        return True
    return normalize_title(title) in index.get(normalize_company(company), set())


_GREENHOUSE_JOB = re.compile(r"/jobs/(\d+)")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_APPLY_SUFFIX = re.compile(r"/(?:application|apply)$")
PROVIDER_KEY_PREFIXES = ("greenhouse:", "ashby:", "lever:")


def url_key(url: str | None) -> str | None:
    if not url or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        query = parse_qs(parts.query)
    except ValueError:
        return None
    if not host:
        return None
    path = parts.path.lower()
    gh_jid = query.get("gh_jid")
    if gh_jid:
        return f"greenhouse:{gh_jid[0]}"
    if host.endswith("greenhouse.io"):
        match = _GREENHOUSE_JOB.search(path)
        if match:
            return f"greenhouse:{match.group(1)}"
        token = query.get("token")
        if token and token[0].isdigit():
            return f"greenhouse:{token[0]}"
    for suffix, provider in (("ashbyhq.com", "ashby"), ("lever.co", "lever")):
        if host.endswith(suffix):
            match = _UUID.search(path)
            if match:
                return f"{provider}:{match.group(0)}"
    return host + _APPLY_SUFFIX.sub("", path.rstrip("/"))


def is_provider_key(key: str | None) -> bool:
    return key is not None and key.startswith(PROVIDER_KEY_PREFIXES)


@dataclass(frozen=True)
class DecidedIndex:
    url_keys: frozenset[str] = frozenset()
    roles: frozenset[tuple[str, str]] = frozenset()
    requisition_roles: frozenset[tuple[str, str]] = frozenset()

    def covers(self, company: str, title: str, url: str | None) -> bool:
        key = url_key(url)
        if key is not None and key in self.url_keys:
            return True
        role = (normalize_company(company), normalize_title(title))
        if role in self.roles:
            return True
        return role in self.requisition_roles and not is_provider_key(key)


def build_decided_index(
    decisions: list[tuple[str | None, str, str]],
    postings: list[tuple[str, str, str | None]],
) -> DecidedIndex:
    keys = frozenset(key for key, _, _ in decisions if key)
    roles: set[tuple[str, str]] = set()
    requisition_roles: set[tuple[str, str]] = set()
    for key, company_key, title_key in decisions:
        target = requisition_roles if is_provider_key(key) else roles
        target.add((company_key, title_key))
    for company, title, url in postings:
        key = url_key(url)
        if key is not None and key in keys:
            role = (normalize_company(company), normalize_title(title))
            target = requisition_roles if is_provider_key(key) else roles
            target.add(role)
    return DecidedIndex(
        url_keys=keys,
        roles=frozenset(roles),
        requisition_roles=frozenset(requisition_roles),
    )


@dataclass(frozen=True)
class FitList:
    roles: list[FitRole]
    hidden: int = 0


def fit_roles(
    session: Session,
    *,
    days: int = 1,
    max_age_days: int = MAX_POSTING_AGE_DAYS,
    now: datetime | None = None,
) -> FitList:
    moment = now or datetime.now(tz=UTC)
    since = moment - timedelta(days=days)
    oldest = moment - timedelta(days=max_age_days)
    rows = session.execute(
        select(
            AtsJob.source,
            AtsJob.company_name,
            AtsJob.title,
            AtsJob.location,
            AtsJob.url,
            AtsJob.comp_min,
            AtsJob.comp_max,
            AtsJob.comp_currency,
            AtsJob.first_seen,
            AtsJob.posted_at,
            AtsJob.content_text,
            DiscoveredBoard.team_size,
        )
        .outerjoin(
            DiscoveredBoard,
            and_(
                AtsJob.source == SOURCE_ATS,
                DiscoveredBoard.found.is_(True),
                DiscoveredBoard.provider == AtsJob.provider,
                DiscoveredBoard.token == AtsJob.company_token,
            ),
        )
        .where(
            AtsJob.is_open.is_(True),
            AtsJob.first_seen >= since,
            AtsJob.source.in_(FIT_SOURCES),
            or_(AtsJob.expires_at.is_(None), AtsJob.expires_at > moment),
        )
    ).all()
    postings = [
        (r.company_name, r.title, r.url)
        for r in session.execute(
            select(AtsJob.company_name, AtsJob.title, AtsJob.url).where(
                AtsJob.is_open.is_(True), AtsJob.source.in_(FIRST_PARTY_SOURCES)
            )
        ).all()
    ]
    index = first_party_index([(company, title) for company, title, _ in postings])
    decided = build_decided_index(
        [
            (r.url_key, r.company_key, r.title_key)
            for r in session.execute(
                select(
                    FitDecision.url_key, FitDecision.company_key, FitDecision.title_key
                )
            ).all()
        ],
        postings,
    )
    roles: dict[tuple[str, str, str | None], FitRole] = {}
    hidden: set[tuple[str, str]] = set()
    for r in rows:
        if not (
            title_fits(r.title)
            and location_fits(r.location)
            and comp_fits(r.comp_max, r.comp_currency)
        ):
            continue
        if not posting_fresh(r.posted_at, oldest):
            continue
        if language_blocks(r.content_text):
            continue
        clearance = clearance_status(f"{r.title}\n{r.content_text}")
        if clearance == CLEARANCE_REQUIRED:
            continue
        key = (r.company_name.lower(), r.title.lower(), r.location)
        if decided.covers(r.company_name, r.title, r.url):
            hidden.add((normalize_company(r.company_name), normalize_title(r.title)))
            continue
        role = FitRole(
            source=r.source,
            company_name=r.company_name,
            title=r.title,
            location=r.location,
            url=r.url,
            comp_min=r.comp_min,
            comp_max=r.comp_max,
            team_size=r.team_size,
            first_seen=r.first_seen,
            clearance=clearance,
            posted_at=r.posted_at,
            traceable=is_traceable(r.source, r.company_name, r.title, index),
        )
        current = roles.get(key)
        if current is None or rank_key(role) < rank_key(current):
            roles[key] = role
    return FitList(roles=sorted(roles.values(), key=rank_key), hidden=len(hidden))


def _pay(role: FitRole) -> str:
    if role.comp_max is None:
        return "pay not listed"
    if role.comp_min is None or role.comp_min == role.comp_max:
        return f"${role.comp_max // 1000}k"
    return f"${role.comp_min // 1000}k-${role.comp_max // 1000}k"


def _age(role: FitRole, now: datetime) -> str:
    days = max(0, (now - role.listed_at).days)
    label = "posted" if role.posted_at is not None else "first seen"
    return f"{label} today" if days == 0 else f"{label} {days}d ago"


def _hidden_line(hidden: int) -> str:
    noun = "role" if hidden == 1 else "roles"
    return f"hidden: {hidden} {noun} already in the application tracker"


def format_fit_list(
    roles: list[FitRole], days: int, now: datetime | None = None, hidden: int = 0
) -> str:
    moment = now or datetime.now(tz=UTC)
    if not roles:
        return f"no new fitting roles in the last {days} day(s)\n{_hidden_line(hidden)}"
    lines = [f"{len(roles)} new fitting roles in the last {days} day(s)", ""]
    for r in roles:
        size = f", {r.team_size} people" if r.team_size is not None else ""
        lines.append(
            f"- {r.company_name}{size}: {r.title} ({r.location or 'location not listed'}; {_pay(r)}; {_age(r, moment)})"
        )
        tag = f" [clearance: {r.clearance}]" if r.clearance else ""
        if not r.traceable:
            tag += " [not on company site]"
        lines.append(f"  {r.url or 'no link'}  [{r.source}]{tag}")
    lines.extend(["", _hidden_line(hidden)])
    return "\n".join(lines)
