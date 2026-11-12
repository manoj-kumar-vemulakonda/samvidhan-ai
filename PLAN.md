# Plan: Samvidhan AI, the AI workspace for Indian lawyers

*(Working name, easy to change in one place: `APP_NAME` in `app/config.py`.)*

## Version 3.8 (12 Nov 2026): 97 of the first 101 Acts official, titles whole, State law kept

The Motor Vehicles, Stamp and Water Acts are official now. Only the IPC, CrPC, Evidence Act and Income-tax Act, 2025 still come from the dataset, because India Code has no PDF of them. Checking the three found five more faults, and three of them were in the whole library.

- **What was wrong:**
  - **Water Act:** India Code prints ss. 3, 13 and 31 with the number of the section before ("2. Constitution of Central Boards" right after "2. Definitions"). After a Chapter heading the reader took the repeat for numbering that starts again, and the heading was lost.
  - **Stamp Act:**
    - Schedule I is printed "SCHEDULE 1", which wasn't read as a heading.
    - 260 pages of the States' own Schedules follow it, one of them a substituted "SCHEDULE I".
    - Karnataka's own "9A. Power of State Government to consolidate duties" and Orissa's "77A" are printed before the Act's own ss. 9A and 77A, and were taken for them.
  - **Motor Vehicles Act:**
    - Its 51 scanned pages are the First Schedule's pictures of road signs; the sections are text.
    - The Schedule heading has two note numbers: "1[2[THE FIRST SCHEDULE]]".
  - **State law dropped, everywhere:** a State's own section that the reader took for a heading ("47-A. Instruments under-valued" in the Stamp Act; Registration ss. 18A, 80B–80G for Tripura; HSA ss. 6B, 29A–29C …) was "left out", and its text went with it, about 43,000 characters in 14 sections.
  - **Central sections missing:** India Code's list of sections lacks Companies ss. 393A and 418A (Act 29 of 2020) and IBC ss. 67B and 67C (Act 6 of 2026), so they were dropped too.
  - **Titles cut short, everywhere:**
    - When a heading wrapped onto the next line before its ".—", the title was its first line and the heading stayed in the text: 647 sections (42 in the BNS, e.g. s. 196 "Promoting enmity … place of birth,").
    - Ten more had no title at all (TPA s. 33, SC/ST s. 14A …).
- **The fixes:**
  - `app/rulebook.py`:
    - A heading that wraps is the whole title (sections only; the full offline rebuild changed only titles, in 41 court-book Acts).
    - "SCHEDULE 1" and "1[2[THE FIRST SCHEDULE]]" are read as headings when officialising.
  - `scripts/officialise_acts.py`:
    - A heading printed with its neighbour's number gets its own, when its title is the next section's (in sequence only).
    - A State's own section stays in the text of the section before, among its State amendments, where India Code prints it.
    - A State's same-numbered section printed first goes back there.
    - The States' Schedules become "State amendments to Schedule I", and an omitted Schedule printed as a note (MV Act Second Schedule) becomes a Schedule of its own.
    - Blank titles are taken from the text.
    - A PDF whose scanned pages all come after its Schedules begin is read without OCR.
    - `UNLISTED` has the four central sections; `HOLD_BACK` is empty.
- **The official texts corrected the dataset:**
  - MV Act s. 192A is a penalty since the Jan Vishwas Act 2026; the dataset still had imprisonment.
  - MV Act s. 26 was replaced in 2019; the dataset kept the old sub-sections.
  - Stamp Act s. 9A is the 2019 stock-exchange duty section.
- **Checked:**
  - The 94 other official Acts read again change only as intended: titles completed, State text put back, the four new sections.
  - No Illustration lost, no footnote added.
  - Water and Stamp lost only footnotes and repeated headings. Stamp's Schedule I is the central one (39,000 characters, no State in it).
- **Library:** 268 books, 27,316 provisions. **Tests:** 248 pass (10 new).

## Version 3.7 (12 Nov 2026): 94 of the first 101 Acts official

Running `officialise_acts.py --all` over the 81 Acts still from the dataset showed seven more ways the reader lost or mixed up text. Two of them had damaged Acts made official earlier.

