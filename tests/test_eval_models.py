"""
The model test (scripts/eval_models.py): its scoring is checked here, so a bug can't make a model look
better or worse than it is. No AI is called.
"""

import importlib.util
from datetime import date
from pathlib import Path

from app import config, intake

PROJECT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("eval_models", PROJECT / "scripts" / "eval_models.py")
eval_models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_models)

TODAY = date(2026, 11, 10)
EXPECTED = {
    "groups": [["fir.pdf", "order.pdf", "bail.pdf"], ["notice.pdf"]],
    "types": {"fir.pdf": ["FIR"], "order.pdf": ["order"], "bail.pdf": ["application"], "notice.pdf": ["notice"]},
    "dates": [{"date": "{d+4}", "time": "10:30", "what": "arguments"}],
}
NAMES = {1: "fir.pdf", 2: "order.pdf", 3: "bail.pdf", 4: "notice.pdf"}


def doc(upload_id, doc_type):
    return {"upload_id": upload_id, "doc_type": doc_type, "title": doc_type}


def test_relative_dates_become_real_dates():
    assert eval_models.render("Put up on {d+4}; FIR of {d-21}", TODAY) == "Put up on 14.11.2026; FIR of 20.10.2026"
    assert eval_models.render("{d+4}", TODAY, iso=True) == "2026-11-14"
    assert eval_models.render("{d+11:long} / {d+12:long} / {d+20:slash}", TODAY) == "21st November 2026 / 22nd November 2026 / 30/11/2026"
    assert eval_models.render("{d+20:slash}", TODAY, iso=True) == "2026-11-30"


def test_a_perfect_sort_scores_100():
    result = {"made_by": "ai", "groups": [
        {"upload_ids": [1, 2, 3], "plan": {"documents": [doc(1, "FIR"), doc(2, "Order"), doc(3, "Bail application")],
                                           "events": [{"date": "2026-11-14", "time": "10:30", "kind": "hearing", "include": True}]}},
        {"upload_ids": [4], "plan": {"documents": [doc(4, "Legal notice")], "events": []}},
    ]}
    scored = eval_models.score_sort(result, NAMES, EXPECTED, TODAY)
    assert scored["score"] == 1 and scored["mistakes"] == []


def test_sorting_mistakes_are_counted():
    # The bail application is put with the notice, the order is called a "Judgment",
    # the hearing time is wrong and a hearing is made up
    result = {"made_by": "ai", "groups": [
        {"upload_ids": [1, 2], "plan": {"documents": [doc(1, "FIR"), doc(2, "Judgment")],
                                        "events": [{"date": "2026-11-14", "time": "11:00", "kind": "hearing", "include": True},
                                                   {"date": "2026-12-01", "time": "", "kind": "hearing", "include": True},
                                                   {"date": "2026-11-01", "time": "", "kind": "hearing", "include": False}]}},
        {"upload_ids": [3, 4], "plan": {"documents": [doc(3, "Application"), doc(4, "Notice")], "events": []}},
    ]}
    scored = eval_models.score_sort(result, NAMES, EXPECTED, TODAY)
    # 6 pairs: fir+order right, fir+bail wrong, fir+notice right, order+bail wrong, order+notice right, bail+notice wrong
    assert scored["grouping"] == 3 / 6
    assert scored["types"] == 3 / 4
    assert scored["dates"] == 0          # the one date missed (wrong time) + one made-up hearing
    assert any("made up a hearing on 2026-12-01" in m for m in scored["mistakes"])


def test_files_the_rules_could_not_place_are_not_together():
    result = {"made_by": "rules", "groups": [
        {"upload_ids": [1, 2, 3], "plan": {"documents": [], "events": []}},
        {"upload_ids": [4, 5], "made_by": "rules", "reason": "No CNR, case number or party names found in these files",
         "plan": {"documents": [], "events": []}},
    ]}
    names = {**NAMES, 5: "receipt.pdf"}
    expected = {**EXPECTED, "groups": [["fir.pdf", "order.pdf", "bail.pdf"], ["notice.pdf"], ["receipt.pdf"]]}
    assert eval_models.score_sort(result, names, expected, TODAY)["grouping"] == 1


def test_answers_are_scored_on_citations_facts_and_made_up_citations():
    item = {"cite": [["BNS:318"], ["IPC:420"]], "mention": [["7 years", "seven years"]], "avoid": ["is non-cognizable"]}
    good = eval_models.score_answer("Up to seven years and fine. (Cognizable or non-cognizable: Cognizable.)", [
        {"id": "BNS:318", "status": "verified", "text": "BNS s. 318"},
        {"id": "IPC:420", "status": "library", "text": "IPC s. 420"}], item)
    assert good["score"] == 1 and good["cites_ok"] == 2 and good["made_up"] == 0
    bad = eval_models.score_answer("The offence is **non-cognizable**.", [
        {"id": "BNS:318", "status": "verified", "text": "BNS s. 318"},
        {"id": "", "status": "outside", "text": "XYZ Act s. 5"}], item)
    # 5 checks: BNS yes, IPC no, seven years no, avoided 'is non-cognizable' no, no red citations no
    assert bad["score"] == 1 / 5 and bad["made_up"] == 1
    assert "did not cite IPC:420" in bad["mistakes"]


def test_the_cheapest_model_close_to_the_best_is_good_enough():
    rows = [{"model": eval_models.NO_AI, "score": 0.5, "usd": 0, "items": [1]},
            {"model": "haiku", "score": 0.70, "usd": 0.001, "items": [1]},
            {"model": "sonnet", "score": 0.93, "usd": 0.05, "items": [1]},
            {"model": "opus", "score": 0.96, "usd": 0.10, "items": [1]}]
    assert eval_models.good_enough(rows)["model"] == "sonnet"


def test_the_sample_set_is_consistent_and_runs_without_ai(workspace, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    piles = eval_models.load_piles(PROJECT / "evals" / "sample", TODAY)
    assert len(piles) >= 2
    for number, pile in enumerate(piles, 1):
        expected = pile["expected"]
        in_groups = sorted(name for group in expected["groups"] for name in group)
        assert in_groups == sorted(pile["texts"]) == sorted(expected["types"])   # every file has a right answer
        assert "{d" not in "".join(pile["texts"].values())                       # the dates were filled in
        uploads = eval_models.uploads_for(pile, number)
        names = {u["id"]: u["original_name"] for u in uploads}
        scored = eval_models.score_sort(intake.sort(uploads), names, expected, TODAY)
        assert 0 <= scored["score"] <= 1 and scored["made_by"] == "rules"
    import json

    questions = json.loads((PROJECT / "evals" / "sample" / "questions.json").read_text(encoding="utf-8"))["questions"]
    assert len(questions) >= 5 and all(q["cite"] and q["mention"] for q in questions)
