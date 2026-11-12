"""Court rules and official schedules: parsing PDFs, citing rules, the bail table, adding your own rule book."""

import time

import pymupdf
import pytest

from app import rulebook
from app.laws import LEGAL_HINTS
from app.rulebook import Line


# ---------------------------------------------------------------------------
# The parser, on made-up text
# ---------------------------------------------------------------------------
@pytest.fixture
def client(workspace, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    with TestClient(app) as test_client:
        yield test_client


def lines(texts, bold=False, page=1):
    return [Line(t, page, 11.0, bold, 72.0, 100.0 + i) for i, t in enumerate(texts)]


def test_orders_and_rules_are_numbered_per_order():
    found = rulebook.parse(lines([
        "ORDER VII", "PLAINT",
        "1. Particulars to be contained in plaint.—The plaint shall contain", "the name of the Court.",
        "11. Rejection of plaint.—The plaint shall be rejected", "(a) where it does not disclose a cause of action;",
        "ORDER XXXIX", "TEMPORARY INJUNCTIONS",
        "1. Cases in which temporary injunction may be granted.—Where in any suit", "2. Injunction to restrain.—In any suit",
    ], bold=True), numbering="orders", bold=True)
    by_id = {p["number"]: p for p in found}
    assert set(by_id) == {"O7R1", "O7R11", "O39R1", "O39R2"}
    assert by_id["O7R11"]["ref"] == "O. VII r. 11" and by_id["O7R11"]["title"] == "Rejection of plaint"
    assert "cause of action" in by_id["O7R11"]["text"]
    assert by_id["O39R1"]["chapter_title"] == "TEMPORARY INJUNCTIONS"


def test_a_second_order_xi_is_the_commercial_one():
    found = rulebook.parse(lines([
        "ORDER XI", "Discovery", "1. Discovery by interrogatories.—In any suit",
        "*[ORDER XI", "Disclosure in commercial suits", "1. Disclosure and discovery of documents.—(1) Plaintiff shall",
    ], bold=True), numbering="orders", bold=True)
    assert [p["number"] for p in found] == ["O11R1", "O11CR1"]
    assert found[1]["ref"] == "O. XI (Commercial) r. 1"


def test_sections_skip_the_table_of_contents_and_the_list_of_amending_acts():
    found = rulebook.parse(lines([
        "2. The North Eastern Areas (Reorganisation) Act, 1971 (81 of 1971).",     # bold in some PDFs
        "18. Sanction for prosecution.",                                            # table of contents, bold by accident
        "1. Short title.—This Act may be called the Test Act.",
        "2. Definitions.—In this Act,—", "(a) “court” means a court;",
        "3. [Omitted.]", "4. Penalty.—Whoever contravenes", "35. Return of plaint.—glued footnote digit 3 + rule 5",
    ], bold=True), numbering="sections", bold=True)
    assert [p["number"] for p in found] == ["1", "2", "3", "4", "5"]
    assert found[2]["status"] == "omitted"


def test_a_stray_contents_line_does_not_hide_the_sections_after_it():
    # "21-A." from the table of contents came first, so 5, 7, 10 ... looked like going backwards
    found = rulebook.parse(lines([
        "21-A. Special provision regarding jurisdiction of certain", "courts and validation of certain Acts.",
        "5. (1) The pecuniary jurisdiction of the Chief Judge", "7. Distribution of business.—The Chief Judge may",
        "10. (1) The Government may, after consultation", "21. (1) The Government may", "21-A. (1) Notwithstanding anything",
        "22. The courts shall sit", "23. Subject to the other provisions",
    ]), numbering="sections", bold=False)
    assert [p["number"] for p in found] == ["5", "7", "10", "21", "21A", "22", "23"]


def test_a_section_inserted_then_substituted_has_two_note_numbers():
    # NGT Act s. 10A: inserted in 2017 (note 1), substituted in 2026 (note 2) - "1[2[10A." is still a heading
    found = rulebook.parse(lines([
        "10. Removal of Chairperson.—The Chairperson may be removed", "by the President.",
        "1[2[10A. Selection, qualifications, conditions of service.—Notwithstanding anything",
        "5[ 6[10B. Other conditions.—The Central Government may", "11. Acting Chairperson.—In the event",
    ]), numbering="sections", bold=False)
    assert [p["number"] for p in found] == ["10", "10A", "10B", "11"]
    assert found[1]["title"].startswith("Selection, qualifications") and "Notwithstanding" not in found[0]["text"]


def test_title_case_chapter_headings_only_when_asked():
    texts = ["CHAPTER XXVI", "Contents line", "Chapter I", "1. Title.—These rules", "2. Commencement.—They shall"]
    plain = rulebook.parse(lines(texts, bold=True), numbering="rules", bold=True)
    asked = rulebook.parse(lines(texts, bold=True), numbering="rules", bold=True, title_headings=True)
    assert plain[0]["chapter"] == "Chapter XXVI" and asked[0]["chapter"] == "Chapter I"


def test_rules_that_start_again_in_each_part_get_unique_ids():
    found = rulebook.parse(lines([
        "PART A", "GENERAL", "1. Scope.", "2. Applications.", "PART B", "COPIES", "1. Who may apply.", "2. Fees.",
    ]), numbering="rules", bold=False)
    assert [p["number"] for p in found] == ["PTAR1", "PTAR2", "PTBR1", "PTBR2"]
    assert found[2]["ref"] == "Pt. B r. 1"


def test_pages_fallback_when_there_is_no_numbering():
    texts = [Line("Practice direction about listing of urgent matters " * 3, page) for page in (1, 2, 3)]
    found, how = rulebook.parse_or_pages(texts)
    assert how == "pages" and [p["ref"] for p in found] == ["p. 1", "p. 2", "p. 3"]


def test_roman_numbers():
    assert rulebook.roman_to_int("XXXIX") == 39 and rulebook.int_to_roman(47) == "XLVII"


def test_a_wrapped_the_schedule_is_not_a_schedule_heading():
    # NI Act s. 2: "... Rep. by the Repealing and Amending Act, 1891, s. 2 and / the Schedule I." made
    # everything after it one big "Schedule"
    found = rulebook.parse(lines([
        "1. Short title.—This Act may be called the Test Act.",
        "2. [Repeal of enactments.] Rep. by the Repealing and Amending Act, 1891 (12 of 1891), s. 2 and",
        "the Schedule I.",
        "3. Interpretation-clause.—In this Act, unless there is anything repugnant",
        "THE SCHEDULE", "Enactments repealed",
    ]), numbering="sections", bold=False)
    assert [p["number"] for p in found] == ["1", "2", "3", "SCH"]


def test_schedules_headed_by_a_roman_number_are_not_glued_to_the_last_section():
    # Succession Act: "SCHEDULE I", "1[SCHEDULE II" ... ended up in s. 392 (or, read another way, in s. 5)
    found = rulebook.parse(lines([
        "390. Provisions with respect to certificates.—Notwithstanding anything in Bombay Regulation",
        "391. Saving.—Nothing in Part VIII, Part IX or Part X shall validate any disposition",
        "392. [Repeals].—Rep. by the Repealing Act, 1927 (12 of 1927), s. 2 and Sch.",
        "SCHEDULE I", "(See section 28)", "TABLE OF CONSANGUINITY",
        "1[SCHEDULE II", "PART I", "(See section 54)", "(1) Father and mother.",
        "SCHEDULE III", "5. In applying any of the following sections, namely, sections seventy-five",
    ]), numbering="sections", bold=False, numbered_schedules=True)
    by_number = {p["number"]: p for p in found}
    assert list(by_number) == ["390", "391", "392", "SCH1", "SCH2", "SCH3"]
    assert "Father and mother" in by_number["SCH2"]["text"] and "In applying" in by_number["SCH3"]["text"]
    assert "SCHEDULE" not in by_number["392"]["text"] and by_number["SCH3"]["ref"] == "Sch. III"


def test_schedules_with_a_note_number_in_front_and_the_bills_reasons():
    # RBI, UAPA, IT Act: "1[THE FIRST SCHEDULE" (substituted), "1THE SECOND SCHEDULE"; WPA: "1[SCHEDULE — I";
    # and the Bill's Statement of Objects and Reasons that India Code prints after the Act is not law
    texts = [
        "60. Power to make rules.—The Central Government may make rules.", "61. Repeal.—The Act is repealed.",
        "1[THE FIRST SCHEDULE", "[See section 9]", "1. The Western Area shall consist of the States of Goa.",
        "1THE SECOND SCHEDULE", "SCHEDULED BANKS", "Axis Bank Ltd.",
        "1[SCHEDULE — III", "Blackbuck Antilope cervicapra",
        "STATEMENT OF OBJECTS AND REASONS", "The Bill seeks to consolidate the law.", "2. Hence this Bill.",
    ]
    found = {p["number"]: p for p in rulebook.parse(lines(texts), numbering="sections", bold=False, numbered_schedules=True)}
    assert list(found) == ["60", "61", "SCH1", "SCH2", "SCH3"]
    assert found["61"]["text"].strip() == "The Act is repealed." and "Axis Bank" in found["SCH2"]["text"]
    assert "Bill" not in found["SCH3"]["text"]
    # court-rule books: the note-numbered headings only when asked, but the Bill's reasons never
    plain = {p["number"]: p for p in rulebook.parse(lines(texts), numbering="sections", bold=False)}
    assert list(plain) == ["60", "61"] and "Bill" not in plain["61"]["text"]
    assert rulebook.OBJECTS_AND_REASONS.match("STATEMENT OF OBJECTS AND REASONSES")      # IBC's misprint


def test_a_heading_that_wraps_onto_the_next_line_is_the_whole_title():
    # 647 sections (42 in the BNS) had the first line of a long heading as their title and the heading again
    # in their text; a sentence that runs into "Explanation.—" is not a heading
    texts = ["195. Assaulting public servant.—Whoever assaults.",
             "196. Promoting enmity between different groups on grounds of religion, race, place of birth,",
             "residence, language, etc., and doing acts prejudicial to maintenance of harmony.—(1) Whoever,—",
             "(a) by words promotes enmity.",
             "197. Every agreement in restraint of the marriage of any person is void",
             "unless made by a minor. Explanation.—This applies to all."]
    found = {p["number"]: p for p in rulebook.parse(lines(texts), numbering="sections", bold=False)}
    assert found["196"]["title"] == ("Promoting enmity between different groups on grounds of religion, race, place of "
                                     "birth, residence, language, etc., and doing acts prejudicial to maintenance of harmony")
    assert found["196"]["text"].startswith("(1) Whoever")
    assert found["197"]["title"].startswith("Every agreement") and found["197"]["text"].startswith("Every agreement")


def test_a_schedule_numbered_in_figures():
    # Stamp Act: "SCHEDULE 1" (not "I") stayed glued to s. 79; a State's "SCHEDULE I-A" is not a heading
    texts = ["78. Act to be translated.—Every State Government shall provide.", "79. [Repealed.] Rep. by Act 10 of 1914.",
             "SCHEDULE 1", "STAMP-DUTY ON INSTRUMENTS", "13. BILL OF EXCHANGE", "SCHEDULE I-A", "RATES IN HIMACHAL PRADESH"]
    found = {p["number"]: p for p in rulebook.parse(lines(texts), numbering="sections", bold=False, numbered_schedules=True)}
    assert list(found) == ["78", "79", "SCH1"] and found["SCH1"]["ref"] == "Sch. I" and found["SCH1"]["title"] == "Schedule I"
    assert "SCHEDULE" not in found["79"]["text"] and "SCHEDULE I-A" in found["SCH1"]["text"]


def test_a_bold_note_in_note_type_is_still_a_note():
    # IBC p. 15: the commencement note prints its dates in bold; "2. 5th August, 2016 ..." became section 2
    page = page_of(1, [(LAW, 11.0, False, 72.0), ("1. The proviso omitted by the A.O. 2020.", 9.0, False, 72.0),
                       ("2. 5th August, 2016, vide Notifn. No. S.O. 2618(E), see Gazette of India.", 10.0, True, 72.0),
                       ("19th August, 2016—S. 3, cls. (1), (5), vide notifn. No. S.O. 2746(E).", 9.0, True, 86.0)])
    assert rulebook.footnote_block(page, 11.0) == 1
    heading = page_of(1, [(LAW, 11.0, False, 72.0), ("1. Subs. by Act 3 of 1921.", 9.0, False, 72.0),
                          ("2. Application.—The provisions of this Code shall apply to", 11.0, True, 90.0)])
    assert rulebook.footnote_block(heading, 11.0) is None      # a bold heading in the usual size is law


# India Code prints Illustrations (and some whole pages) in small type, and some notes in the usual type
def page_of(page, rows):
    """rows: (text, size, bold, x) -> Lines, one below the other."""
    return [Line(text, page, size, bold, x, 100.0 + 12 * i) for i, (text, size, bold, x) in enumerate(rows)]


LAW = "The person who makes the proposal is called the promisor and the person accepting it is the promisee, "


def illustrated_page(page=1):
    return page_of(page, [
        ("2. Interpretation-clause.—In this Act the following words are used in the following senses,", 11.0, True, 90.0),
        (LAW, 11.0, False, 72.0), (LAW, 11.0, False, 72.0), (LAW, 11.0, False, 72.0),
        ("Illustrations", 11.0, False, 280.0),
        ("(a) A makes a contract with B to buy B's horse if A survives C. This contract cannot be", 9.0, False, 90.0),
        ("enforced by law unless and until C dies in A's lifetime.", 9.0, False, 72.0),
        ("3. Communication of proposals.—The communication of proposals is deemed to be made", 11.0, True, 90.0),
        (LAW, 11.0, False, 72.0),
        ("1. Subs. by Act 3 of 1951, s. 3 and Sch., for “Part A States”.", 11.0, False, 72.0),   # a note, usual size
        ("2. Ins. by s. 4, ibid.", 9.0, False, 72.0),
        ("3. The words “and Berar” omitted by", 9.0, False, 72.0),
        ("the A.O. 1950.", 9.0, False, 72.0),                        # a note's next line, back at the left edge
    ])


def test_shape_keeps_small_illustrations_and_drops_notes_of_any_size():
    kept = [line.text for line in rulebook.clean_lines(illustrated_page(), notes="shape")]
    assert "Illustrations" in kept and any(t.startswith("(a) A makes a contract") for t in kept)
    assert "enforced by law unless and until C dies in A's lifetime." in kept
    assert not any("Subs. by" in t or "Ins. by" in t or "omitted by" in t or "A.O. 1950" in t for t in kept)
    # court-rule books keep the old reading: small lines go, whatever they are
    by_size = [line.text for line in rulebook.clean_lines(illustrated_page(), notes="size")]
    assert not any(t.startswith("(a) A makes a contract") for t in by_size)


def test_a_numbered_list_in_the_law_is_not_taken_for_notes():
    schedule = page_of(1, [
        ("THE FIFTH SCHEDULE", 11.0, True, 250.0),
        ("1. The arbitrator is an employee, consultant, advisor or has any other past or present", 11.0, False, 72.0),
        ("business relationship with a party.", 11.0, False, 90.0),
        ("2. The arbitrator currently represents or advises one of the parties.", 11.0, False, 72.0),
    ])
    assert rulebook.footnote_block(schedule, 11.0) is None
    notes = page_of(1, [(LAW, 11.0, False, 72.0), ("1. Ins. by Act 33 of 2019, s. 5 (w.e.f. 30-8-2019).", 9.0, False, 72.0),
                        ("2. But see s. 135, infra.", 9.0, False, 72.0)])
    assert rulebook.footnote_block(notes, 11.0) == 1


def test_a_short_note_repeated_on_many_pages_does_not_hide_its_block():
    # "2. Ins. by s. 27, ibid." is on most pages, so it looks like a running head; without it the
    # numbering 1, 3 had a gap and the notes in the usual size stayed in the law
    pages = []
    for page in range(1, 8):
        pages += page_of(page, [
            (f"{page}. Short title.—This Act may be called the Test Act, {page}.", 11.0, True, 90.0),
            (LAW, 11.0, False, 72.0), (LAW, 11.0, False, 72.0),
            (f"1. Subs. by Act 20 of 1929, s. {page + 20}, for “at the same rate of interest {page}”.", 11.0, False, 72.0),
            ("2. Ins. by s. 27, ibid.", 9.0, False, 72.0),
            (f"3. The words “for a term of years {page}” omitted by s. 28, ibid.", 11.0, False, 72.0),
        ])
    kept = [line.text for line in rulebook.clean_lines(pages, notes="shape")]
    assert not any("Subs. by" in t or "omitted by" in t for t in kept)
    assert sum(t == LAW for t in kept) == 14


def test_a_page_printed_in_small_type_is_kept():
    # CPC ss. 52-60 on India Code: the law in 9 pt on a few pages, the notes in the same size
    normal = page_of(1, [("51. Powers of Court to enforce execution.—Subject to such conditions", 11.0, True, 90.0)]
                     + [(LAW, 11.0, False, 72.0)] * 30)
    small = page_of(2, [("52. Enforcement of decree against legal representative.—(1) Where a decree", 9.0, True, 90.0)]
                    + [(LAW, 9.0, False, 72.0)] * 6
                    + [("1. Subs. by Act 3 of 1921, s. 2, for “will be discharged”.", 9.0, False, 72.0)])
    kept = [line.text for line in rulebook.clean_lines(normal + small, notes="shape")]
    assert kept[31].startswith("52. Enforcement of decree") and sum(t == LAW for t in kept) == 36
    assert not any("Subs. by" in t for t in kept)


# ---------------------------------------------------------------------------
# The official texts in the library
# ---------------------------------------------------------------------------
def test_cpc_orders_limitation_articles_and_bail_table_are_in(law_library):
    assert law_library.get("CPC:O7R11").label == "CPC O. VII r. 11"
    assert law_library.get("CPC:O7R11").title == "Rejection of plaint"
    assert law_library.get("CPC:O7R11").page                       # page of the official PDF
    assert law_library.get("CPC:O39R1").title.startswith("Cases in which temporary injunction")
    assert law_library.get("CPC:O13AR1") is not None                # commercial summary judgment
    assert law_library.get("LIMITATION:ART54").label == "Limitation Act Art. 54"
    assert "Three years" in law_library.get("LIMITATION:ART54").text
    row = law_library.get("BNSS:FS_103(1)")
    assert row is not None and "Non-bailable" in row.text and "Court of Session" in row.text
    assert sum(1 for s in law_library.sections if s.startswith("CPC:O")) > 650
    assert sum(1 for s in law_library.sections if s.startswith("LIMITATION:ART")) == 137
    assert sum(1 for s in law_library.sections if s.startswith("BNSS:FS_")) > 400


def test_court_acts_are_in(law_library):
    assert "mobile court" in law_library.get("GRAMNYAYALAYA:9").title.lower()
    assert law_library.get("GENCLAUSES:10").title == "Computation of time"
    for code in ("COURTFEES", "SUITSVALUATION", "OATHS", "NOTARIES", "PROBATION", "MEDIATION", "HMGA", "TRUSTS"):
        assert law_library.act(code) and len(law_library.act(code).sections) >= 3


@pytest.mark.parametrize("text, expected", [
    ("Can the plaint be rejected under Order 7 Rule 11 CPC?", ["CPC:O7R11"]),
    ("O.XXXIX rr. 1 & 2 CPC", ["CPC:O39R1", "CPC:O39R2"]),
    ("Order XXXIX Rules 1 and 2", ["CPC:O39R1", "CPC:O39R2"]),
    ("Article 54 of the Limitation Act", ["LIMITATION:ART54"]),
    ("Order 7 Rule 11 CPC and section 11 CPC", ["CPC:O7R11", "CPC:11"]),
])
def test_rule_references_in_questions(law_library, text, expected):
    assert law_library.find_refs(text) == expected


@pytest.mark.parametrize("tag, expected", [
    ("CPC O. VII r. 11", "CPC:O7R11"), ("CPC Order 7 Rule 11", "CPC:O7R11"),
    ("Limitation Act Art. 54", "LIMITATION:ART54"), ("BNSS Sch. I (BNS s. 318(4))", "BNSS:FS_318(4)"),
    ("BNS s. 103", "BNS:103"), ("Gram Nyayalayas Act s. 9", "GRAMNYAYALAYA:9"),
])
def test_citation_tags_resolve(law_library, tag, expected):
    assert law_library.resolve_citation(tag)[1] == expected


def test_unknown_rule_of_a_known_book_is_flagged(law_library):
    assert law_library.resolve_citation("CPC O. XCIX r. 1") == ("CPC", None)


def test_bns_section_brings_its_bail_classification(law_search):
    hits = law_search.search("Is u/s 318 BNS bailable?", k=6)
    ids = [h.section.id for h in hits]
    assert ids[0] == "BNS:318" and any(i.startswith("BNSS:FS_318") for i in ids)


def test_check_answer_verifies_rule_citations(law_library):
    from app.assistant import check_answer
    from app.retrieval import Sources

    sources = Sources(query="q")
    result = check_answer("Reject it [CPC O. VII r. 11]; limitation [Limitation Act Art. 54]; and [CPC O. XCIX r. 1].",
                          sources, law_library)
    status = {c["text"]: c["status"] for c in result["citations"]}
    assert status["CPC O. VII r. 11"] == "library" and status["Limitation Act Art. 54"] == "library"
    assert status["CPC O. XCIX r. 1"] == "unknown"


HINT_TITLE_WORDS = {"rejection of plaint": "rejection", "temporary injunction": "temporary injunction",
                    "written statement": "written statement", "leave to defend": "appearance",
                    "summary judgment": "summary judgment", "mobile court": "mobile", "money lent": "money lent",
                    "adverse possession": "possession", "computation of time": "time"}


@pytest.mark.parametrize("phrase, word", HINT_TITLE_WORDS.items())
def test_procedure_hints_point_at_the_right_rules(law_library, phrase, word):
    assert word in law_library.get(LEGAL_HINTS[phrase][0]).title.lower()


# ---------------------------------------------------------------------------
# Adding your own rule book
# ---------------------------------------------------------------------------
def rules_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    text = ("RULES OF THE TEST HIGH COURT, 2026\n\nCHAPTER I\nPRELIMINARY\n"
            "1. Short title. These rules may be called the Test High Court Rules, 2026.\n"
            "2. Definitions. In these rules Registrar means the Registrar General.\n\n"
            "CHAPTER II\nFILING\n1. Paper book. Every appeal shall be filed with a paper book in three sets.\n"
            "2. Caveat. A caveat shall be valid for ninety days from the date of filing.\n"
            "3. Urgent mentioning. Urgent matters may be mentioned before the Bench at 10.30 a.m.\n")
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 780), text, fontsize=11)
    return doc.tobytes()


