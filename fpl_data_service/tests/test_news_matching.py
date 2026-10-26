"""Finding players and clubs in a story."""

from types import SimpleNamespace

import pytest

from fpl_data.news.matching import Matcher

ARS, MCI, CHE, MUN, NEW, LEE = 1, 2, 3, 4, 5, 6


def player(pid, web, first, second, team):
    return SimpleNamespace(id=pid, web_name=web, first_name=first, second_name=second, team_id=team)


PLAYERS = [
    player(1, "Saka", "Bukayo", "Saka", ARS),
    player(2, "Haaland", "Erling", "Haaland", MCI),
    player(3, "Maddison", "James", "Maddison", 7),
    player(4, "James", "Reece", "James", CHE),
    player(5, "Bradley", "Conor", "Bradley", 8),
    player(6, "Barcola", "Bradley", "Barcola", 8),
    player(7, "B.Silva", "Bernardo", "Silva", MCI),
    player(8, "Silva", "Thiago", "Silva", CHE),
    player(9, "Mount", "Mason", "Mount", MUN),
    player(10, "Trafford", "James", "Trafford", LEE),
    player(11, "Pedro Porro", "Pedro", "Porro", 7),
    player(12, "João Pedro", "João Pedro", "Junqueira de Jesus", CHE),
    player(13, "Lucky", "Some", "Lucky", NEW),
    player(14, "Dedić", "Dedic", "Dedić", NEW),
    player(15, "Gomez", "Joe", "Gomez", 7),
    player(16, "Gomez", "Tomas", "Gomez", CHE),
]
TEAMS = [
    SimpleNamespace(id=ARS, name="Arsenal", short_name="ARS"),
    SimpleNamespace(id=MCI, name="Man City", short_name="MCI"),
    SimpleNamespace(id=CHE, name="Chelsea", short_name="CHE"),
    SimpleNamespace(id=MUN, name="Man Utd", short_name="MUN"),
    SimpleNamespace(id=NEW, name="Newcastle", short_name="NEW"),
]


@pytest.fixture(scope="module")
def matcher():
    return Matcher(PLAYERS, TEAMS)


def ids(matcher, text, team=None):
    return matcher.find(text, team).player_ids


def test_a_distinctive_surname_matches_with_its_possessive_and_accents(matcher):
    assert ids(matcher, "Saka's injury") == [1]
    assert ids(matcher, "DEDIC out") == [14]


def test_the_club_is_found_by_any_of_its_names(matcher):
    assert matcher.find("Manchester City and Man City").team_ids == [MCI]
    assert matcher.find("Arsenal").team_ids == [ARS]


def test_a_feed_for_a_team_attaches_it(matcher):
    assert matcher.find("Injury latest", feed_team_id=ARS).team_ids == [ARS]


def test_a_full_name_is_not_also_anyone_with_that_first_name(matcher):
    # "James" alone is Reece James or Daniel James; here it's James Maddison's first name.
    assert ids(matcher, "James Maddison limps off") == [3]
    assert ids(matcher, "Bradley Barcola doubt") == [6]


def test_a_shared_surname_needs_team_context(matcher):
    assert ids(matcher, "Gomez injured") == []  # which Gomez?
    assert ids(matcher, "Gomez injured", team=CHE) == [16]
    assert ids(matcher, "Gomez injured at Chelsea") == [16]
    # Only one Silva is *known as* "Silva" (the other is "B.Silva"), so he is the likely one;
    # with the other's club in view, it's the other.
    assert ids(matcher, "Silva injured") == [8]
    assert ids(matcher, "Silva injured", team=MCI) == [7]


def test_a_two_word_web_name_is_not_also_its_second_word(matcher):
    assert ids(matcher, "Pedro Porro fit again") == [11]


def test_an_everyday_word_must_be_capitalised(matcher):
    assert ids(matcher, "Lucky escape") == [13]
    assert ids(matcher, "he was lucky to survive") == []
    assert ids(matcher, "problems continue to mount", team=MUN) == []
    assert ids(matcher, "Mount is back in training", team=MUN) == [9]


def test_a_common_word_surname_needs_context_even_when_capitalised(matcher):
    assert ids(matcher, "Mount is back in training") == []


def test_places_and_managers_are_not_players(matcher):
    assert ids(matcher, "Trouble at Old Trafford") == []
    assert ids(matcher, "Trafford keeps clean sheet") == [10]
    assert ids(matcher, "Enzo Maresca said Haaland needs a rest") == [2]


def test_nothing_found_in_unrelated_text(matcher):
    result = matcher.find("Weather warning for the weekend")
    assert (result.player_ids, result.team_ids) == ([], [])
