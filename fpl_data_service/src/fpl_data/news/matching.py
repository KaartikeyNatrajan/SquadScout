"""Finding which players and teams a story mentions.

Names are the hard part. "Silva" is two or three FPL players, "Gabriel" is one
player's whole name and another's first name, and "Wood" or "White" are also
ordinary words. Rules, from most to least certain:

1. A full name ("Bukayo Saka", "Bruno Fernandes") always matches.
2. A surname of at least 4 letters used by one player matches on its own.
3. A surname shared by several players, or an everyday word ("White", "King"),
   matches only with team context: the story is in a team's search feed, or it
   names the player's club. Without context it is skipped. One missed story is
   cheaper than a story pinned on the wrong player.
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from fpl_data import names
from fpl_data.db.players import PlayerRow
from fpl_data.db.teams import TeamRow
from fpl_data.news.feeds import TEAM_SEARCH_NAMES

MIN_SURNAME_LENGTH = 4

# Surnames that are also ordinary words: only trusted with team context. (Every
# one-word surname must also be written with a capital letter in the story.)
COMMON_WORDS = frozenset(
    "white young king wood green brown gray grey cook march bell mount hall long "
    "gordon lewis wilson cash rice strand hill".split()
)

# Other names newspapers use for a club (besides its FPL name and search name).
TEAM_NICKNAMES = {
    "Man City": ["man city"],
    "Man Utd": ["man utd", "man united"],
    "Spurs": ["spurs", "tottenham"],
    "Nott'm Forest": ["nott'm forest", "nottm forest"],
    "Wolves": ["wolves", "wolverhampton"],
    "Newcastle": ["newcastle"],
    "Leeds": ["leeds"],
    "Brighton": ["brighton"],
}


# Managers share first names with players ("Enzo" Maresca, "Enzo" Fernandez), so
# their names are removed from a story before looking for players. Edit when managers change.
MANAGERS = [
    "enzo maresca",
    "mikel arteta",
    "arne slot",
    "pep guardiola",
    "ruben amorim",
    "thomas frank",
    "unai emery",
    "eddie howe",
    "oliver glasner",
    "marco silva",
    "sean dyche",
    "andoni iraola",
    "daniel farke",
    "scott parker",
    "liam rosenior",
    "michael carrick",
    "fabian hurzeler",
    "nuno espirito santo",
    "graham potter",
    "regis le bris",
    "david moyes",
    "kieran mckenna",
    "roy hodgson",
]


# Places that contain a player's surname ("Old Trafford", "St James' Park").
PLACES = ["old trafford", "st james park", "king power", "villa park", "turf moor", "selhurst park"]


def _fold_accents(text: str) -> str:
    """Strip accents but keep the capitals ("Dedić" -> "Dedic")."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _capitalised(word: str, folded_text: str) -> bool:
    """Whether `word` appears in the text written like a name: "Saka" or "SAKA"."""
    return bool(
        re.search(
            rf"(?<![A-Za-z])(?:{re.escape(word.capitalize())}|{re.escape(word.upper())})(?![A-Za-z])",
            folded_text,
        )
    )


def tokens(text: str) -> list[str]:
    """Lowercase, accent-free words: "Trent Alexander-Arnold's" -> trent alexander arnold s"""
    return re.findall(r"[a-z0-9]+", names.normalize(text))


def _phrase(text: str) -> str:
    return " ".join(tokens(text))


@dataclass
class Match:
    player_ids: list[int]
    team_ids: list[int]


class Matcher:
    """Built once per refresh from the stored players and teams."""

    def __init__(self, players: Sequence[PlayerRow], teams: Sequence[TeamRow]):
        self._team_phrases: dict[str, int] = {}
        for team in teams:
            variants = [team.name, TEAM_SEARCH_NAMES.get(team.name, team.name)]
            variants += TEAM_NICKNAMES.get(team.name, [])
            for variant in variants:
                self._team_phrases[_phrase(variant)] = team.id

        self._team_of = {p.id: p.team_id for p in players}
        self._strong: dict[str, int] = {}  # full name -> player id
        weak: dict[str, set[int]] = defaultdict(set)  # surname -> players who go by it
        web_name_of: dict[str, set[int]] = defaultdict(set)
        for p in players:
            first, second, web = _phrase(p.first_name), _phrase(p.second_name), _phrase(p.web_name)
            if first and second:
                for full in {f"{first} {second}", f"{first.split()[0]} {second.split()[-1]}"}:
                    self._strong.setdefault(full, p.id)
            if (
                len(web.split()) > 1
            ):  # "Pedro Porro", "João Pedro": two words are as good as a full name
                self._strong.setdefault(web, p.id)
            for key in {
                web,
                second,
                second.split()[-1] if second else "",
                web.split()[-1] if web else "",
            }:
                if len(key) >= MIN_SURNAME_LENGTH:
                    weak[key].add(p.id)
            if len(web) >= MIN_SURNAME_LENGTH:
                web_name_of[web].add(p.id)
        self._weak = weak
        self._web_name_of = web_name_of
        # Longest names first. A name found is removed from the text before shorter ones
        # are looked for, so "James Maddison" is James Maddison and not also anyone
        # called James, and "Pedro Porro" is not also "Pedro".
        self._order = sorted(set(self._strong) | set(weak), key=lambda k: (-len(k.split()), k))

    def find(self, text: str, feed_team_id: int | None = None) -> Match:
        folded = _fold_accents(text)
        padded = f" {_phrase(text)} "
        for phrase in [*MANAGERS, *PLACES]:
            padded = padded.replace(f" {phrase} ", " ")

        teams = {tid for phrase, tid in self._team_phrases.items() if f" {phrase} " in padded}
        context = set(teams)
        if feed_team_id is not None:
            context.add(feed_team_id)

        players: set[int] = set()
        for key in self._order:
            needle = f" {key} "
            if needle not in padded:
                continue
            if key in self._strong:
                players.add(self._strong[key])
            else:
                if " " not in key and not _capitalised(key, folded):
                    continue  # "problems mount", "lucky": an ordinary word, not a name
                found = self._resolve(key, self._weak[key], context)
                players |= found
            padded = padded.replace(needle, " ")

        team_ids = set(teams)
        if feed_team_id is not None:
            team_ids.add(feed_team_id)
        return Match(sorted(players), sorted(team_ids))

    def _resolve(self, key: str, candidates: set[int], context: set[int]) -> set[int]:
        """Which player(s) a surname found in a story refers to."""
        in_context = {pid for pid in candidates if self._team_of[pid] in context}
        if in_context:
            return in_context
        if key in COMMON_WORDS:
            return set()
        if len(candidates) == 1:
            return candidates
        # Several players share the name; if exactly one is *known as* it (his FPL
        # web name is the surname), he's the likely one.
        known_as = self._web_name_of.get(key, set()) & candidates
        return known_as if len(known_as) == 1 and len(candidates) <= 2 else set()
