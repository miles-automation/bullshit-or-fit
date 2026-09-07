import hashlib
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel, Field

PARSER_VERSION = "html-paragraphs-v1"
SECTION = re.compile(
    r"^item\s+(1[abc]?|[2-9]|1[0-6])\s*[.:—–-]?\s+(.{3,100}?)\s*$", re.I
)
BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "br"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class FilingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        hidden = (
            bool(self.stack and self.stack[-1][1])
            or tag in {"script", "style", "head", "ix:header", "ix:hidden"}
            or "hidden" in attributes
            or bool(
                re.search(r"display\s*:\s*none", attributes.get("style") or "", re.I)
            )
        )
        if tag not in VOID_TAGS:
            self.stack.append((tag, hidden))
        if not hidden:
            if tag in BLOCKS:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if not (self.stack and self.stack[-1][1]) and tag in BLOCKS:
            self.parts.append("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if not (self.stack and self.stack[-1][1]):
            self.parts.append(data)


def normalize(raw: bytes) -> str:
    decoded = raw.decode("utf-8-sig", errors="replace")
    if not re.search(r"<(?:html|body|div|p)[\s>]", decoded, re.I):
        raise ValueError("unsupported or malformed filing: expected HTML document")
    parser = FilingHTMLParser()
    parser.feed(decoded)
    parser.close()
    paragraphs = []
    for line in "".join(parser.parts).splitlines():
        cells = [re.sub(r"\s+", " ", cell).strip() for cell in line.split("\t")]
        line = " | ".join(cell for cell in cells if cell)
        if line:
            paragraphs.append(line)
    result = "\n\n".join(paragraphs)
    if len(result) < 200:
        raise ValueError("filing has insufficient readable text")
    if re.search(
        r"undeclared automated tool|request rate threshold exceeded", result, re.I
    ):
        raise ValueError("SEC access error returned instead of a filing")
    return result


@dataclass(frozen=True)
class Paragraph:
    index: int
    start: int
    end: int
    text: str
    section: str | None


def paragraphs(text: str) -> list[Paragraph]:
    result = []
    section = None
    for index, match in enumerate(re.finditer(r"[^\n]+", text)):
        heading = SECTION.fullmatch(match.group())
        if heading and not re.search(r"\.{2,}|\s\d+$", heading.group(2)):
            section = f"Item {heading.group(1).upper()}: {heading.group(2)}"
        result.append(
            Paragraph(index, match.start(), match.end(), match.group(), section)
        )
    return result


class Rule(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    category: str = Field(pattern=r"^[a-z0-9_]+$")
    pattern: str
    reported_pattern: str
    intention_pattern: str
    context_pattern: str = ""
    exclude_pattern: str = ""


class Rules(BaseModel):
    version: str
    rules: list[Rule]

    @property
    def fingerprint(self) -> str:
        payload = self.model_dump_json().encode()
        return f"{self.version}:{hashlib.sha256(payload).hexdigest()[:16]}"


def load_rules(path: str = "") -> Rules:
    source = Path(path) if path else Path(__file__).with_name("rules.json")
    rules = Rules.model_validate(json.loads(source.read_text()))
    if len({rule.category for rule in rules.rules}) != len(rules.rules):
        raise ValueError("one rule per category required")
    for rule in rules.rules:
        re.compile(rule.pattern, re.I)
        re.compile(rule.reported_pattern, re.I)
        re.compile(rule.intention_pattern, re.I)
        re.compile(rule.context_pattern, re.I)
        re.compile(rule.exclude_pattern, re.I)
    return rules


def classify(excerpt: str, section: str | None, rule: Rule) -> str:
    assertions: set[str] = set()
    for sentence in re.split(r"(?<=[.!?;])\s+", excerpt):
        match = re.search(rule.pattern, sentence, re.I)
        if match is None:
            continue
        hypothetical = bool(
            re.search(
                r"\b(?:may|might|could|if|risks?|potential|would)\b", sentence, re.I
            )
        )
        reported = bool(re.search(rule.reported_pattern, sentence, re.I))
        intention = bool(re.search(rule.intention_pattern, sentence, re.I))
        negated = bool(
            re.search(
                r"\b(?:(?:did not|do not|does not|have not|has not)\s+(?:implement|undertake|announce|complete|expect|plan|reduce|experience)|no plans to|no layoffs)\b[^.;!?]{0,80}$",
                sentence[: match.end()],
                re.I,
            )
        )
        historical_risk = hypothetical and bool(
            re.search(
                r"\b(?:experienced|have in the past|has in the past|have expanded|have increased)\b",
                sentence,
                re.I,
            )
        )
        if sum((hypothetical, reported, intention, negated)) > 1 or historical_risk:
            assertions.add("mixed")
        elif negated:
            assertions.add("negated")
        elif hypothetical:
            assertions.add("hypothetical_risk")
        elif intention:
            assertions.add("forward_looking_intention")
        elif reported and not (section and section.startswith("Item 1A:")):
            assertions.add("reported_event")
        else:
            assertions.add("unknown")
    supported = assertions - {"unknown"}
    return (
        next(iter(supported))
        if len(supported) == 1 and "mixed" not in supported
        else "unknown"
    )


@dataclass(frozen=True)
class Evidence:
    paragraph: Paragraph
    category: str
    rule_id: str
    assertion: str
    extractor_version: str


def extract(text: str, rules: Rules) -> list[Evidence]:
    patterns = [(rule, re.compile(rule.pattern, re.I)) for rule in rules.rules]
    version = rules.fingerprint
    return [
        Evidence(
            paragraph,
            rule.category,
            rule.id,
            classify(paragraph.text, paragraph.section, rule),
            version,
        )
        for paragraph in paragraphs(text)
        for rule, pattern in patterns
        if pattern.search(paragraph.text)
        and re.search(rule.context_pattern, paragraph.text, re.I)
        and not (
            rule.exclude_pattern
            and re.search(rule.exclude_pattern, paragraph.text, re.I)
        )
    ]
