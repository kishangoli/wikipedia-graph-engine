from pathlib import Path

import pytest

from backend.services.errors import InvalidArticle, WikipediaError
from backend.services.parser import extract_links, parse_input


def test_only_article_prose_and_lists_are_traversed():
    html = (Path(__file__).parent / "fixtures" / "article.html").read_text()
    links = extract_links(html, "Cat")
    assert [link.title for link in links] == ["Domestic cat", "Mammal", "Ancient Egypt", "Star Trek: Discovery", "Café", "Feline"]
    assert links[0].anchor_text == "domestic cat"
    assert links[1].url.endswith("Mammal#Characteristics")


@pytest.mark.parametrize("value, title", [("  domestic_cat  ", "domestic cat"), ("https://en.wikipedia.org/wiki/Caf%C3%A9#History", "Café"), ("https://en.wikipedia.org/w/index.php?title=Cat&action=view", "Cat"), ("Star Trek: Discovery", "Star Trek: Discovery")])
def test_input_normalization(value, title):
    assert parse_input(value) == title


@pytest.mark.parametrize("value", ["", "  ", "https://example.com/wiki/Cat", "https://fr.wikipedia.org/wiki/Chat", "https://en.wikipedia.org.evil.test/wiki/Cat", "https://en.wikipedia.org/w/index.php?title=Cat&action=edit", "Category:Cats", "Cat|Dog", "Cat#History", "https://en.wikipedia.org/wiki/Cat?oldid=12"])
def test_invalid_or_non_article_inputs_are_rejected(value):
    with pytest.raises(InvalidArticle):
        parse_input(value)


def test_missing_content_container_is_not_an_empty_article():
    with pytest.raises(WikipediaError):
        extract_links("<html><p>not article content</p></html>", "Cat")


def test_valid_empty_article():
    assert extract_links('<div class="mw-parser-output"><p>No links.</p></div>', "Cat") == []