def wait_for(code, seconds=60):
    from app import rulebooks

    end = time.time() + seconds
    while time.time() < end:
        job = next((j for j in rulebooks.jobs() if j["code"] == code), None)
        if job and job["status"] in ("done", "failed"):
            return job
        time.sleep(0.3)
    raise AssertionError("the rule book was not read in time")


def test_add_and_remove_a_rule_book(workspace):
    from app import rulebooks
    from app.laws import library

    code = rulebooks.start(rules_pdf(), "test.pdf", "Rules of the Test High Court, 2026", "Test HC Rules",
                           court="Test High Court", jurisdiction="Testland", numbering="chapters")
    job = wait_for(code)
    assert job["status"] == "done", job
    lib = library()
    book = lib.act(code)
    assert book.user and book.kind == "rules" and book.court == "Test High Court"
    assert lib.get(f"{code}:CH2R2").title == "Caveat"
    assert lib.resolve_citation("Test HC Rules Ch. II r. 2") == (code, f"{code}:CH2R2")
    assert lib.find_refs("What does Chapter II Rule 3 of the Test HC Rules say?") == [f"{code}:CH2R3"]
    assert rulebooks.remove(code)
    assert library().act(code) is None


def test_rule_book_api(client):
    from app import rulebooks

    courts = client.get("/api/laws/courts").json()
    names = [c["name"] for c in courts["courts"]]
    assert "Supreme Court of India" in names and "Delhi High Court" in names and len(names) >= 27
    response = client.post("/api/laws/books", files={"book": ("rules.pdf", rules_pdf(), "application/pdf")},
                           data={"name": "Rules of the Test High Court, 2026", "cite": "Test HC Rules",
                                 "court": "Test High Court", "numbering": "chapters"})
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    assert wait_for(code)["status"] == "done"
    section = client.get(f"/api/laws/sections/{code}:CH2R2").json()
    assert section["label"] == "Test HC Rules Ch. II r. 2" and section["book_kind"] == "rules"
    assert section["pdf_url"].startswith(f"/api/laws/books/{code}/pdf")
    pdf = client.get(section["pdf_url"].split("#")[0])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.post("/api/laws/books", files={"book": ("x.pdf", b"not a pdf", "application/pdf")},
                       data={"name": "X"}).status_code == 400
    assert client.delete(f"/api/laws/books/{code}").status_code == 200
    assert client.delete("/api/laws/books/CPC").status_code == 404      # only your own books can be removed
    assert rulebooks.jobs() is not None


