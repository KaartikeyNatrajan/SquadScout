"""Player and team news from outside the FPL API (RSS feeds).

FPL's own `news` field says little and is slow: it won't tell you that the
manager said at a press conference that a player needs a rest. So we also read
news headlines. The design rule is "code retrieves, the model interprets":

- Code finds stories that mention a player or team, tags their *topic*
  (injury, fitness, suspension, rest, team news, press conference), removes
  duplicates, and keeps the wording and source.
- Code never decides what a story *means* for a player. "Haaland doubt, Semenyo
  injured" names two players and one injury; a keyword can't tell which is
  which. So no `status: injured` is ever produced. The language model reads the
  short, dated, sourced snippet and decides.

    feeds.py      which feeds to read
    client.py     downloading them
    rss.py        parsing RSS into raw items
    text.py       cleaning text, building the snippet the model sees
    topics.py     tagging a story's topic
    matching.py   finding which players and teams a story mentions
    pipeline.py   raw items -> stories ready to store
    selection.py  choosing which stories to show (recent, no near-duplicates)
    refresh.py    the hourly job
"""
