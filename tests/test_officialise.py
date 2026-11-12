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


def test_the_list_of_sections_in_its_untidy_forms():
    # India Code's lists run the title into the number, space the dot, write I and O after a hyphen, drop the dot
    lines = [SimpleNamespace(text=t) for t in (
        "22E.Award of Permanent Lok Adalat to be final.", "125A . Penalty for filing false affidavit, etc.",
        "45-IA. Requirement of registration.", "49-I Conditions for scheduled specimens.", "1[53S. Right to legal representation.",
        "ACT NO. 39 OF 1987", "23. Something in the body.—(1) ...")]
    assert officialise.contents_numbers(lines) == {"22E", "125A", "45IA", "49I", "53S"}


def test_a_hyphen_before_i_or_o_is_the_same_section():
    # the dataset writes SEBI s. 15-I, the reader 15I (as the library does): one section, not one lost and one new
    old = OLD + [section("15-I", "Power to adjudicate"), section("15-O", "Appeal")]
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [
        section("41A", "Notice of appearance"), section("15I", "Power to adjudicate"), section("15O", "Appeal")]
    result = officialise.compare(old, new)
    assert result["coverage"] == 1 and result["extra"] == [] and result["missing"] == []


def test_a_section_whose_heading_was_not_read_is_cut_off_the_one_before():
    # Benami Act: "60 Application of other laws not barred.—" has no dot, so s. 60 stayed at the end of s. 59;
    # Companies Act: India Code prints "37ZA." for s. 378ZA; a mere mention of the title is not a heading
    old = OLD + [section("41B", "Application of other laws not barred"), section("41C", "Annual general meetings"),
                 section("41D", "Consultative Committee")]
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [section(
        "41A", "Notice of appearance", TEXT + "functions. 60 Application of other laws not barred.—The provisions of "
        "this Act shall be in addition to any other law. 37ZA. Annual general meetings.—(1) Every Producer Company "
        "shall hold a meeting. The Consultative Committee constituted under section 41D shall be consulted.")]
    missing = officialise.compare(OLD + old[-3:], new)["missing"]
    assert officialise.split_glued(new, old, missing) == ["41B", "41C"]
    by_number = {s["number"]: s for s in new}
    assert by_number["41A"]["text"].endswith("functions.")
    assert by_number["41B"]["text"] == "The provisions of this Act shall be in addition to any other law."
    assert by_number["41C"]["title"] == "Annual general meetings" and by_number["41C"]["text"].startswith("(1) Every")
    assert "41D" not in by_number and "Consultative Committee constituted" in by_number["41C"]["text"]


def test_a_missed_section_found_inside_another_is_refused():
    # Water Act: India Code prints s. 3 as "2. Constitution of Central Boards"; its heading is lost, its law ends
    # up in s. 2 - keeping the dataset's s. 3 as well would give the law twice
    law = ("The Central Government shall, with effect from such date being a date not later than six months of "
           "the commencement of this Act in the States, constitute a Central Board.")
    old = OLD + [section("42", "Constitution of Central Boards", "42. Constitution of Central Boards.—(1) " + law)]
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [
        section("41A", "Notice of appearance", TEXT + " (1) " + law)]
    assert any("inside another section" in p for p in officialise.compare(old, new)["problems"])
    new[-1]["text"] = TEXT
    assert not any("inside" in p for p in officialise.compare(old, new)["problems"])   # truly missing: kept


def test_a_section_that_swells_is_refused():
    # Stamp Act s. 79 "[Repealed.]" took in 200,000 characters of Schedules printed "SCHEDULE 1"
    old = OLD[:-1] + [section("41A", "[Repealed.]", "[Repealed.] Rep. by Act 10 of 1914.")]
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [
        section("41A", "[Repealed.]", "[Repealed.] Rep. by Act 10 of 1914. SCHEDULE 1 STAMP-DUTY " + "Two annas. " * 2000)]
    assert any("much longer" in p for p in officialise.compare(old, new)["problems"])


def test_a_range_substituted_by_other_sections_is_gone():
    # EPA s. 17 in the dataset: no longer a section, so the PDF may lack it (still kept, with its note)
    old = OLD + [section("42", "Offences by Government Departments", "42. Offences by Government Departments. [Substituted "
                         "by Act 18 of 2023, s. 2 and Schedule, for sections 15 to 17 (w.e.f. 1-4-2024). See section 15B.]")]
    assert officialise.is_gone(old[-1]) and not officialise.is_gone(OLD[0])
    new = [section(str(n), f"Title of section {n}") for n in range(1, 41)] + [section("41A", "Notice of appearance")]
    result = officialise.compare(old, new)
    assert result["problems"] == [] and result["coverage"] == 1 and result["missing"] == ["42"]


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
