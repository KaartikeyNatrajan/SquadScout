"""Tagging a story's topic from its words.

A topic says what a story is *about*, never what it *concludes*. "injury" means
injury words appear; it doesn't mean the player named is injured (the headline
may say he's back from injury, or that a team-mate is). The model reads the text
and decides. Tagging errs towards tagging too much: a story wrongly tagged is
read and ignored, but a story missed is lost.
"""

import re

from fpl_data import names

# topic -> patterns, matched against the lowercase, accent-free text.
_PATTERNS: dict[str, list[str]] = {
    "injury": [
        r"injur\w*",
        r"ruled out",
        r"sidelined",
        r"surgery",
        r"hamstring",
        r"groin",
        r"calf",
        r"ankle",
        r"knee",
        r"muscle",
        r"fracture\w*",
        r"setback",
        r"knock",
        r"lay-?off",
        r"out until",
        r"out for",
        r"(?:will|to|could|may|set to|likely to) miss",
        r"blow for",
        r"scan",
        r"worr(?:y|ies|ied)",
    ],
    "fitness": [
        r"fitness",
        r"(?<!cast )(?<!casts )(?<!casting )(?<!no )doubts?(?! over| about| on| whether)",
        r"doubtful",
        r"fit to",
        r"late test",
        r"touch and go",
        r"race against time",
        r"recover\w*",
        r"unavailable",
        r"not available",
        r"available for",
        r"unlikely to play",
        r"will not play",
        r"not playing",
        r"not in (?:\w+ ){0,3}squad",
        r"misses out",
        r"absent",
        r"left out",
        r"unused sub(?:stitute)?",
    ],
    "return": [
        r"return(?:s|ed|ing)?",
        r"comeback",
        r"back in training",
        r"(?:spotted )?in training",
        r"back from",
        r"fit again",
        r"back in the squad",
    ],
    "suspension": [
        r"suspen\w+",
        r"banned?",
        r"red card",
        r"sent off",
        r"yellow cards?",
        r"serve a ban",
    ],
    "rest": [
        r"needs? (?:a )?rest",
        r"rest(?:ed|ing)?(?! of)",
        r"rotat\w+",
        r"load management",
        r"workload",
        r"minutes management",
    ],
    "team_news": [
        r"team news",
        r"line-?ups?",
        r"xis?",
        r"starting (?:xi|eleven|11)s?",
        r"predicted (?:xi|team)",
        r"confirmed (?:\w+ ){0,3}team",
        r"expected to start",
        r"dropped",
        r"benched",
        r"to start",
    ],
    "press_conference": [
        r"press conference",
        r"presser",
        r"news conference",
        r"pre-?match press",
        r"told reporters",
        r"speaking ahead of",
        r"speaking at",
    ],
}

_COMPILED = {
    topic: re.compile(r"(?<![a-z0-9])(?:" + "|".join(patterns) + r")(?![a-z0-9])")
    for topic, patterns in _PATTERNS.items()
}


def tag(text: str) -> tuple[list[str], list[str]]:
    """The topics found in `text`, and the words that triggered them (the snippet
    builder prefers sentences containing those)."""
    normalized = names.normalize(text).replace("’", "'")
    topics, words = [], []
    for topic, pattern in _COMPILED.items():
        found = [m.group(0) for m in pattern.finditer(normalized)]
        if found:
            topics.append(topic)
            words.extend(found)
    return topics, words
