"""Scoring the news pipeline against the hand-labeled headlines (testdata/news_eval.json).

Each labeled story says whether a person would want it kept (it is about a player's
or club's availability, fitness, discipline, rest or selection) and which players it
is genuinely about. The snapshot of players is the squad list the stories were
collected against (testdata/news_eval_players.json).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fpl_data.news import pipeline
from fpl_data.news.matching import Matcher
from fpl_data.news.models import Feed, RawItem

TESTDATA = Path(__file__).parent / "testdata"


def load_matcher() -> Matcher:
    snapshot = json.loads((TESTDATA / "news_eval_players.json").read_text(encoding="utf-8"))
    players = [SimpleNamespace(**p) for p in snapshot["players"]]
    teams = [SimpleNamespace(**t) for t in snapshot["teams"]]
    return Matcher(players, teams)


def load_labels(name: str = "news_eval.json") -> list[dict]:
    return json.loads((TESTDATA / name).read_text(encoding="utf-8"))


@dataclass
class Result:
    label: dict
    kept: bool
    players: set[int]
    topics: list[str]
    snippet: str


def run(labels_file: str = "news_eval.json", matcher: Matcher | None = None) -> list[Result]:
    matcher = matcher or load_matcher()
    results = []
    for label in load_labels(labels_file):
        feed = Feed(label["source"], "", team_id=label["feed_team_id"])
        raw = RawItem(
            title=label["title"],
            link="https://example.com",
            published=datetime(2026, 10, 25, tzinfo=UTC),
            description=label["description"],
            source=label["source"],
            feed=feed,
        )
        stories = pipeline.process([raw], matcher)
        story = stories[0] if stories else None
        results.append(
            Result(
                label,
                story is not None,
                set(story.player_ids) if story else set(),
                story.topics if story else [],
                story.snippet if story else "",
            )
        )
    return results


def scores(results: list[Result]) -> dict[str, float]:
    tp = sum(r.kept and r.label["keep"] for r in results)
    fp = sum(r.kept and not r.label["keep"] for r in results)
    fn = sum(not r.kept and r.label["keep"] for r in results)
    # Players: judged on the stories that are wanted and were kept.
    p_tp = p_fp = p_fn = 0
    for r in results:
        if not (r.kept and r.label["keep"]):
            continue
        want = set(r.label["players"])
        p_tp += len(want & r.players)
        p_fp += len(r.players - want)
        p_fn += len(want - r.players)
    return {
        "story_precision": tp / (tp + fp) if tp + fp else 1.0,
        "story_recall": tp / (tp + fn) if tp + fn else 1.0,
        "player_precision": p_tp / (p_tp + p_fp) if p_tp + p_fp else 1.0,
        "player_recall": p_tp / (p_tp + p_fn) if p_tp + p_fn else 1.0,
    }


if __name__ == "__main__":
    names = {
        p["id"]: p["web_name"]
        for p in json.loads((TESTDATA / "news_eval_players.json").read_text())["players"]
    }
    import sys

    results = run(sys.argv[1] if len(sys.argv) > 1 else "news_eval.json")
    for key, value in scores(results).items():
        print(f"{key}: {value:.2f}")
    print()
    for r in results:
        want = set(r.label["players"])
        problems = []
        if r.kept and not r.label["keep"]:
            problems.append("WRONGLY KEPT")
        if not r.kept and r.label["keep"]:
            problems.append("MISSED")
        if r.kept and r.label["keep"] and r.players != want:
            problems.append(
                f"players got {sorted(names[i] for i in r.players)} want {sorted(names[i] for i in want)}"
            )
        if problems:
            print(r.label["id"], r.label["title"][:90], "->", "; ".join(problems), r.topics)