def test_official_pdf_is_served_for_cpc_orders(client):
    section = client.get("/api/laws/sections/CPC:O7R11").json()
    if not section.get("pdf_url"):
        pytest.skip("official PDFs not downloaded on this PC")
    response = client.get(section["pdf_url"].split("#")[0])
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------------------
# The inbox folder: rule-book PDFs saved there are added automatically
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filename, court, state", [
    ("Bombay High Court Appellate Side Rules 1960.pdf", "Bombay High Court", "Maharashtra; Goa"),
    ("Civil_Rules_of_Practice_Kerala_1971.pdf", "Kerala High Court; District courts", "Kerala"),
    ("FRD_Volume1 Punjab Haryana.pdf", "Punjab and Haryana High Court", "Punjab; Haryana; Chandigarh"),
    ("some rules.pdf", "", ""),
])
def test_inbox_guesses_the_court_from_the_file_name(filename, court, state):
    from app.rulebooks import guess_book

    guess = guess_book(filename)
    assert guess["court"] == court and guess["jurisdiction"] == state
    assert len(guess["cite"]) <= 45 and "_" not in guess["name"]


def test_inbox_adds_a_saved_pdf(workspace):
    import os

    from app import rulebooks
    from app.laws import library

    inbox = rulebooks.inbox_dir()
    inbox.mkdir(parents=True, exist_ok=True)
    pdf = inbox / "Test High Court Rules 2026.pdf"
    pdf.write_bytes(rules_pdf())
    old = time.time() - 60
    os.utime(pdf, (old, old))                       # finished saving a minute ago
    (inbox / "still-downloading.pdf").write_bytes(rules_pdf())
    codes = rulebooks.scan_inbox()
    assert len(codes) == 1 and not pdf.exists() and (inbox / "still-downloading.pdf").exists()
    assert wait_for(codes[0])["status"] == "done"
    assert library().act(codes[0]).name == "Test High Court Rules 2026"
    rulebooks.remove(codes[0])