- **What was wrong:**
  - **Section numbers spelled two ways.** India Code writes an I or O after a hyphen ("45-I", so it isn't read as 1 or 0), and the dataset and the reader write "45I". RBI ss. 45-I to 45-IE were dropped, and Insurance would have had s. 52I and s. 52-I.
  - **The PDF's list of sections misread.** "22E.Award" (no space), "125A . Penalty", "49-I Conditions" (no dot) were not taken as entries, so real sections were dropped as "not in the Act's list". **Arbitration s. 29A (time limit for the award) and s. 43-I had been lost this way since v3.6.**
  - **Sections inserted, then substituted:** "1[2[10A." has two note numbers, and the reader expected one (NGT, ATA, RDBA, SEBI).
  - **Headings without a dot or with a misprinted number:** "60 Application of …" (Benami), "[86 Classification …" (JJ), "37ZA." for 378ZA (Companies), and CPC s. 95's heading. Each such section stayed at the end of the one before. CPC s. 95 was even in the library twice (official, inside s. 94, and from the dataset).
  - **The Bill's Statement of Objects and Reasons**, which India Code prints after the Act, was glued to the last section of about 50 Acts, BNS s. 358 among them.
  - **Schedules glued to the last section** when the heading carries a note number ("1[THE FIRST SCHEDULE", "1THE SECOND SCHEDULE", "1[SCHEDULE — I"): RBI s. 61 held the list of scheduled banks; UAPA s. 53 the banned organisations.
  - **A footnote taken for a section:** IBC's commencement note prints its dates in bold, so "2. 5th August, 2016 …" became s. 2.
- **The fixes:**
  - `app/rulebook.py`:
    - "1[2[10A." is a heading.
    - The Statement of Objects and Reasons ends the Act, in every book. The full offline rebuild changed 25 court-book Acts, and only by removing that text from their last provision.
    - Note-numbered Schedule headings and bold notes in note type are read when officialising only.
  - `scripts/officialise_acts.py`:
    - "45-I" = "45I"; the untidy list entries are read.
    - A section whose heading was missed is cut off the section before, where its title from the library is followed by ".—".
    - "[Substituted … for sections 15 to 17]" counts as gone (EPA s. 17).
    - Two new refusals: a missed section whose text is inside another (Water Act), and a section that swells (Stamp Act s. 79).
    - `UNLISTED` holds the real sections India Code's list leaves out (DPA s. 8A); `HOLD_BACK` holds Acts kept back by hand (Stamp).
- **Replaced: 74 more, 94 in all.** The 20 earlier ones were read again: Arbitration got ss. 29A and 43-I and its Fourth Schedule back; CPC s. 95 is official now; the Bill's reasons are gone from BNS, Consumer, DV, HMA, IT Act, SC/ST and SRA; the IT Act and SC/ST Schedules are split off.
- **Checked for every replaced Act:**
  - No Illustration lost, and no section points at the table of contents.
  - What left the shrunk sections (checked on a sample) was amendment notes, headings the dataset repeated, and State or J&K amendments, never law.
  - The sections that grew are new Schedules (IRDAI, WPA's species lists) or sections the dataset had only as a title (SMA s. 50).
- **Still from the dataset (7):**
  - IPC, CrPC, the Evidence Act and the Income-tax Act, 2025: no PDF of the Act on India Code.
  - Motor Vehicles: a scan.
  - Water: India Code misnumbers ss. 3, 13, 31.
  - Stamp: the States' Schedules.
- **Library:** 268 books, 27,306 provisions. **Tests:** 238 pass (10 new).

## Version 3.6 (11 Nov 2026): five more official Acts, and the Illustrations back

Teaching the reader the CPC and NI Act layouts showed that it lost or mixed up text in five ways. One of them had quietly damaged v3.5.

- **What was wrong:**
  - **Illustrations were dropped.** India Code prints Illustrations in small type, like footnotes, and the reader threw small type away.
    - In v3.5 the Indian Succession Act lost its Illustrations' text, and the BNS and BSA lost the word "Illustration(s)" above them (it's on so many pages that it looked like a page header).
    - The Contract Act would have lost all of them, and v3.5's checks would not have noticed.
  - **Footnotes in normal type stayed in the law.** Some pages print a note ("1. Subs. by Act 20 of 1929 …") as large as the law: TPA had 28 such notes inside its sections, Arbitration 18, Contract 12.
  - **Pages printed small were lost.** CPC ss. 52–60 are in 9 pt, so the reader dropped them.
  - **Headings that aren't headings, and headings it missed.**
    - "the Schedule I." at the start of a wrapped line (NI Act s. 2) was taken for a Schedule heading, and the rest of the Act became one "Schedule".
    - The Succession Act's "SCHEDULE I … VIII" were glued onto s. 392 (or s. 5).
  - **Page links into the table of contents.** For the Succession, NI and Contract Acts, the reading that won took its page numbers from the list of sections.
- **The fixes** (`app/rulebook.py`, switched on by `scripts/officialise_acts.py`):
  - Footnotes are found by their shape: the numbered block at the foot of a page, worded like notes ("Subs.", "Ins.", "Act 20 of 1929", "ibid.", "see … supra"). Their size doesn't matter. A numbered list in the law (a Schedule's items) is not mistaken for notes.
  - Small type right after an "Illustration(s)" label is kept, and the label is no longer a "page header".
  - On a page where most of the law is small, small type is kept.
  - "the Schedule" in lower case is not a heading; "SCHEDULE III" is.
