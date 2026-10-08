"""Small lexical recall for bounded memory; no network, vectors or duplicate index."""

import re

_WORDS = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]+")
_STOP = frozenset(
    "a an the is are was were do does did of to in on for and or my me i we "
    "what how why when where please".split()
)


def query_terms(query: str) -> frozenset[str]:
    """Keep Latin words and overlapping Chinese pairs instead of whole questions.

    A Chinese question normally has no whitespace. Whole-query matching loses
    an otherwise exact remembered topic; pairs keep this local lexical lookup
    useful without assuming a semantic model or enlarging collection scope.
    """
    terms: set[str] = set()
    for word in _WORDS.findall(query.casefold()):
        if word in _STOP:
            continue
        if _CJK.fullmatch(word) and len(word) > 1:
            terms.update(word[index : index + 2] for index in range(len(word) - 1))
        else:
            terms.add(word)
    return frozenset(terms)


def overlap_score(content: str, terms: frozenset[str]) -> int:
    return len(query_terms(content).intersection(terms))
