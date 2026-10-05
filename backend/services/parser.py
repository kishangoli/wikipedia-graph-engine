"""Extract visible article-text links, excluding page furniture and reference sections."""

import re
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from backend.models import RawLink
from backend.services.errors import InvalidArticle, WikipediaError


PARSER_VERSION = 1
EXCLUDED_SECTIONS = {"references", "notes", "footnotes", "citations", "sources", "bibliography", "bibliographies", "further reading", "external links", "notes and references", "references and notes"}
EXCLUDED_CONTAINERS = "table, figure, figcaption, aside, nav, script, style, sup.reference, .reference, .references, .reflist, .refbegin, .infobox, .navbox, .sidebar, .hatnote, .metadata, .ambox, .ombox, .tmbox, .fmbox, .cmbox, .imbox, .thumb, .thumbcaption, .gallery, .toc, .mw-editsection, .mw-empty-elt, .catlinks, .shortdescription, .sistersitebox, [role='navigation']"
NON_ARTICLE_PREFIXES = {"talk", "user", "user talk", "wikipedia", "wikipedia talk", "wp", "project", "file", "image", "file talk", "media", "mediawiki", "mediawiki talk", "template", "template talk", "help", "help talk", "category", "category talk", "portal", "portal talk", "draft", "draft talk", "module", "module talk", "special", "timedtext", "book"}


def article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="()_,:")


def normalize_title(title: str) -> str:
    return " ".join(title.replace("_", " ").split())


def title_from_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or parsed.netloc.lower() != "en.wikipedia.org":
        raise InvalidArticle("Use an English Wikipedia article URL.")
    if parsed.path.startswith("/wiki/"):
        if parsed.query:
            raise InvalidArticle("Use a current article URL without query parameters.")
        title = unquote(parsed.path[6:])
    elif parsed.path == "/w/index.php":
        query = parse_qs(parsed.query)
        if set(query) - {"title", "action"} or query.get("action", ["view"]) != ["view"]:
            raise InvalidArticle("Use an article URL, not an edit or revision URL.")
        title = query.get("title", [""])[0]
    else:
        raise InvalidArticle("Use a /wiki/ article URL or an article title.")
    return normalize_title(title)


def parse_input(value: str) -> str:
    value = value.strip()
    if "://" in value or value.startswith("//"):
        value = title_from_url(value if not value.startswith("//") else "https:" + value)
    else:
        value = normalize_title(value)
    if not value or any(character in value for character in "|#[]{}<>\n\r"):
        raise InvalidArticle("Enter an exact article title or a valid article URL.")
    if value.partition(":")[0].lower() in NON_ARTICLE_PREFIXES and ":" in value:
        raise InvalidArticle("Only article pages are supported.")
    return value


def extract_links(html: str, source_title: str) -> list[RawLink]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output")
    if root is None:
        raise WikipediaError("Article HTML is missing its content container.")
    for element in root.select(EXCLUDED_CONTAINERS):
        if element.parent is not None:
            element.decompose()

    links = []
    seen = set()
    excluded_level = None
    for element in root.find_all(["h2", "h3", "h4", "h5", "h6", "a"]):
        if element.name != "a":
            level = int(element.name[1])
            if excluded_level is not None and level <= excluded_level:
                excluded_level = None
            labels = [element.get_text(" ", strip=True), element.get("id", "")]
            labels += [node.get("id", "") for node in element.find_all(id=True)]
            if any(re.sub(r"\s+", " ", label.replace("_", " ")).strip().lower() in EXCLUDED_SECTIONS for label in labels):
                excluded_level = level if excluded_level is None else min(level, excluded_level)
            continue
        if excluded_level is not None or element.find_parent(["p", "li", "dd", "dt"]) is None:
            continue
        href = element.get("href", "")
        phrase = element.get_text(" ", strip=True)
        if not phrase or not href or href.startswith("#") or set(element.get("class", [])) & {"new", "external", "interwiki", "extiw"}:
            continue
        url = urljoin(article_url(source_title), href)
        try:
            title = parse_input(url)
        except (InvalidArticle, ValueError):
            continue
        if title == normalize_title(source_title) or title in seen:
            continue
        seen.add(title)
        links.append(RawLink(title=title, anchor_text=phrase, url=url))
    return links
