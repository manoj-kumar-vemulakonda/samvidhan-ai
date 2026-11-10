# The model test: which Claude model is good enough for each job?

Cheaper models cost less but may make more mistakes. This test runs the app's own code on files and questions whose right answers are known, once per model, and reports the score, time and cost of each. Then you can pick the cheapest model that is good enough.

## Run it

Double-clicking won't work; open a terminal in `D:\lawyers-ai` and type:

```
.venv\Scripts\python.exe scripts\eval_models.py
```

| Option | What it does |
|---|---|
| *(nothing)* | Tests haiku, sonnet and opus through Claude Code (your subscription; nothing is charged per call) |
| `--models sonnet,opus:high` | Only these models. `:high` means "thinking longer", like the 🧠 Deep button |
| `--jobs sort` or `--jobs answer` | Only one job |
| `--set private` | Your own real files (see below) instead of the made-up ones |
| `--engine api` | Through your API key (charged to your credits); models are then full names like `claude-sonnet-5-5` |

It takes about 15 minutes for three models. The report is printed and saved in `evals\results\`.

## What is tested

**1. Sorting a pile of files (the home-screen drop box).** For each pile it checks three things:
- **Grouping:** were the files that belong to the same case put together, and the others kept apart?
- **Document types:** was each file named as the right kind of document (FIR, order, notice…)?
- **Dates:** was each hearing found with the right date and time? A coming hearing that isn't in the files counts as a mistake.

"no AI (rules)" is always tested too. It shows how well the app does with no AI at all.

**2. Answering law questions.** One point for each:
- right provision cited;
- key fact stated (for example "7 years");
- wrong statement avoided.

There's also a point for citing nothing that doesn't exist (no red chips).

The report's **"Good enough"** line names the cheapest model whose score is within 5 points of the best one.

## The made-up files (`evals\sample\`)

There are three piles, and six law questions:
- **Pile 1** mixes a cheating FIR and its bail matter with a cheque bounce and a divorce summons.
- **Pile 2** mixes a money-recovery suit with a consumer complaint. The notice sent before the suit has no case number, so it can only be matched by the parties.
- **Pile 3** is harder: a Hindi FIR whose bail order is in English, with scanning errors (`C0URT`); a handwritten-style date slip; and two *different* cheque cases between the same two parties, which must stay apart. Dates are written three ways.

The dates are written as `{d+4}` (4 days after the test runs) or `{d-21}` (21 days before), so "coming" and "past" hearings stay right whenever you run it. `{d+6:long}` writes "16th November 2026", and `{d+6:slash}` writes 16/11/2026.

**The test already found bugs.** Its first run showed all three models missing the same two citations. The cause was the app's law search, not the models:
- "section 318(4)" was read as sections 318 *and* 4;
- BNS sections that are mapped to the IPC sub-section by sub-section (318(1)–(4) → IPC 415–420) had no old-law equivalent at all;
- "cheque returned unpaid" didn't bring up NI Act s. 142.

All three are fixed, and each has a regression test. This is why a test like this is worth having.

## Your own real files (`evals\private\`, the better test)

Real files are messier than made-up ones: scans, handwriting, Hindi, many pages. Testing with 20–50 of them tells you much more.

1. Make a folder for each pile, for example `evals\private\piles\pile-1\`, and copy the files into it (PDF, Word, photos).
2. In the same folder, make `expected.json` with the right answers. Use the file names exactly as they are, and real dates (`2026-11-21`):

```json
{
  "title": "Bail matter and a cheque bounce",
  "expected": {
    "groups": [["FIR.pdf", "bail order.pdf"], ["cheque memo.jpg", "notice.pdf"]],
    "types": {"FIR.pdf": ["FIR"], "bail order.pdf": ["order"], "cheque memo.jpg": ["memo", "return"], "notice.pdf": ["notice"]},
    "dates": [{"date": "2026-11-21", "time": "10:30", "what": "bail arguments"}]
  }
}
```

- `groups`: which files belong to the same case.
- `types`: for each file, words of which at least one must appear in the type the app gives it.
- `dates`: the hearings the app must find. `time` is optional.

3. Optionally, add `evals\private\questions.json` with your own questions, in the same format as `evals\sample\questions.json`.
4. Run `.venv\Scripts\python.exe scripts\eval_models.py --set private`.

**Privacy:** `evals\private\` and `evals\results\` are never uploaded to GitHub (they're in `.gitignore`). The test copies the files into its own empty workspace, `.cache\eval-ws`, and deletes it at the end. Your real cases are never touched. With Claude Code or the API, the file texts are sent to Claude, the same as when you use the app.
