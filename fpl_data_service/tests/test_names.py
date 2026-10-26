import pytest

from fpl_data.names import normalize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ødegaard", "odegaard"),
        ("Groß", "gross"),
        ("dos Santos Magalhães", "dos santos magalhaes"),
        ("F.Kadıoğlu", "f.kadioglu"),
        ("Petrović", "petrovic"),
        ("Hjertø-Dahl", "hjerto-dahl"),
        ("Saka", "saka"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected
