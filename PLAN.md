# Plan: Samvidhan AI, the AI workspace for Indian lawyers

*(Working name, easy to change in one place: `APP_NAME` in `app/config.py`.)*

## Version 3.1 (10 Nov 2026): official texts and court rules

You asked for every rule of the Supreme Court, the High Courts and the lower (including mobile) courts. Here is what was added, all from official sources:

- **From India Code** (indiacode.gov.in), using its public API:
  - The CPC's **Orders and Rules**: 715, cited like `[CPC O. VII r. 11]`, including the commercial Order XI, XIII-A and XV-A.
  - The Limitation Act's **137 Articles**: `[Limitation Act Art. 54]`.
  - The **BNSS First Schedule**: 441 rows showing whether an offence is bailable or cognizable and which court tries it. When a BNS section comes up, its row comes with it.
  - **45 more central Acts:** General Clauses, Court-fees, Suits Valuation, Gram Nyayalayas (the mobile village courts), Civil Courts 1887, Small Cause Courts, Oaths, Notaries, Mediation 2023, Probation, Criminal Procedure (Identification), NIA, Lokpal, Hindu Minority and Guardianship, the Muslim, Christian and Parsi personal laws, Trusts, Waqf, MTP, PCPNDT, Citizenship, Immigration and Foreigners 2025, Railways, and others.
  - **State Acts for district courts:** civil-courts and court-fees Acts.
- **From the courts' own websites:**
  - **Supreme Court:** the Supreme Court Rules, 2013 (a scanned gazette, read with OCR), the 2024–25 amendments, the Handbook on Practice and Procedure (updated 06.10.2025) and the contempt rules.
  - **High Courts:**
    - Delhi: the Original Side Rules 2018, and the Rules and Orders (Vol. I–V, which also govern Delhi's district courts).
    - Allahabad 1952.
    - Gujarat 1993, plus the Civil and Criminal Manuals.
    - Rajasthan 1952, plus the General Rules 2018.
    - Orissa 1948, plus the GRCO.
    - Jharkhand 2001, plus the Civil Court Rules.
    - J&K 1999, plus the Gram Nyayalayas Rules 2025.
    - Manipur 2019, plus the Criminal Rules and Orders.
    - Meghalaya 2013.
    - Chhattisgarh 2007.
    - The AP Appellate Side Rules.
    - Family-court, contempt, PIL, case-flow and video-conferencing rules of several High Courts.
- **Every rule opens its official PDF at the right page.**
- **Library → 🏛 Courts and their rules** lists all 25 High Courts, the Supreme Court, district courts and Gram Nyayalayas: what is in, what is missing, and each court's official site.
- **＋ Add a rule book:** any court's PDF becomes citable in minutes, and scanned PDFs are read with OCR. You can also save a PDF into the inbox folder, or paste the text. This is how the rule books that are still missing (Bombay, Madras, Calcutta, P&H, and Kerala's Rules of Practice) can be added.
- **Ranking:** the rules of the case's court and the lawyer's own courts come first.
- **Search index:** it is now incremental, so new rules are added in minutes and the old 23,511 pieces are reused.
- **Rule books found on district-court websites** (dcourts.gov.in), for courts whose own sites did not answer:
  - the Karnataka Civil and Criminal Rules of Practice;
  - the Tamil Nadu Civil Rules of Practice;
  - the AP/Telangana Criminal Rules of Practice 1990;
  - the Bombay and Madras e-filing and video-conferencing rules;
  - Calcutta and MP rule amendments;
  - Sikkim and Tripura family-court, mediation, ADR and case-flow rules;
  - the Gauhati e-Courts rules.

**Result:** the library has **268 books and 27,122 citable provisions**:
- 171 Acts.
- 94 court rule books (including the Madras HC Writ Rules 2021 from the TN Gazette), now including the main rules of the Kerala, Karnataka, MP, Sikkim, Gauhati, Telangana, Patna and Uttarakhand courts.
- 3 handbooks.

