"""How well the news pipeline does on real headlines labeled by hand.

news_eval.json (133 stories) was used while tuning the rules, so its scores are
optimistic. news_eval_heldout.json (60 stories) was labeled before the rules were
scored on it and is the honest number. Run `uv run python tests/news_eval.py
[file]` to see each mistake. The floors below sit just under today's scores, so a
change to the rules that makes either set worse fails here.
"""

import pytest

from tests.news_eval import run, scores


@pytest.mark.parametrize(
    ("labels", "floor"),
    [
        (
            "news_eval.json",
            {
                "story_precision": 0.95,
                "story_recall": 0.95,
                "player_precision": 0.95,
                "player_recall": 0.95,
            },
        ),
        (
            "news_eval_heldout.json",
            {
                "story_precision": 0.85,
                "story_recall": 0.9,
                "player_precision": 0.95,
                "player_recall": 0.95,
            },
        ),
    ],
)
def test_scores_stay_above_the_floor(labels, floor):
    result = scores(run(labels))
    for name, minimum in floor.items():
        assert result[name] >= minimum, f"{labels}: {name} {result[name]:.2f} < {minimum}"


def test_match_reports_and_stream_spam_are_dropped():
    results = {r.label["title"]: r for r in run()}
    assert not results["Arsenal beat Everton to go level on points with leaders Man City"].kept
    assert not results["Sunderland 0-0 Leeds: Premier League – as it happened"].kept


def test_a_headline_naming_several_players_finds_them_all():
    results = {r.label["title"]: r for r in run()}
    result = results[
        "Szoboszlai, Munoz, Barcola, Frimpong - Liverpool injury news and return dates ahead of Brighton"
    ]
    assert result.players == {368, 377, 627, 357}
    assert "injury" in result.topics
