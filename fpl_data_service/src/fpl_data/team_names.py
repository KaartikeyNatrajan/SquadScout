"""The names people use for each club, besides FPL's own.

FPL calls clubs "Man City", "Spurs", "Nott'm Forest"; newspapers and people say
"Manchester City", "Tottenham", "Nottingham Forest". Used to find a club named in
a request (handlers) or a news story (news/), and to search Google News.
"""

# FPL's name -> the full name newspapers use. Clubs not listed are called the same.
FULL_NAMES = {
    "Man City": "Manchester City",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham Hotspur",
    "Nott'm Forest": "Nottingham Forest",
    "Leeds": "Leeds United",
    "Newcastle": "Newcastle United",
    "Wolves": "Wolverhampton Wanderers",
}

# Other common names (lowercase).
_NICKNAMES = {
    "Man City": ["man city"],
    "Man Utd": ["man utd", "man united"],
    "Spurs": ["spurs", "tottenham"],
    "Nott'm Forest": ["nott'm forest", "nottm forest"],
    "Wolves": ["wolves", "wolverhampton"],
    "Newcastle": ["newcastle"],
    "Leeds": ["leeds"],
    "Brighton": ["brighton"],
}


def full_name(fpl_name: str) -> str:
    """The name newspapers use: "Man City" -> "Manchester City"."""
    return FULL_NAMES.get(fpl_name, fpl_name)


def all_names(fpl_name: str) -> list[str]:
    """Every name a club goes by, FPL's own first."""
    return [fpl_name, full_name(fpl_name), *_NICKNAMES.get(fpl_name, [])]
