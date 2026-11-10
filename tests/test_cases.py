"""Tests for the landmark-judgment case notes (data/cases.json) and how they are found and cited."""

import json

from fastapi.testclient import TestClient

from app import answer, config
from app.cases import load_cases
from app.citations import check_citations
from app.constitution import load_constitution
from app.main import app

library = load_cases()


def test_case_library_is_valid():
    assert len(library.cases) >= 40
    ids = [c.id for c in library.cases]
    assert len(ids) == len(set(ids)) and all(i.startswith("case-") for i in ids)
    known = load_constitution().ids
    for case in library.cases:
        assert case.held and case.name and case.short.endswith(f"({case.year})"), case.id
        for provision in case.articles:  # every linked Article / Schedule entry must exist
            assert provision in known, (case.id, provision)


def test_find_cases_by_citation_name():
    assert library.find_by_name("Puttaswamy (2017)").id == "case-puttaswamy-2017"
    assert library.find_by_name("Puttaswamy, 2017").id == "case-puttaswamy-2017"
    assert library.find_by_name("Kesavananda Bharati v. State of Kerala (1973)").id == "case-kesavananda-1973"
    assert library.find_by_name("Some Unknown Case (1999)") is None
    assert [c.id for c in library.named_in("What did Kesavananda decide?")] == ["case-kesavananda-1973"]


def test_search_picks_fitting_cases(engine):
    def cases_for(question):
        return [c.case.id for c in engine.search_cases(question, engine.search(question))]

    assert cases_for("Do I have a right to privacy?")[0] == "case-puttaswamy-2017"
    assert "case-njac-2015" in cases_for("How are Supreme Court judges appointed?")
    assert "case-kihoto-hollohan-1992" in cases_for("What is the anti-defection law?")
    assert "case-indra-sawhney-1992" in cases_for("Is there a limit on reservations?")
    assert cases_for("What is the punishment for theft?") == []  # nothing fits: no case is added


def test_case_citations_are_checked():
    answer_text = "Beyond the text: privacy is protected [Puttaswamy (2017)]; see also [Kharak Singh (1962)]."
    result = check_citations(answer_text, ["21", "case-puttaswamy-2017"], library)
    assert result.verified == ["case-puttaswamy-2017"]
    assert result.unverified == ["case?Kharak Singh (1962)"]  # not in the library -> shown in red
    assert result.labels["case-puttaswamy-2017"] == "Puttaswamy (2017)"
    assert result.labels["case?Kharak Singh (1962)"] == "Kharak Singh (1962)"


def test_case_routes_and_ask_sends_case_notes(monkeypatch):
    with TestClient(app) as client:
        data = client.get("/api/judgments").json()
        assert any(c["id"] == "case-puttaswamy-2017" for c in data["cases"])
        note = client.get("/api/judgments/case-puttaswamy-2017").json()
        assert note["about"][0] == {"id": "21", "label": "Article 21"}
        assert "indiankanoon.org" in note["link"]
        assert client.get("/api/judgments/case-nothing").status_code == 404

        # The case notes found for a question are sent to Claude together with the Articles
        sent = {}

        async def fake_stream_answer(question, hits, style, history, cases=None):
            sent["prompt"] = answer.build_prompt_text(question, hits, style, history, cases)
            yield {"type": "done", "mode": "ai", "citations": {}}

        monkeypatch.setattr(config, "ANSWER_ENGINE", "claude_code")
        monkeypatch.setattr("app.main.stream_answer", fake_stream_answer)
        response = client.post("/api/ask", json={"question": "Do I have a right to privacy?"})
        first = json.loads(response.text.split("\n\n")[0].split("data: ", 1)[1])
        assert first["cases"][0]["id"] == "case-puttaswamy-2017"
        assert '<case cite_as="[Puttaswamy (2017)]"' in sent["prompt"]
