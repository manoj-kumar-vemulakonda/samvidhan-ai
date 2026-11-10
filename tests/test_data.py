"""Checks that data/constitution.json was built correctly from the official PDF."""

from app.constitution import load_constitution

c = load_constitution()


def test_edition_information():
    assert c.source["as_on"], "the edition date should be read from the PDF"
    assert "One Hundred and Sixth" in c.source["latest_amendment"]


def test_count_is_sensible():
    # 395 numbered Articles + inserted ones (21A, 243ZH, ...) + omitted ones + the Preamble
    assert 480 <= len(c.articles) <= 520


def test_preamble():
    preamble = c.get("preamble")
    assert preamble is not None
    assert preamble.text.startswith("WE, THE PEOPLE OF INDIA")
    assert "SOVEREIGN SOCIALIST SECULAR DEMOCRATIC REPUBLIC" in preamble.text


def test_well_known_articles():
    assert c.get("21").title == "Protection of life and personal liberty"
    assert c.get("21").text.startswith("No person shall be deprived of his life or personal liberty")
    assert c.get("21a").title == "Right to education"
    assert "twenty-four hours" in c.get("22").text
    assert "thirty-five years" in c.get("58").text
    assert c.get("300A").title == "Persons not to be deprived of property save by authority of law"
    assert c.get("368").title.startswith("Power of Parliament to amend the Constitution")


def test_newest_amendments_are_included():
    # Women's reservation - Constitution (One Hundred and Sixth Amendment) Act, 2023
    for article_id in ("330A", "332A", "334A"):
        assert c.get(article_id) is not None, article_id
    assert c.get("371I").title == "Special provision with respect to the State of Goa"


def test_omitted_articles_are_marked():
    assert c.get("31").status == "omitted"
    assert "Forty-fourth Amendment" in c.get("31").text
    assert c.get("21").status == "in_force"


def test_parts():
    ids = [p["id"] for p in c.parts]
    for part in ("III", "IV", "IVA", "V", "XX", "XXII"):
        assert part in ids
    assert c.get("14").part == "III" and c.get("14").part_title == "Fundamental Rights"
    assert c.get("51A").part == "IVA"


def test_no_empty_or_footnote_text():
    for article in c.provisions:  # Articles and Schedule pieces
        assert article.title, article.id
        assert article.text.strip(), article.id
        assert "" not in article.text


# ---- the Schedules -------------------------------------------------------------
def test_all_twelve_schedules_are_there():
    numbers = {int(a.id[1:].split("-")[0]) for a in c.schedules}
    assert numbers == set(range(1, 13))


def test_seventh_schedule_lists():
    # List I has 97 entries + 2A, 92A, 92B, 92C; List II 66; List III 47 + 11A, 17A, 17B, 20A, 33A
    assert len(c.schedule_pieces(7)) == 101 + 66 + 52
    police = c.get("S7-II-2")
    assert police.label == "Seventh Schedule, List II, Entry 2"
    assert police.text.startswith("Police (including railway and village police)")
    assert police.refs == "Article 246" and police.chapter == "List II—State List"
    assert c.get("S7-III-17A").text == "Forests."
    assert c.get("S7-I-33").status == "omitted"


def test_other_schedules():
    languages = c.get("S8").text
    assert "22. Urdu." in languages and "6. Hindi." in languages
    assert c.get("S10-P2").title == "Disqualification on ground of defection"
    assert c.get("S10-P3").status == "omitted"  # printed only as "* * *"
    assert c.get("S3-I").title == "Oath of office for a Minister for the Union"
    assert "swear in the name of God/solemnly affirm" in c.get("S3-I").text
    assert "Madhya Pradesh - 11" in c.get("S4").text  # first line of a page (just below the header)
    assert c.get("S6-P1").title == "Autonomous districts and autonomous regions"
    assert "11. Drinking water." in c.get("S11").text


def test_court_ruling_notes():
    # The official text keeps the struck-down 99th Amendment (NJAC) but footnotes it
    for article_id in ("124A", "124B", "124C"):
        notes = " ".join(c.get(article_id).notes)
        assert "struck down by the Supreme Court" in notes and "Advocates-on-Record" in notes
    assert "Minerva Mills" in " ".join(c.get("368").notes)
    assert "Kihoto Hollohon" in " ".join(c.get("S10-P7").notes)
    assert c.get("21").notes == ()


def test_table_of_contents_has_schedules():
    groups = {g["id"]: g for g in c.table_of_contents()}
    assert groups["S7-II"]["kind"] == "schedule"
    assert groups["S7-II"]["title"] == "Seventh Schedule - List II—State List"
    assert groups["III"]["kind"] == "article"