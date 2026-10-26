"""Name normalisation for searching: "Ødegaard" -> "odegaard"."""

import unicodedata

# Letters that Unicode can't split into "a base letter + an accent mark"
# (ø is its own letter, not o plus a mark), so they are replaced by hand.
_SPECIAL = str.maketrans({"ø": "o", "Ø": "o", "ß": "ss", "đ": "d", "Đ": "d", "ı": "i"})


def normalize(text: str) -> str:
    """Lowercase a name and strip its accents, so searches ignore them.

    "Ødegaard" -> "odegaard", "Magalhães" -> "magalhaes", "Groß" -> "gross".
    """
    # NFD splits "é" into "e" + an accent mark; category "Mn" is the accent
    # marks ("nonspacing marks"); NFC puts back together whatever is left.
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    recomposed = unicodedata.normalize("NFC", without_marks)
    return recomposed.translate(_SPECIAL).lower()
