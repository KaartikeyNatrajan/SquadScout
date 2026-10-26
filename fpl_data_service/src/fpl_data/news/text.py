"""Cleaning feed text, and building the snippet the language model is shown."""

import html
import re

from fpl_data import names

SNIPPET_LENGTH = 200

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_BOILERPLATE = re.compile(
    r"\s*(Continue reading\.\.\.|Read more\.\.\.|Read more|Click here.*)$", re.I
)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def clean_html(raw: str) -> str:
    """Plain text from an HTML snippet: no tags, entities decoded, one space between words."""
    text = html.unescape(_TAGS.sub(" ", html.unescape(raw)))
    text = _SPACES.sub(" ", text).strip()
    return _BOILERPLATE.sub("", text).strip()


def strip_outlet(title: str, source: str) -> str:
    """Google News ends every headline with " - Outlet Name"; remove it."""
    title = title.strip()
    if source and title.endswith(f" - {source}"):
        return title[: -len(source) - 3].rstrip()
    return title


def title_key(title: str) -> str:
    """A key that is the same for the same headline however it's capitalised or punctuated,
    so one story from two feeds is stored once."""
    return " ".join(re.findall(r"[a-z0-9]+", names.normalize(title)))


def build_snippet(title: str, summary: str, focus: list[str]) -> str:
    """The text the model is shown for a story: the headline, and when the feed gave a
    summary, the one sentence of it most worth reading. At most SNIPPET_LENGTH
    characters, cut at a word boundary.

    `focus` are words that make a sentence worth reading: the names of the players
    mentioned and the topic words found. The sentence with the most of them is used
    (the first one if none has any).
    """
    title = title.strip().rstrip(".")
    sentence = _best_sentence(summary, focus, avoid=title)
    text = f"{title}. {sentence}" if sentence else title
    return _truncate(text, SNIPPET_LENGTH)


def _best_sentence(summary: str, focus: list[str], avoid: str) -> str:
    sentences = [s.strip() for s in _SENTENCES.split(summary) if s.strip()]
    # A summary that just repeats the headline adds nothing.
    sentences = [s for s in sentences if names.normalize(avoid)[:40] not in names.normalize(s)]
    if not sentences:
        return ""
    words = [names.normalize(w) for w in focus if w]

    def score(sentence: str) -> int:
        normalized = names.normalize(sentence)
        return sum(1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}", normalized))

    best = max(sentences, key=score)  # max returns the first of equals, so ties favour the earliest
    return best if score(best) > 0 else sentences[0]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"