**Tests:** 187 pass.

**Live check with Claude Code:**
- "Is s. 318(4) BNS bailable, which court?" was answered from the BNSS First Schedule: cognizable, non-bailable, Magistrate of the first class. All 7 citations were green.
- "Can the plaint in a 2021 specific-performance suit filed in 2025 be rejected?" was answered citing `[CPC O. VII r. 11]` and `[Limitation Act Art. 54]`, with all citations green.

## Status: version 3 built (9 Nov 2026)

Versions 1 and 2 (the Constitution assistant) are done. See "History" at the end.
Version 3 turns the app into a **workspace for a practising lawyer**, as you described it. Everything in the tables below is built.

**Checked with real AI (Claude Code), using sample case files:**
- **Organisation plan (33 s):**
  - It split a 3-page bundle into a bail application (pp. 1–2) and a vakalatnama (p. 3), and read the photo of a legal notice by OCR.
  - It filed 6 documents into 6 folders with new names.
  - It added the 12 Nov 10:30 hearing and 2 deadlines to the calendar.
  - It filled in the title, court and case number.
  - It warned that the medical records the court asked for are missing.
- **Questions:**
  - A case question was answered from the order, citing `[Doc 6, p. 1]`.
  - The general question on cheating gave a BNS ↔ IPC table, with all citations green.
  - A Tamil question got a Tamil answer.
- **Hearing-brief workflow:** a 6-step plan, then the brief in 51 s.
- **Litigation review:** 6 documents in parallel in 52 s, with a risk chart, a report, and Excel and ZIP exports.

**Tests:** 145 pass, the 77 old ones plus 68 new. The page was checked in headless Edge at desktop and phone sizes, with no JavaScript errors.

**Not checked live:**
- The API engine (no key). It is tested with fakes.
- The real Windows pop-up for desktop notifications (it needs a person to click "Allow"). Reminder timing is covered by tests.

## What you asked for, and how each point is built

| # | Your words | How it works in the app |
|---|---|---|
| 1 | Clean interface | A three-column screen, like a mail app: cases on the left, the chat in the middle, the calendar on the right. Calm colours and no clutter. It also fits a phone. |
| 2 | Right side: my calendar, next hearing, which case | The right column shows a month calendar with coloured dots, a **Next hearing** card (date, time, court, case), the next 14 days, and your reminders. |
| 3 | Left side: existing cases, and New case (= a new chat) | The left column lists your cases (search box, active and closed). **+ New case** creates the case, its folder on disk and its first chat in one click. |
| 4 | Every chat takes files; the AI proposes how to arrange them | Drag files into any chat, or click 📎. PDF, Word, images and scanned pages are read with **OCR**. The AI then writes an **organisation plan**: for each document its type, date, a new file name and folder, and a one-line summary. A PDF that holds several documents is split. Dates found are proposed for the calendar. |
| 5 | The user approves, or changes the plan and saves | The plan is an editable card: change any name, folder, type, date or page range; untick dates; or type "put all orders in one folder" and the AI revises it. Nothing moves until you press **Approve & organise**. |
| 6 | Files separated and renamed by the approved plan | The files are copied into numbered folders inside the case folder, for example `03 Orders & Judgments\2026-11-02 Order - Bail application listed.pdf`. Split PDFs become separate files. The untouched originals are kept in `_originals`. |
| 7 | Every case on its own, never combined | Each case has its own folder, chats, documents, search index and dates. A case chat can only see that case's files, which is checked in the code and covered by tests. |
| 8 | A new case updates the calendar from its files | Hearing dates, "reply within 4 weeks" deadlines and limitation dates found in the files go into the calendar when you approve the plan. Each entry links back to the page it came from. |
| 9 | Personalised notifications and reminders | A reminder service runs inside the app. It sends a morning agenda ("Good morning, Adv. Sharma: 2 hearings today"), an evening-before alert for hearings, and 7-, 3- and 1-day countdowns for deadlines and limitation. You can also ask for a one-click **hearing brief** for any hearing. You get a bell with a counter, pop-ups, and Windows desktop notifications if you allow them. You choose the times in Settings. |
| 10 | Chat with anything, independent of any case | **General chats** in the left column work like ChatGPT for Indian law. Files dropped there are read for that chat only, and one click can turn them into a new case. |
| 11 | The AI knows all the laws and rules | The library grows from 1 law to **about 105**: the Constitution, the new criminal laws (BNS, BNSS, BSA), the old ones they replaced (IPC, CrPC, Evidence Act) for older cases, and about 95 central Acts (CPC, Contract, NI Act, Limitation, Companies, IBC, Arbitration, Consumer Protection, DV Act, POCSO, NDPS, IT Act, RTI, labour codes, GST, Income-tax Act 2025 and more), about 12,000 sections in all. It also has **2,600 old-to-new section mappings** (IPC→BNS, CrPC→BNSS, Evidence Act→BSA), 45 landmark judgments, and Claude's own knowledge, which is marked "not verified" when it isn't backed by a section in the library. |

