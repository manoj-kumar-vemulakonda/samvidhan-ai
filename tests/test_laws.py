"""Tests for the Acts library: loading, section references, the IPC -> BNS converter and search."""

import pytest

from app.laws import LEGAL_HINTS


def test_library_has_the_main_acts(law_library):
    assert len(law_library.acts) >= 100
    assert len(law_library.sections) >= 11000
    for code in ("BNS", "BNSS", "BSA", "IPC", "CRPC", "IEA", "CPC", "NIA", "LIMITATION", "CONTRACT", "COMPANIES"):
        assert code in law_library.acts, code
    assert len(law_library.act("BNS").sections) == 358
    assert law_library.get("BNS:103").title == "Punishment for murder"
    assert law_library.get("IPC:498A").title.startswith("Husband or relative of husband")
    assert law_library.act("IPC").status == "repealed" and law_library.act("IPC").replaced_by == "BNS"


def test_known_dataset_errors_stay_corrected(law_library):
    # The dataset's footnote put the 15 -> 30 days change in NI Act s. 138 in 2015; it was 2002. Since v3.6
    # s. 138 is India Code's text: no footnotes in the text (the PDF page has them) and no wrong year
    section = law_library.get("NIA:138")
    assert "within thirty days" in section.text and "2015" not in section.text
    assert law_library.act("NIA").source.startswith("India Code") and section.page and section.page >= 8


def test_official_acts_keep_their_illustrations(law_library):
    # India Code prints Illustrations small, like footnotes; v3.5 lost them (v3.6 found and fixed it)
    assert "Illustration" in law_library.get("BNS:110").text
    assert "A makes a contract with B to buy B’s horse" in law_library.get("CONTRACT:32").text
    assert "I promise to pay B or order Rs. 500" in law_library.get("NIA:4").text
    succession = law_library.get("SUCCESSION:5").text
    assert "Illustrations" in succession and "SCHEDULE" not in succession   # Schedules no longer glued on


def test_the_dataset_acts_are_official_now(law_library):
    # v3.7: every first-101 Act that India Code has as a readable PDF uses its text; these 7 can't (yet)
    dataset = sorted(code for code, act in law_library.acts.items() if act.source.startswith("Open dataset"))
    assert dataset == ["CRPC", "IEA", "INCOMETAX", "IPC", "MVA", "STAMP", "WATER"]
    # sections the dataset lacked, which India Code writes "45-IA" or leaves out of its own list (DPA s. 8A)
    assert law_library.get("RBI:45IA").title == "Requirement of registration and net owned fund"
    assert law_library.get("DPA:8A").title == "Burden of proof in certain cases"
    assert law_library.get("ARBITRATION:29A").title == "Time limit for arbitral award"     # lost in v3.6, back
    assert law_library.get("NGT:10A") is not None                   # "1[2[10A.": inserted, then substituted
    # the Bill's Statement of Objects and Reasons and the Schedules are not glued to the last section
    assert "OBJECTS AND REASONS" not in law_library.get("BNS:358").text
    assert len(law_library.get("RBI:61").text) < 200 and law_library.get("RBI:SCH2") is not None


@pytest.mark.parametrize("text, expected", [
    ("Is u/s 302 IPC bailable?", ["IPC:302"]),
    ("sections 420, 406 and 120B IPC", ["IPC:420", "IPC:406", "IPC:120B"]),
    ("Section 138 of the Negotiable Instruments Act", ["NIA:138"]),
    ("N.I. Act s.138", ["NIA:138"]),
    ("BNS 103 punishment", ["BNS:103"]),
    ("धारा 103 बीएनएस", ["BNS:103"]),
    ("petition under s. 482 CrPC", ["CRPC:482"]),
    ("what does section 65B of the Evidence Act say", ["IEA:65B"]),
    ("u/s 13B HMA", ["HMA:13B"]),
    ("Section 5 Limitation Act delay", ["LIMITATION:5"]),
    ("IT Act section 66A", ["ITACT:66A"]),
    # a sub-section is not a section of its own: 318(4) is s. 318, not also s. 4 (found by the model test)
    ("cheating under section 318(4) of the BNS", ["BNS:318"]),
    ("u/s 318(4), 316(2) BNS", ["BNS:318", "BNS:316"]),
])
def test_finds_section_references(law_library, text, expected):
    assert law_library.find_refs(text) == expected


def test_ordinary_words_are_not_act_names(law_library):
    assert law_library.find_refs("it act as a shield for section 5 people") == []
    assert law_library.mentioned_acts("a posh hotel") == []


@pytest.mark.parametrize("code, number, target", [
    ("IPC", "302", "BNS:103"), ("IPC", "420", "BNS:318"), ("IPC", "498A", "BNS:85"), ("IPC", "376", "BNS:64"),
    ("CRPC", "438", "BNSS:482"), ("CRPC", "125", "BNSS:144"), ("IEA", "65B", "BSA:63"), ("BNS", "103", "IPC:302"),
    # mapped sub-section by sub-section: BNS 318(1)-(4) = IPC 415, 417, 418, 420 (found by the model test)
    ("BNS", "318", "IPC:420"), ("BNS", "318", "IPC:415"),
])
def test_old_to_new_converter(law_library, code, number, target):
    result = law_library.convert(code, number)
    assert target in [m["section_id"] for m in result["mappings"]]


def test_converter_explains_sedition(law_library):
    mappings = law_library.convert("IPC", "124A")["mappings"]
    assert mappings[0]["status"] == "Deleted" and "152" in mappings[0]["note"]
    assert law_library.convert("IPC", "420")["mappings"][0]["label"] == "BNS s. 318(4)"


# Every hint must point at a section about that topic (checked on its title)
HINT_TITLE_WORDS = {
    "anticipatory bail": "apprehending arrest", "cheque bounce": "dishonour", "murder": "murder", "cheating": "cheating",
    "dowry death": "dowry death", "divorce": "divorce", "electronic evidence": "electronic", "caveat": "caveat",
    "fir": "information in cognizable", "quashing": "inherent powers", "condonation of delay": "extension of prescribed period",
    "specific performance": "specific performance", "rti application": "request for obtaining information",
}


@pytest.mark.parametrize("phrase, word", HINT_TITLE_WORDS.items())
def test_hints_point_at_the_right_sections(law_library, phrase, word):
    section = law_library.get(LEGAL_HINTS[phrase][0])
    assert section is not None and word in section.title.lower()


def test_every_hint_exists(law_library):
    missing = [s for ids in LEGAL_HINTS.values() for s in ids if law_library.get(s) is None]
    assert len(missing) <= 2, missing  # a couple of Acts lack some sections in the dataset


@pytest.mark.parametrize("question, expected, top", [
    ("punishment for cheating", "BNS:318", 3),
    ("cheque bounce notice within 30 days", "NIA:138", 3),
    ("my client's cheque was returned unpaid, by when must the complaint be filed", "NIA:142", 3),
    ("anticipatory bail", "BNSS:482", 3),
    ("maintenance of wife", "BNSS:144", 5),
])
def test_search_finds_the_section(law_search, question, expected, top):
    ids = [h.section.id for h in law_search.search(question, k=8)]
    assert expected in ids[:top], ids


def test_search_adds_the_new_law_for_an_old_section(law_search):
    hits = law_search.search("Is u/s 302 IPC bailable?", k=6)
    assert [h.section.id for h in hits[:2]] == ["IPC:302", "BNS:103"]
    assert hits[0].reason == "exact reference" and hits[1].reason == "old/new equivalent"
    assert any(m["label"].startswith("BNS s. 103") for m in hits[0].related)