- **Court-rule books keep the old reading.** Tried on them, the new one changed 34 books, some for the better, some not (see Honest limits). The full rebuild of every book is unchanged.
- **New checks before an Act is replaced:**
  - No more than 5% of the sections may lose their Illustrations or the label. Illustrations that the old text had in the wrong section count if they are in a labelled section now. This check would have stopped v3.5's loss.
  - Readings that start at the Act ("ACT NO. 26 OF 1881") win ties. A reading whose sections point at pages before the Act begins is refused.
  - `--redo` reads again an Act this script made official before.
- **Replaced: 5 more, 20 in all.** CPC (its sections; the Orders stay as they were), NI Act, Contract, Transfer of Property, Arbitration.
  - CPC s. 95 (its heading is printed word by word) and NI Act s. 81A (not in the PDF) keep the dataset text, marked "from dataset".
  - Arbitration gained 18 sections the dataset lacked (ss. 76–87 and others).
  - Many dataset "sections" were really several sections run together (TPA "s. 114" held ss. 114–137), so some texts are shorter now and correct.
  - The NI Act now has its Illustrations (s. 4's promissory notes, the maturity-date examples …), which the dataset never had.
- **Re-read: the 15 of v3.5.** Illustrations back (BNS 72 sections, BSA 57, Succession 128), 6 leftover footnotes gone, and the Succession Act's 8 Schedules split off. Every section of the 20 Acts now points at a page of the Act itself.
- **Footnotes are not in the text.** v3.5's notes said the official texts carry India Code's footnotes; they don't. The PDF page, one click away, has them.
- **Library:** still 268 books, now 27,157 provisions; 81 of the first 101 Acts are still from the dataset. **Tests:** 228 pass (10 new, including one per problem above). The full rebuild of all court-rule books gives exactly the same books as before.

## Version 3.5 (10 Nov 2026): the most-cited Acts in their official text

The model test showed that the open dataset behind the first 101 Acts can be wrong. Two of three models repeated its wrong amendment year. So the most-cited Acts were swapped for the official India Code text.

- **`scripts/officialise_acts.py`** downloads the official PDF and reads its sections in each way the reader knows. It keeps the reading that best matches what the library had. The Act is swapped only if all of these hold:
  - 98%+ of the sections are found;
  - 85%+ of the titles agree;
  - the texts are really there and about as long as before (a reading of only the table of contents is refused);
  - every new "section" is named in the PDF's own list of sections. India Code prints State amendments under the sections they change (e.g. Hindu Succession Act "6B", "29A"); those are left out.
- **Kept as before:**
  - schedules added earlier (CPC Orders, Limitation Articles, BNSS First Schedule);
  - repealed sections that the PDF lists as one range;
  - a section the PDF doesn't have (NI Act s. 81A), marked "from dataset".
- **Replaced (15):** BNS, BNSS, BSA, Limitation, Specific Relief, Hindu Marriage, Hindu Succession, Domestic Violence, Consumer Protection, POCSO, NDPS, SC/ST Atrocities, Indian Succession, RTI, IT Act. Each now has its "as on" date and page numbers. *(This line also said "India Code's own footnotes": wrong, the reader leaves footnotes out. And v3.6 found Illustrations missing and some page numbers pointing at the contents. See v3.6.)* NDPS gained ss. 68I and 68O, which the dataset lacked.
- **Not replaced:**
  - IPC, CrPC and Evidence Act: India Code lists them but no longer offers their PDFs. The search used to fall back to *another* document (a 2019 notification, an 1891 amending Act); a title check now refuses that.
  - CPC, NI Act, Contract, TPA and Arbitration: the readings fell short of the checks.
  - Motor Vehicles Act: a scan.
- **A mistake caught on the way:** the first run replaced 20 Acts. Six of them had matched only the PDF's table of contents, with every number and title but no text, because the first checks didn't look at the text. They were rolled back from Git, and the text checks were added before anything else was replaced.
- **Library:** still 268 books, now 27,130 provisions (new sections and schedules). **Tests:** 218 pass (5 new). The model test's law questions still score 100% on the new texts.

## Version 3.4 (10 Nov 2026): the model test, and the bugs it found

You wanted the best results at the lowest cost. To choose a model by measurement instead of guessing:

- **`scripts/eval_models.py`** runs the app's own code, once per model, on files and questions with known right answers. It scores two jobs and records the time, the cost and which model actually answered:
  - **sorting a pile of files:** grouping, document types and hearing dates;
  - **answering law questions:** the right provisions cited, the key facts stated, wrong claims avoided, and no made-up citations.
- **The test sets.** `evals/sample/` holds 3 made-up piles, 18 documents in all: Hindi, scan errors, dates written three ways, and two different cases between the same parties. It also has 6 questions. The lawyer's own real files go in `evals/private/`, which never reaches GitHub. See `evals/README.md`.
- **Bugs it found in the app** (all fixed, each with a regression test):
  - "section 318(4)" was read as sections 318 **and 4**;
  - BNS sections mapped sub-section by sub-section had **no IPC equivalent** at all (BNS 318 ↔ IPC 415/417/418/420);
  - "cheque returned unpaid" didn't bring up **NI Act s. 142**;
  - the open dataset's footnote to **NI Act s. 138** gave the wrong year (2015 instead of 2002). Haiku and Sonnet repeated it; only Opus doubted it. It's now corrected on every build (`CORRECTIONS` in `scripts/build_laws.py`).
- **Bugs in the test itself:** two "wrong statement" checks also matched correct answers, so they were narrowed. A lesson: read a sample of answers, not only the scores.
- **Results through Claude Code** (sample set, after the fixes):

  | Model | Sorting | Answers | Cost per pile / per answer (API prices) |
  |---|---|---|---|
  | no AI (rules) | 73% | – | free |
  | Haiku 5.5 | 100% | 100% | ₹0.42 / ₹0.07 |
  | Sonnet 5.5 | 100% | 100% | ₹2.42 / ₹0.90 |
  | Opus 5.5 | 100% | 100% | ₹5.91 / ₹2.62 |

  On clean, typed files and textbook questions even Haiku is enough, at a tenth of Sonnet's cost. But the sample is small and easy, and only Opus doubted the dataset's wrong year. So don't switch the everyday model on these results alone. Run the test on 20–50 real files first.
- **Tests:** 213 pass (13 new).

## Version 3.3 (10 Nov 2026): simple tasks, Deep answers, cheaper AI

You said workflows were confusing for you and for lawyers, and asked for the most cost-efficient AI with the best results.

- **"Workflows" are now "Tasks".**
  - Every new chat shows them as buttons, and **📋 Tasks** sits under the chat box and on Home.
  - Click a task, fill its small form and press **Start ▶**: the result is written straight away. That's one AI call instead of two, since the planning call is skipped.
  - **Show me the steps first** brings back the old plan → approve screen for lawyers who want it.
  - The recipe editor moved out of the left column to **Settings → Advanced → Task recipes**.
  - The stray blank "My workflow" recipe was removed.
- **Three model sizes, one per kind of job.**
  - Everyday work uses **Claude Sonnet 5.5** (the new API default, half Opus's price).
  - **🧠 Deep** in the chat uses **Claude Opus 5.5** at effort `high`, for hard questions.
  - Small jobs use **Claude Haiku 5.5**.
  - With Claude Code the same switch picks `opus`.
  - Settings: `CLAUDE_MODEL`, `DEEP_MODEL`, `DEEP_EFFORT`, `CLAUDE_CODE_DEEP_MODEL`.
- **Prompt caching (API).**
  - The system prompt and a case's details and file index are marked for caching, so the next question in the same case reads them back at $0.20 per million tokens instead of $2.
  - The cost under each answer now prices cached tokens correctly; before, it charged them at the full input price.
- **Checked live in headless Edge with Claude Code:**
  - **Home → Tasks** opened the picker, and a new chat showed 7 task buttons (the other 3 are for case chats).
  - **Rights after arrest → Start** ran at once, with no plan screen, and wrote its report with 6 green citations.
  - A **🧠 Deep** answer was labelled as such.
  - **Settings → Task recipes** listed all 10 recipes.
  - There were no JavaScript errors.
- **Tests:** 200 pass (8 new). Caching and the model sizes are tested with a fake API client; no API key was used.

## Version 3.2 (10 Nov 2026): the drop box on the home screen

You asked: a lawyer opens the site, drops a bunch of case files, and the app makes the plan and fills the calendar by itself, clean and simple.

- **📂 Drop your case files here** sits on the home screen. The lawyer drops files from one case or many, and doesn't make a case first.
- **One AI call** reads the whole pile and does three things:
  - sorts the files into cases, by CNR, case numbers and parties (an order citing both the FIR and the bail application ties them together);
  - recognises cases the lawyer already has, so no duplicates are made;
  - makes each case's filing plan: types, new names, folders, hearing dates and deadlines with their source sentence, and warnings.
- **Without AI,** the files are grouped by CNR, case numbers and "A v. B" titles, with keyword filing plans.
- **One card, one button.** The card shows each case. **Move to…** puts a file into another case instantly, with no AI call. Wrong dates can be unticked. **✓ Approve all** creates the cases, files everything and fills the calendar, using the same code as a case's own filing plan.
- **If filing fails for one case,** that case's plan waits in the case chat, ready to try again.
- **Checked live with Claude Code.** 6 mixed files (an FIR, a bail application, a court order, a cheque-bounce notice, a bank return memo and a divorce summons) were sorted into the right 3 cases in about 30 seconds. After Approve all, the 3 cases were created, the 6 documents filed and 2 hearings added to the calendar. Then the same steps were clicked in headless Edge, with no JavaScript errors.
- **Tests:** 192 pass (5 new).

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

- **The Act texts.** 97 of the first 101 Acts use the official India Code text (15 in v3.5, 5 in v3.6, 74 in v3.7, 3 in v3.8). Their amendment footnotes are not in the text; the PDF page, one click away, has them.
  - 4 still come from the open dataset (GSMS-B/indian-legal-mcp, MIT/Apache licence): IPC, CrPC, the Evidence Act and the Income-tax Act, 2025, because India Code has no PDF of the Act itself. That text was built from India Code but not from the official PDF, so it has footnote digits and State amendments in it.
  - Motor Vehicles Act: the First Schedule's road signs are pictures (scanned pages 122–172). The library has the Schedule's heading and notes; the PDF page shows the signs.
  - States' amendments stay in the text of the section they change, as India Code prints them. For the Stamp Act's Schedule I they are a provision of their own.
  - Single sections kept from the dataset, marked "from dataset", where the PDF lacks them: NI Act s. 81A (to be checked), Insurance ss. 65A and 114C, IBC ss. 74 and 76 (omitted), WPA s. 10 (omitted), and repealed ones in HSA and SRA.
  - Small leftovers: a few amendment notes inside Schedules (RBI's list of banks, Companies Schedule III) and two in LLP (ss. 2 and 67B).
  - Court-rule books still use the old reading of small type. The v3.6 reading (Illustrations kept, footnotes found by their shape) changed 34 of them when tried, some for the better. Each needs checking before it is switched on for them. Two would gain rules from the "the Schedule" fix (Allahabad 1952 +239, Uttarakhand e-Nyaya Shruti +25), but a forms book got worse. Reading "SCHEDULE I" headings would split the Schedules off the last section in 7 State court-fees and personal-law Acts (e.g. Court Fees Act s. 36 now holds 68,000 characters of Schedules), but the Kerala Court Fees Act came out empty, so it's left off for them too.
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
  - The last 4 of the first 101 Acts from official texts (97 done by v3.8): IPC, CrPC and the Evidence Act from the Gazette, and the Income-tax Act, 2025 once India Code has its PDF.
  - Check the court-rule books one by one with the v3.6 reading, and switch it on where it's better.
  - A Word add-in.

---

## History

- **Version 2 (8 Nov 2026).** All 12 Schedules (283 citable pieces). Court-ruling footnotes from the official text (e.g. NJAC struck down). 45 landmark-judgment notes. Hindi and Hinglish. 77 tests.
- **Version 1 (4 Nov 2026).**
  - The Constitution of India from the official PDF (*As on 10th September, 2026*): 506 entries, including omitted Articles.
  - RAG search (exact reference, topic hints, ChromaDB, BM25), answers that cite their sources, and green/red citation checks.
  - Simple and Detailed styles, Browse, and the "How I answered" panel.
  - Answers come from Claude Code (your subscription) or the API.