The other items from the earlier status table are built too:

| Item | How |
|---|---|
| IPC → BNS converter | Type "IPC 420" or "498A", and see BNS 318(4) or BNS 85, both texts side by side, and whether the provision changed. The chat uses the converter automatically, so an IPC question also gets the BNS section. |
| Reusable workflows with plan approval, and "Skills" | You pick a workflow. The AI writes a step plan, may ask questions first, waits for your approval, then runs it with progress shown. Each workflow is a plain-English **Skill** file in `skills\`, which you can edit or copy in the app or in Notepad. The ten built in are: Check an FIR, Rights after arrest, Bail application, Hearing brief, Case chronology, Reply to a legal notice, Cheque-bounce (s.138) notice, RTI application, Check a rent agreement, Limitation check. |
| Drafting | Any answer or workflow result can be downloaded as a **Word (.docx)** file under your name and saved into the case's `08 Drafts` folder. |
| OCR for scanned PDFs and photos | RapidOCR runs on your PC, free. For pages it can't read well, such as Hindi, handwriting or poor scans, Claude reads the page image instead. That uses your subscription and can be switched off. |
| Bulk review table | In a case, the **Review** tab is a table: rows are documents, columns are your questions. The AI fills every cell with an answer, a quote and a page, and it can be exported to Excel. |
| Risk charts, due diligence with sub-agents | Due-diligence templates (property title, contract, litigation file) add a **risk** rating to each cell. One AI "sub-agent" per document works in parallel. A final agent writes the **due-diligence report**, and a **risk chart** shows where the problems are. |
| Data rooms | Each case's numbered folders are its data room. **Export data room** makes one ZIP with every folder plus an `Index.xlsx` (document, type, date, summary). |
| Other Indian languages | Questions in Tamil, Telugu, Bengali, Marathi, Gujarati, Kannada, Malayalam, Punjabi, Odia and Urdu, plus Hindi and Hinglish, are detected by their script. They are searched in English, and the answer comes back in the same language. You can also set a fixed answer language in Settings. |
| eCourts (CNR) | Each case has a CNR field. The app checks the CNR and explains it (state, district, court, year). One click opens the official eCourts page, where you type the CAPTCHA. If you save that status page as a PDF and drop it into the case, its next hearing date goes into the calendar. There is no scraping and no CAPTCHA bypass. |

## The screen

```
┌────────────────────┬───────────────────────────────────────────┬─────────────────────┐
│ ⚖ Samvidhan AI      │ State v. Ramesh Kumar        Chat Files Review│  November 2026  ‹ › │
│ [+ New case]        │ Saket Courts · Bail · CNR DLST01…           │  Mo Tu We Th Fr Sa Su│
│ 🔍 Find a case      │ ─────────────────────────────────────────── │   …  ●  …   ●       │
│ CASES               │  You: (3 files)                             │ NEXT HEARING         │
│ ● State v. Ramesh   │  AI: Organisation plan  [table, editable]   │ Thu 12 Nov · 10:30   │
│   Sharma v. Gupta   │      [Approve & organise] [Ask for changes] │ State v. Ramesh      │
│ GENERAL CHATS       │  AI: Organised 5 documents, 2 dates added   │ Saket, Court 4       │
│   + New chat        │  You: What did the court direct last time?  │ NEXT 14 DAYS         │
│   Limitation for…   │  AI: … [Doc 3, p.2] [BNSS s. 480]           │ • 15 Nov  Reply due  │
│ TOOLS               │                                             │ REMINDERS 🔔 2        │
│   Law library       │ ┌─────────────────────────────────────────┐ │ • Hearing tomorrow…  │
│   IPC → BNS         │ │ 📎  Ask about this case…   Workflows ▾  ➤│ │                      │
│   Workflows · Skills│ └─────────────────────────────────────────┘ │                      │
│   Settings          │                                             │                      │
└────────────────────┴───────────────────────────────────────────┴─────────────────────┘
```

## How it's built

**Storage:** one folder holds all your work, `D:\lawyers-ai\workspace\`.
- `samvidhan.db` is a SQLite database. It needs no server and is built into Python. It holds cases, chats, messages, documents, the text of every page, dates, reminders, reviews and settings.
- `cases\<case name> (<id>)\` holds the numbered folders and `_originals\`.
- Searching a case's own files uses SQLite FTS5 (keywords) plus ChromaDB (meaning). Every search is filtered by the case's id.

**The AI:** a single module, `app/llm.py`, runs every AI task.
- It uses Claude Code on your PC (your subscription) or the API.
- It supports streaming text, JSON answers checked against a schema (`--json-schema`), and reading page images.
- Small jobs use a fast model; answers, plans and drafts use the main model.

**The pipeline for files:**

```
upload ─► save in _originals ─► read text (PyMuPDF / Word / OCR) per page
       ─► AI organisation plan (or a keyword-based plan when AI is off)
       ─► you edit / approve ─► split + rename + file ─► index the pages
       ─► dates into the calendar ─► reminders