def test_rule_book_from_pasted_text(client):
    text = ("CIVIL RULES OF PRACTICE, TESTLAND, 1971\n\nCHAPTER I\nPRELIMINARY\n"
            "1. Short title. These rules may be called the Civil Rules of Practice, Testland, 1971.\n"
            "2. Definitions. In these rules, unless the context otherwise requires, Court means a civil court.\n\n"
            "CHAPTER II\nPLEADINGS\n1. Paper. All pleadings shall be written on durable paper of foolscap size.\n"
            "2. Verification. Every pleading shall be verified at the foot by the party or by one of the parties pleading.\n"
            "3. Copies. A copy of every plaint shall be filed for each defendant along with process fee.\n")
    response = client.post("/api/laws/books", data={"text": text, "name": "Civil Rules of Practice, Testland, 1971",
                                                     "cite": "Testland CRP", "court": "Testland High Court",
                                                     "numbering": "chapters"})
    assert response.status_code == 200, response.text
    code = response.json()["code"]
    assert wait_for(code)["status"] == "done"
    rule = client.get(f"/api/laws/sections/{code}:CH2R2").json()
    assert rule["title"] == "Verification" and rule["pdf_url"] == ""
    assert client.post("/api/laws/books", data={"name": "Empty"}).status_code == 400
    assert client.delete(f"/api/laws/books/{code}").status_code == 200
