"""Does the search find the right Articles for typical questions?"""

import pytest


def top_ids(engine, question, k=6):
    return [hit.article.id for hit in engine.search(question, k=k)]


@pytest.mark.parametrize(
    "question, expected, within",
    [
        ("Can police arrest me without a warrant?", "22", 3),
        ("right to education", "21A", 1),
        ("Article 370", "370", 1),
        ("What is the minimum age to become President?", "58", 3),
        ("Is property a fundamental right?", "300A", 6),
        ("Is property a fundamental right?", "31", 6),
        ("Do I have a right to privacy?", "21", 3),
        ("How is the Constitution amended?", "368", 1),
        ("What are my fundamental duties?", "51A", 1),
        ("reservation for women in Lok Sabha", "330A", 6),
        ("habeas corpus writ high court", "226", 3),
        # The Schedules
        ("Can my state make laws about police?", "S7-II-2", 3),
        ("Can my state make its own laws on police?", "S7-II-2", 3),
        ("Can my state pass a law on liquor?", "S7-II-8", 3),
        ("Who can make laws on forests?", "S7-III-17A", 3),
        ("Can Parliament legislate on education?", "S7-III-25", 3),
        ("What is the anti-defection law?", "S10-P2", 1),
        ("How many languages are recognised by the Constitution?", "S8", 1),
        ("What are the powers of panchayats?", "S11", 3),
        ("How many Rajya Sabha seats does Uttar Pradesh have?", "S4", 3),
        ("What oath does a Union minister take?", "S3-I", 1),
        ("How are Supreme Court judges appointed?", "124A", 6),
    ],
)
def test_finds_expected_article(engine, question, expected, within):
    assert expected in top_ids(engine, question, k=6)[:within]


def test_exact_references_come_first(engine):
    assert top_ids(engine, "Compare Articles 14, 19 and 21")[:3] == ["14", "19", "21"]
    hits = engine.search("What does art. 21a say?")
    assert hits[0].article.id == "21A" and hits[0].reason == "exact reference"


def test_exact_schedule_references(engine):
    assert top_ids(engine, "What is Entry 97 of List I?")[0] == "S7-I-97"
    assert top_ids(engine, "Explain paragraph 2 of the Tenth Schedule")[0] == "S10-P2"
    assert top_ids(engine, "what is in the eighth schedule")[0] == "S8"


def test_empty_query(engine):
    assert engine.search("   ") == []


def test_hit_has_display_fields(engine):
    data = engine.search("freedom of speech")[0].to_dict()
    for key in ("id", "label", "title", "part", "status", "score", "reason", "snippet"):
        assert key in data