```

**The law library:**
- `scripts/build_laws.py` turns the downloaded Acts into one clean format, `data/laws/*.json`, plus `concordance.json` (old → new sections).
- One search index covers every Act, using the same exact-reference, meaning and keyword methods as the Constitution.
- Citations look like `[BNS s. 103]`, `[NI Act s. 138]`, `[Article 21]` or `[Doc 4, p. 2]`, and every one is checked: green if it was in the sources, red if not.

**Live updates:** one event stream, `/api/stream`, pushes upload progress, finished plans, reminders and review progress to the page.

**Reminders:** a background loop checks the calendar every minute against your reminder settings. It never sends the same reminder twice.

**Code layout:**

```
app/
  config.py      settings and folders
  db.py          the SQLite database: tables and helpers
  store.py       cases, chats, messages, documents, dates, notifications, settings
  files.py       safe file names, case folders, PDF splitting, data-room ZIP
  extract.py     text from PDF / Word / images, OCR (RapidOCR, then Claude for hard pages)
  llm.py         every AI call: Claude Code or API, streaming, JSON, images
  dates.py       Indian date formats, finding hearing dates and deadlines in text
  organizer.py   the organisation plan: make, revise, check, apply
  casesearch.py  searching a case's own documents (FTS5 + ChromaDB)
  laws.py        the Acts library, section look-up, IPC → BNS converter
  retrieval.py   gathers sources for a chat: case files + Constitution + Acts + judgments
  assistant.py   the lawyer-assistant prompt, streaming answers, citation checks
  reminders.py   the reminder service
  bus.py         live events to the page
  workflows.py   Skills, workflow plans and runs
  review.py      review tables, due diligence, risk ratings
  exports.py     Word, Excel, calendar (.ics), data-room ZIP
  routes/        the web API, one file per area
  (search.py, constitution.py, citations.py, cases.py, language.py and answer.py are kept from v1 and v2)
skills/          the workflow Skill files (plain English, editable)
data/laws/       the Acts library;  data/raw/ = the downloads it was built from
workspace/       YOUR cases and files (never shared; delete = start fresh)
```

## Build order

1. Law library: `build_laws.py`, `laws.py`, the IPC → BNS concordance.
2. Workspace core: database, cases, chats, uploads, reading files and OCR, organisation plans, filing the documents, calendar, reminders.
3. Chat: retrieval over case files and all the law, the new prompt, citations for Acts and documents.
4. The new three-column web page.
5. Workflows and Skills, drafting to Word.
6. Review table, due diligence, risk chart, data-room export.
7. Regional languages.
8. Tests, a live check in the browser with real sample files, README, screenshots.

## Honest limits

- **The Act texts** come from an open dataset (GSMS-B/indian-legal-mcp, MIT/Apache licence). That dataset was built from India Code, but it isn't the official PDF the way our Constitution is. Some texts still contain footnote digits. The official India Code website timed out from this PC; the plan is to cross-check against it later.
- **Court rules still missing** (v3.1, end of 10 Nov 2026):
  - Bombay (the site doesn't answer this network), Madras (refuses automated downloads, HTTP 429) and Punjab & Haryana (HTTP 403): their main rule books. Download them in a browser and drop them in the inbox.
  - Calcutta, Tripura and the Uttarakhand HC Rules: not posted online as files.
  - Kerala: the Civil Rules of Practice 1971 and Criminal Rules of Practice 1982 are not online officially (library, printed copy or RTI). The Kerala HC Rules 1971 and 8 other Kerala books are in.
  - Gujarat's Civil and Criminal Manuals are 370 MB and 150 MB scans, and were skipped.

  Library → Courts lists each one with its official link, and **Add a rule book** brings them in.
- **Scanned books** (the SC Rules 2013, Chhattisgarh 2007 and some rule books) are read with OCR, so a word can be wrong. The official PDF page is one click away, so check important rules there.
- **Gaps that remain:** State laws in general (rent, land revenue, police) and the CrPC First Schedule for old IPC cases. For these the AI uses its own knowledge, marked "(verify)".
- **Judgments:** 45 landmark notes. Full-text judgment search is not included.
- **OCR:** good for clear English print. Hindi and handwriting go to Claude, if you allow it.
- **Desktop reminders:** these only appear while the app is running. Email or WhatsApp reminders need the app to be online (v4).
- **One user:** the app is single-user and local. Logins for a team or firm come with going online.

## Roadmap after v3

- **v4, online:**
  - GitHub, hosting, logins for lawyers and firms, and email/WhatsApp reminders.
  - Usage limits, and saved answers to cut cost.
  - Live eCourts status through a licensed provider.
- **v5:**
  - Full-text judgment search.
  - Rules of the remaining High Courts once their sites can be reached (or added by users), and State laws (rent, land revenue, police Acts) from India Code's State collections.
  - Replace the first 101 Acts' open-dataset text with the official India Code text (the same pipeline as v3.1).
  - A Word add-in.

---

## History

- **Version 2 (8 Nov 2026).** All 12 Schedules (283 citable pieces). Court-ruling footnotes from the official text (e.g. NJAC struck down). 45 landmark-judgment notes. Hindi and Hinglish. 77 tests.
- **Version 1 (4 Nov 2026).**
  - The Constitution of India from the official PDF (*As on 10th September, 2026*): 506 entries, including omitted Articles.
  - RAG search (exact reference, topic hints, ChromaDB, BM25), answers that cite their sources, and green/red citation checks.
  - Simple and Detailed styles, Browse, and the "How I answered" panel.
  - Answers come from Claude Code (your subscription) or the API.
