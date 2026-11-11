"""
scripts/officialise_acts.py swaps an Act's open-dataset text for the official India Code text only when the
PDF was read well. Its checks are tested here (no downloads).
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("officialise_acts", PROJECT / "scripts" / "officialise_acts.py")
officialise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(officialise)

TEXT = "Whoever does the thing described in this section shall be punished with imprisonment for a term. " * 2


def section(number, title, text=TEXT, **extra):
    return {"number": number, "title": title, "text": text, **extra}


OLD = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [section("41A.", "Notice of appearance")]


def test_a_good_reading_passes():
    new = [section(str(n), f"Title of section {n}.") for n in range(1, 41)] + [section("41A", "Notice of appearance")]
    result = officialise.compare(OLD, new)
    assert result["problems"] == [] and result["coverage"] == 1      # '41A.' in the dataset is s. 41A


def test_the_table_of_contents_alone_is_refused():
    # every number and title, but no text: what the reader gets from the PDF's list of sections
    new = [section(str(n), f"Title of section {n}", text="") for n in range(1, 41)] + [section("41A", "Notice of appearance", "")]
    result = officialise.compare(OLD, new)
    assert any("no text" in p for p in result["problems"])


def test_cut_texts_and_missing_sections_are_refused():
    short = [section(str(n), f"Title of section {n}", text=TEXT[:60]) for n in range(1, 41)]
    assert any("too short" in p for p in officialise.compare(OLD, short)["problems"])
    half = [section(str(n), f"Title of section {n}") for n in range(1, 21)]
    assert any("found" in p for p in officialise.compare(OLD, half)["problems"])


def test_repealed_sections_may_be_missing_and_keep_their_old_text():
    old = OLD + [section("42", "[Repealed.]", "[Repealed.]"), section("43", "[Repealed.]", "Rep. by Act 9 of 1932.")]
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [section("41A", "Notice of appearance")]
    result = officialise.compare(old, new)
    assert result["problems"] == [] and result["missing"] == ["42", "43"]
    merged = officialise.merge(new, old, result["missing"])
    assert [s["number"] for s in merged[-3:]] == ["41A", "42", "43"] and merged[-1]["from_dataset"]


def test_only_sections_in_the_acts_own_list_count():
    lines = [SimpleNamespace(text=t) for t in (
        "ARRANGEMENT OF SECTIONS", "6. Equal rights of daughter.", "29. Failure of heirs.", "THE SCHEDULE",
        "ACT NO. 30 OF 1956", "6. Equal rights of daughter.—(1) ...", "STATE AMENDMENT Karnataka",
        "6B. Interest to devolve by survivorship.—...")]
    assert officialise.contents_numbers(lines) == {"6", "29", "SCHEDULE"}   # 6B (a State amendment) is not in it
    assert officialise.plain_title("The Hindu Succession Act, 1956") == officialise.plain_title("Hindu Succession Act,  1956")


ILLUSTRATION = "\nIllustration\nA promises to paint a picture for B by a certain day, at a certain price."


def test_lost_illustrations_are_refused():
    # India Code prints Illustrations small, like footnotes: a reading that drops them must not replace the text
    old = [section(str(n), f"Title of section {n}", TEXT + (ILLUSTRATION if n % 4 == 0 else "")) for n in range(1, 41)]
    without = [section(str(n), f"Title of section {n}") for n in range(1, 41)]
    assert any("Illustrations of 10 of 10" in p for p in officialise.compare(old, without)["problems"])
    with_them = [section(str(n), f"Title of section {n}", TEXT + (ILLUSTRATION.replace("\n", " ") if n % 4 == 0 else ""))
                 for n in range(1, 41)]
    assert officialise.compare(old, with_them)["problems"] == []
    # the words without their label (dropped as a "running head") are not enough
    unlabelled = [dict(s, text=s["text"].replace("Illustration", "")) for s in with_them]
    assert any("Illustrations of 10 of 10" in p for p in officialise.compare(old, unlabelled)["problems"])


def test_illustrations_in_another_section_or_in_a_note_are_not_lost():
    # the dataset ran s. 5 and s. 6 together (TPA ss. 114-137); and "The Illustrations omitted by ..." is a note
    old = ([section(str(n), f"Title of section {n}") for n in range(1, 5)]
           + [section("5", "Title of section 5", TEXT + "\n6. Next.—More text." + ILLUSTRATION),
              section("6", "Title of section 6", TEXT + "\n1. The Illustrations omitted by Act 20 of 1929, s. 11.")]
           + [section(str(n), f"Title of section {n}") for n in range(7, 41)])
    new = ([section(str(n), f"Title of section {n}") for n in range(1, 6)]
           + [section("6", "Title of section 6", TEXT + ILLUSTRATION)]
           + [section(str(n), f"Title of section {n}") for n in range(7, 41)])
    assert officialise.compare(old, new)["problems"] == []


def test_reading_an_official_act_again_keeps_it_official():
    old = [section(str(n), f"Title of section {n}") for n in range(1, 5)] + [section("4A", "Kept before", from_dataset=True)]
    new = [section(str(n), f"Title of section {n}") for n in (1, 2, 4)]
    merged = officialise.merge(new, old, ["3", "4A"], old_is_official=True)
    flags = {s["number"]: s.get("from_dataset", False) for s in merged}
    assert flags == {"1": False, "2": False, "3": False, "4": False, "4A": True}
