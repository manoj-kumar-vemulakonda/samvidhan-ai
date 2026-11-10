"""Tests for finding Article references and checking citations (no internet needed)."""

from app.citations import (
    check_citations,
    find_article_refs,
    find_citations,
    find_schedule_refs,
    label_for_id,
    mentioned_schedules,
    schedule_id,
)

KNOWN = {"14", "19", "21", "21A", "22", "32", "226", "243ZH", "370"}


def test_finds_simple_reference():
    assert find_article_refs("What does Article 21 say?", KNOWN) == ["21"]


def test_finds_lettered_and_lowercase_reference():
    assert find_article_refs("explain art. 21a please", KNOWN) == ["21A"]
    assert find_article_refs("Article 243ZH", KNOWN) == ["243ZH"]


def test_finds_clause_reference():
    assert find_article_refs("Is Art 19(1)(a) absolute?", KNOWN) == ["19"]


def test_finds_lists_and_hindi():
    assert find_article_refs("Compare Articles 14, 19 and 21", KNOWN) == ["14", "19", "21"]
    assert find_article_refs("अनुच्छेद 370 क्या है", KNOWN) == ["370"]


def test_ignores_unknown_numbers_and_plain_words():
    assert find_article_refs("the art of 2023 and article 999", KNOWN) == []
    assert find_article_refs("Can police arrest me?", KNOWN) == []


def test_find_citations_in_answer():
    answer = (
        "No. You must be produced within 24 hours [Article 22(2)]. "
        "This protects liberty [Article 21] and you can file a writ [Article 32] "
        "or [Article 226]. See also the [Preamble]. [sic]"
    )
    assert find_citations(answer) == ["22", "21", "32", "226", "Preamble"]


def test_check_citations_flags_articles_not_in_sources():
    answer = "Within 24 hours [Article 22(2)], liberty [Article 21], writ [Article 32]."
    result = check_citations(answer, ["21", "22"])
    assert result.verified == ["22", "21"]
    assert result.unverified == ["32"]
    assert result.all_verified is False


def test_check_citations_all_verified():
    result = check_citations("Equality [Article 14].", ["14", "21"])
    assert result.all_verified
    assert result.to_dict()["cited"] == ["14"]


# ---- the Schedules -------------------------------------------------------------
def test_schedule_ids_from_citations():
    assert schedule_id("Seventh Schedule, List II, Entry 2") == "S7-II-2"
    assert schedule_id("List I, Entry 97") == "S7-I-97"           # the Seventh Schedule is assumed
    assert schedule_id("Entry 17A of the Concurrent List") == "S7-III-17A"
    assert schedule_id("Tenth Schedule, Paragraph 2") == "S10-P2"
    assert schedule_id("Third Schedule, Form III") == "S3-III"
    assert schedule_id("Second Schedule, Part D") == "S2-D"
    assert schedule_id("Eighth Schedule") == "S8"
    assert schedule_id("Scheduled Castes") is None


def test_labels_for_ids():
    assert label_for_id("S7-II-2") == "Seventh Schedule, List II, Entry 2"
    assert label_for_id("S10-P7") == "Tenth Schedule, Paragraph 7"
    assert label_for_id("S8") == "Eighth Schedule"
    assert label_for_id("21A") == "Article 21A"


def test_schedule_references_in_questions():
    known = {"S7-I-97", "S7-II-5", "S10-P2", "S8"}
    assert find_schedule_refs("What is entry 97 of List I?", known) == ["S7-I-97"]
    assert find_schedule_refs("list II, entry 5 and the Eighth Schedule", known) == ["S7-II-5", "S8"]
    assert find_schedule_refs("Explain para 2 of the tenth schedule", known) == ["S10-P2"]
    assert find_schedule_refs("Can police arrest me?", known) == []
    assert mentioned_schedules("Is police in the seventh schedule?") == [7]


def test_schedule_citations_in_answer():
    answer = "Police is a State subject [Seventh Schedule, List II, Entry 2] under [Article 246]."
    result = check_citations(answer, ["246"])
    assert result.cited == ["S7-II-2", "246"]
    assert result.unverified == ["S7-II-2"]
    assert result.labels["S7-II-2"] == "Seventh Schedule, List II, Entry 2"
