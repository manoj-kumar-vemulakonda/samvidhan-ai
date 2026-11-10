"""
Build the Acts library: data/raw/acts-gsmsb/*.json  ->  data/laws/

The raw files come from the open dataset GSMS-B/indian-legal-mcp (MIT / Apache-2.0
licence; the text itself was taken from India Code). They use six different layouts, so
this script turns every Act into ONE simple format:

    data/laws/acts/BNS.json   {"code", "cite", "name", "year", "category", "status", ...,
                               "sections": [{"number", "title", "chapter", "chapter_title", "text"}]}
    data/laws/index.json      the list of Acts (no section text) - the library's table of contents
    data/laws/concordance.json   old -> new section mappings (IPC -> BNS, CrPC -> BNSS, IEA -> BSA)
                                 and back again

Run it again whenever the raw files change:
    .venv\\Scripts\\python.exe scripts\\build_laws.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
RAW = PROJECT / "data" / "raw" / "acts-gsmsb"
OUT = PROJECT / "data" / "laws"

SOURCE = ("Open dataset GSMS-B/indian-legal-mcp (github.com/GSMS-B/indian-legal-mcp, MIT/Apache-2.0), "
          "compiled from India Code (indiacode.nic.in)")

MAPPING_FILES = {
    "ipc_to_bns": ("IPC", "BNS"), "bns_to_ipc": ("BNS", "IPC"),
    "crpc_to_bnss": ("CRPC", "BNSS"), "bnss_to_crpc": ("BNSS", "CRPC"),
    "iea_to_bsa": ("IEA", "BSA"), "bsa_to_iea": ("BSA", "IEA"),
}

# Raw files without an act_code inside them
CODE_BY_FILE = {
    "bharatiya_nyaya_sanhita_2023": "BNS",
    "bharatiya_nagarik_suraksha_sanhita_2023": "BNSS",
    "bharatiya_sakshya_adhiniyam_2023": "BSA",
    "code_of_civil_procedure_1908": "CPC",
    "code_of_criminal_procedure_1973": "CRPC",
    "indian_evidence_act_1872": "IEA",
    "indian_penal_code_1860": "IPC",
    "motor_vehicles_act_1988": "MVA",
    "negotiable_instruments_act_1881": "NIA",
}
# Some dataset codes are easy to confuse ("CA", "EA", "LA"...): use clearer ones
RENAME_CODE = {
    "CA": "COMPANIES", "EA": "ELECTRICITY", "LA": "LIMITATION", "ICA": "CONTRACT", "IPA": "PARTNERSHIP",
    "ISA": "SUCCESSION", "CPA": "CONSUMER", "ACA": "ARBITRATION", "IT_ACT": "ITACT", "ITA": "INCOMETAX",
    "MHA": "MENTALHEALTH", "COMMERCIAL_COURTS": "COMMCOURTS", "CONTEMPT_COURTS": "CONTEMPT",
}

CRIMINAL_NOTE_NEW = "In force from 1 July 2024."
CRIMINAL_NOTE_OLD = ("Repealed on 1 July 2024 and replaced by the {new}. It still governs offences committed "
                     "before that date (and proceedings pending then).")

# code: (citation name, full name, category, extra aliases, status, replaced_by, replaces)
ACTS: dict[str, tuple] = {
    "BNS": ("BNS", "Bharatiya Nyaya Sanhita, 2023", "Criminal law",
            ["bns", "bharatiya nyaya sanhita", "nyaya sanhita", "भारतीय न्याय संहिता", "बीएनएस"], "in_force", None, "IPC"),
    "BNSS": ("BNSS", "Bharatiya Nagarik Suraksha Sanhita, 2023", "Criminal law",
             ["bnss", "bharatiya nagarik suraksha sanhita", "nagarik suraksha sanhita", "भारतीय नागरिक सुरक्षा संहिता", "बीएनएसएस"],
             "in_force", None, "CRPC"),
    "BSA": ("BSA", "Bharatiya Sakshya Adhiniyam, 2023", "Criminal law",
            ["bsa", "bharatiya sakshya adhiniyam", "sakshya adhiniyam", "भारतीय साक्ष्य अधिनियम"], "in_force", None, "IEA"),
    "IPC": ("IPC", "Indian Penal Code, 1860", "Criminal law",
            ["ipc", "i p c", "indian penal code", "penal code", "आईपीसी", "भारतीय दंड संहिता"], "repealed", "BNS", None),
    "CRPC": ("CrPC", "Code of Criminal Procedure, 1973", "Criminal law",
             ["crpc", "cr p c", "code of criminal procedure", "criminal procedure code", "सीआरपीसी", "दंड प्रक्रिया संहिता"],
             "repealed", "BNSS", None),
    "IEA": ("Evidence Act", "Indian Evidence Act, 1872", "Criminal law",
            ["iea", "evidence act", "indian evidence act", "साक्ष्य अधिनियम"], "repealed", "BSA", None),
    "CPC": ("CPC", "Code of Civil Procedure, 1908", "Civil procedure and property",
            ["cpc", "c p c", "code of civil procedure", "civil procedure code", "सिविल प्रक्रिया संहिता"], "in_force", None, None),
    "NIA": ("NI Act", "Negotiable Instruments Act, 1881", "Contracts, commerce and banking",
            ["ni act", "n i act", "negotiable instruments act", "negotiable instrument act", "cheque bounce act"], "in_force", None, None),
    "MVA": ("MV Act", "Motor Vehicles Act, 1988", "Regulatory",
            ["mv act", "m v act", "mva", "motor vehicles act", "motor vehicle act"], "in_force", None, None),
    "LIMITATION": ("Limitation Act", "Limitation Act, 1963", "Civil procedure and property",
                   ["limitation act", "law of limitation"], "in_force", None, None),
    "CONTRACT": ("Contract Act", "Indian Contract Act, 1872", "Contracts, commerce and banking",
                 ["contract act", "indian contract act", "ica"], "in_force", None, None),
    "TPA": ("TPA", "Transfer of Property Act, 1882", "Civil procedure and property",
            ["tpa", "t p act", "transfer of property act"], "in_force", None, None),
    "SRA": ("Specific Relief Act", "Specific Relief Act, 1963", "Civil procedure and property",
            ["specific relief act", "sra"], "in_force", None, None),
    "REGISTRATION": ("Registration Act", "Registration Act, 1908", "Civil procedure and property",
                     ["registration act"], "in_force", None, None),
    "EASEMENTS": ("Easements Act", "Indian Easements Act, 1882", "Civil procedure and property",
                  ["easements act", "easement act"], "in_force", None, None),
    "STAMP": ("Stamp Act", "Indian Stamp Act, 1899", "Tax", ["stamp act", "indian stamp act"], "in_force", None, None),
    "RFCTLARR": ("Land Acquisition Act, 2013", None, "Civil procedure and property",
                 ["land acquisition act", "rfctlarr", "larr act"], "in_force", None, None),
    "COMPANIES": ("Companies Act", "Companies Act, 2013", "Companies, finance and securities",
                  ["companies act", "company law"], "in_force", None, None),
    "IBC": ("IBC", "Insolvency and Bankruptcy Code, 2016", "Companies, finance and securities",
            ["ibc", "insolvency and bankruptcy code", "insolvency code", "bankruptcy code"], "in_force", None, None),
    "ARBITRATION": ("Arbitration Act", "Arbitration and Conciliation Act, 1996", "Contracts, commerce and banking",
                    ["arbitration act", "arbitration and conciliation act"], "in_force", None, None),
    "CONSUMER": ("Consumer Protection Act", "Consumer Protection Act, 2019", "Contracts, commerce and banking",
                 ["consumer protection act", "consumer act", "cpa"], "in_force", None, None),
    "PARTNERSHIP": ("Partnership Act", "Indian Partnership Act, 1932", "Contracts, commerce and banking",
                    ["partnership act", "indian partnership act"], "in_force", None, None),
    "SGA": ("Sale of Goods Act", "Sale of Goods Act, 1930", "Contracts, commerce and banking",
            ["sale of goods act"], "in_force", None, None),
    "COMMCOURTS": ("Commercial Courts Act", "Commercial Courts Act, 2015", "Civil procedure and property",
                   ["commercial courts act"], "in_force", None, None),
    "HMA": ("HMA", "Hindu Marriage Act, 1955", "Family law",
            ["hma", "hindu marriage act", "हिंदू विवाह अधिनियम"], "in_force", None, None),
    "HSA": ("Hindu Succession Act", "Hindu Succession Act, 1956", "Family law",
            ["hindu succession act", "hsa"], "in_force", None, None),
    "HAMA": ("HAMA", "Hindu Adoptions and Maintenance Act, 1956", "Family law",
             ["hama", "hindu adoptions and maintenance act", "hindu adoption and maintenance act"], "in_force", None, None),
    "SMA": ("Special Marriage Act", "Special Marriage Act, 1954", "Family law", ["special marriage act"], "in_force", None, None),
    "SUCCESSION": ("Succession Act", "Indian Succession Act, 1925", "Family law",
                   ["indian succession act", "succession act"], "in_force", None, None),
    "FCA": ("Family Courts Act", "Family Courts Act, 1984", "Family law", ["family courts act", "family court act"], "in_force", None, None),
    "GWA": ("Guardians and Wards Act", "Guardians and Wards Act, 1890", "Family law",
            ["guardians and wards act", "guardian and wards act"], "in_force", None, None),
    "DV": ("DV Act", "Protection of Women from Domestic Violence Act, 2005", "Family law",
           ["dv act", "domestic violence act", "pwdva", "protection of women from domestic violence act", "घरेलू हिंसा"], "in_force", None, None),
    "DPA": ("Dowry Prohibition Act", "Dowry Prohibition Act, 1961", "Family law", ["dowry prohibition act", "dowry act"], "in_force", None, None),
    "PCMA": ("Child Marriage Act", "Prohibition of Child Marriage Act, 2006", "Family law",
             ["prohibition of child marriage act", "child marriage act"], "in_force", None, None),
    "MWPSC": ("Senior Citizens Act", "Maintenance and Welfare of Parents and Senior Citizens Act, 2007", "Family law",
              ["senior citizens act", "maintenance and welfare of parents and senior citizens act"], "in_force", None, None),
    "MWPRDA": ("Muslim Women (Divorce) Act", "Muslim Women (Protection of Rights on Divorce) Act, 1986", "Family law",
               ["muslim women divorce act", "muslim women protection of rights on divorce act"], "in_force", None, None),
    "MWPRMA": ("Triple Talaq Act", "Muslim Women (Protection of Rights on Marriage) Act, 2019", "Family law",
               ["triple talaq act", "muslim women protection of rights on marriage act"], "in_force", None, None),
    "POCSO": ("POCSO Act", "Protection of Children from Sexual Offences Act, 2012", "Criminal law",
              ["pocso", "pocso act", "protection of children from sexual offences act"], "in_force", None, None),
    "NDPS": ("NDPS Act", "Narcotic Drugs and Psychotropic Substances Act, 1985", "Criminal law",
             ["ndps", "ndps act", "narcotic drugs and psychotropic substances act"], "in_force", None, None),
    "JJ": ("JJ Act", "Juvenile Justice (Care and Protection of Children) Act, 2015", "Criminal law",
           ["jj act", "juvenile justice act"], "in_force", None, None),
    "SCST": ("SC/ST Act", "Scheduled Castes and the Scheduled Tribes (Prevention of Atrocities) Act, 1989", "Criminal law",
             ["sc st act", "sc/st act", "atrocities act", "prevention of atrocities act"], "in_force", None, None),
    "PCA": ("PC Act", "Prevention of Corruption Act, 1988", "Criminal law",
            ["pc act", "prevention of corruption act", "anti corruption act"], "in_force", None, None),
    "PMLA": ("PMLA", "Prevention of Money-laundering Act, 2002", "Criminal law",
             ["pmla", "prevention of money laundering act", "money laundering act"], "in_force", None, None),
    "UAPA": ("UAPA", "Unlawful Activities (Prevention) Act, 1967", "Criminal law",
             ["uapa", "unlawful activities prevention act"], "in_force", None, None),
    "ARMS": ("Arms Act", "Arms Act, 1959", "Criminal law", ["arms act"], "in_force", None, None),
    "ITACT": ("IT Act", "Information Technology Act, 2000", "Cyber, data and IP",
              ["it act", "information technology act", "cyber law"], "in_force", None, None),
    "DPDPA": ("DPDP Act", "Digital Personal Data Protection Act, 2023", "Cyber, data and IP",
              ["dpdp act", "dpdpa", "data protection act", "digital personal data protection act"], "in_force", None, None),
    "RTI": ("RTI Act", "Right to Information Act, 2005", "Constitutional and public law",
            ["rti", "rti act", "right to information act", "सूचना का अधिकार"], "in_force", None, None),
    "INCOMETAX": ("Income-tax Act, 2025", "Income-tax Act, 2025", "Tax",
                  ["income tax act", "income-tax act", "income tax act 2025"], "in_force", None, None),
    "CGST": ("CGST Act", "Central Goods and Services Tax Act, 2017", "Tax", ["cgst", "cgst act", "central gst act"], "in_force", None, None),
    "IGST": ("IGST Act", "Integrated Goods and Services Tax Act, 2017", "Tax", ["igst", "igst act"], "in_force", None, None),
    "ELECTRICITY": ("Electricity Act", "Electricity Act, 2003", "Regulatory", ["electricity act"], "in_force", None, None),
    "MENTALHEALTH": ("Mental Healthcare Act", "Mental Healthcare Act, 2017", "Regulatory",
                     ["mental healthcare act", "mental health act"], "in_force", None, None),
    "CONTEMPT": ("Contempt of Courts Act", "Contempt of Courts Act, 1971", "Constitutional and public law",
                 ["contempt of courts act", "contempt act"], "in_force", None, None),
    "ADVOCATES": ("Advocates Act", "Advocates Act, 1961", "Constitutional and public law", ["advocates act"], "in_force", None, None),
    "LSAA": ("Legal Services Authorities Act", "Legal Services Authorities Act, 1987", "Constitutional and public law",
             ["legal services authorities act", "lok adalat"], "in_force", None, None),
    "RERA": ("RERA", "Real Estate (Regulation and Development) Act, 2016", "Civil procedure and property",
             ["rera", "real estate act", "real estate regulation and development act"], "in_force", None, None),
    "SARFAESI": ("SARFAESI Act", None, "Companies, finance and securities", ["sarfaesi", "sarfaesi act"], "in_force", None, None),
    "RDBA": ("RDB Act", "Recovery of Debts and Bankruptcy Act, 1993", "Companies, finance and securities",
             ["rdb act", "rdba", "recovery of debts and bankruptcy act", "drt act"], "in_force", None, None),
    "POSH": ("POSH Act", None, "Labour and employment",
             ["posh", "posh act", "sexual harassment of women at workplace act"], "in_force", None, None),
    "IRC": ("Industrial Relations Code", "Industrial Relations Code, 2020", "Labour and employment",
            ["industrial relations code"], "in_force", None, None),
    "COW": ("Code on Wages", "Code on Wages, 2019", "Labour and employment", ["code on wages", "wages code"], "in_force", None, None),
    "CSS": ("Social Security Code", "Code on Social Security, 2020", "Labour and employment",
            ["code on social security", "social security code"], "in_force", None, None),
    "OSH": ("OSH Code", "Occupational Safety, Health and Working Conditions Code, 2020", "Labour and employment",
            ["osh code", "occupational safety health and working conditions code"], "in_force", None, None),
}

CATEGORY_BY_CODE_DEFAULT = "Other central Acts"
CATEGORY_GUESS = [
    (r"tax|customs|stamp|goods and services", "Tax"),
    (r"pollution|environment|wild ?life|forest|van |green tribunal", "Environment"),
    (r"bank|reserve bank|securit|sebi|insurance|chit|payment|foreign|benami|llp|limited liability|msme|micro, small|competition", "Companies, finance and securities"),
    (r"copyright|patent|trade ?marks?|designs|geographical|telecom|aadhaar", "Cyber, data and IP"),
    (r"labour|wages|social security|industrial", "Labour and employment"),
    (r"human rights|civil rights|representation of the people|disabilit|children to free|administrative tribunal", "Constitutional and public law"),
    (r"drugs|food safety|electricity|motor", "Regulatory"),
]


def tidy(text: str) -> str:
    """Light clean-up of the raw text: context labels, chapter headings, extra spaces."""
    text = (text or "").replace("\r", "")
    text = re.sub(r"^\[Context:[^\]]*\]\s*", "", text)
    text = re.sub(r"^\s*CHAPTER\s+[IVXLC\d]+[A-Z]?\s*\n[^\n]{0,120}\n", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chapter_parts(raw: str) -> tuple[str, str]:
    """'CHAPTER VI OF OFFENCES AFFECTING THE HUMAN BODY' -> ('VI', 'Of offences affecting the human body')."""
    raw = (raw or "").strip()
    match = re.match(r"(?i)chapter\s+([IVXLC]+[A-Z]?|\d+[A-Z]?)\s*[-.:]?\s*(.*)", raw)
    if match:
        return match.group(1).upper(), nice_title(match.group(2))
    return "", nice_title(raw)


def nice_title(title: str) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip(" .-—–")
    if title.isupper() and len(title) > 3:
        title = title.capitalize()
    return title


def section_number(raw, text: str) -> str:
    number = str(raw).strip().upper().replace(" ", "")
    # "Section 498A" stored as 498: take the full number from the text if it starts with it
    match = re.match(r"\s*(\d{1,4}[A-Z]{0,3})\s*[.\-]", text or "")
    if match and match.group(1).startswith(number) and match.group(1) != number:
        return match.group(1)
    return number


def read_raw(path: Path) -> tuple[dict, list[dict]]:
    """Any of the raw layouts -> (act info found in the file, list of sections)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    info: dict = {}
    if isinstance(data, dict):
        info = {"code": data.get("act_code"), "name": data.get("act_name"), "year": data.get("year")}
        items = data.get("sections") or []
    else:
        items = data
    sections = []
    for item in items:
        text = item.get("text") or item.get("section_desc") or item.get("description") or ""
        number = item.get("section_number", item.get("section", item.get("Section", "")))
        title = item.get("section_title") or item.get("title") or ""
        if "chapter_number" in item:
            chapter, chapter_title = str(item.get("chapter_number") or ""), nice_title(item.get("chapter_title"))
        elif isinstance(item.get("chapter"), str):
            chapter, chapter_title = chapter_parts(item["chapter"])
        else:
            chapter = str(item.get("chapter") or "")
            chapter_title = nice_title(item.get("chapter_title") or "")
        text = tidy(text)
        sections.append({
            "number": section_number(number, text),
            "title": nice_title(title),
            "chapter": chapter,
            "chapter_title": chapter_title,
            "text": text,
        })
    return info, sections


def merge_duplicates(sections: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    ordered = []
    for section in sections:
        key = section["number"]
        if key in seen:
            if section["text"] and section["text"] not in seen[key]["text"]:
                seen[key]["text"] += "\n\n" + section["text"]
            continue
        seen[key] = section
        ordered.append(section)
    return ordered


def status_of(section: dict) -> str:
    text = (section["title"] + " " + section["text"][:80]).lower()
    return "omitted" if re.search(r"\[(repealed|omitted)\.?\]|^\s*(repealed|omitted)\.?\s*$", text) else "in_force"


def build_acts() -> list[dict]:
    acts = []
    for path in sorted(RAW.glob("*.json")):
        stem = path.stem
        if stem in MAPPING_FILES:
            continue
        info, sections = read_raw(path)
        code = CODE_BY_FILE.get(stem) or (info.get("code") or stem.upper())
        code = RENAME_CODE.get(code, code)
        year_match = re.search(r"(\d{4})$", stem)
        year = int(info.get("year") or (year_match.group(1) if year_match else 0) or 0)
        meta = ACTS.get(code)
        raw_name = (info.get("name") or "").strip()
        if raw_name and not re.search(r"\d{4}$", raw_name) and year:
            raw_name = f"{raw_name}, {year}"
        if meta:
            cite, name, category, aliases, status, replaced_by, replaces = meta
            name = name or raw_name or stem.replace("_", " ").title()
        else:
            name = raw_name or stem.replace("_", " ").title()
            cite = re.sub(r",?\s*\d{4}$", "", re.sub(r"^The\s+", "", name)).strip()
            category = next((c for pattern, c in CATEGORY_GUESS if re.search(pattern, name, re.I)), CATEGORY_BY_CODE_DEFAULT)
            aliases, status, replaced_by, replaces = [], "in_force", None, None
        short = re.sub(r",?\s*\d{4}$", "", re.sub(r"^The\s+", "", name)).strip()
        alias_set = []
        for alias in [cite, short, name, *aliases]:
            key = alias_key(alias)
            if key and key not in alias_set:
                alias_set.append(key)
        note = ""
        if code in ("BNS", "BNSS", "BSA"):
            note = CRIMINAL_NOTE_NEW
        elif replaced_by:
            new_name = ACTS[replaced_by][1]
            note = CRIMINAL_NOTE_OLD.format(new=new_name)
        sections = merge_duplicates(sections)
        for section in sections:
            section["status"] = status_of(section)
        acts.append({
            "code": code, "cite": cite, "name": name, "short": short, "year": year, "category": category,
            "status": status, "replaced_by": replaced_by, "replaces": replaces, "note": note,
            "aliases": alias_set, "source": SOURCE, "sections": sections,
        })
    return acts


def alias_key(text: str) -> str:
    """How act names are compared: lower case, no punctuation, no year, no leading 'the'."""
    text = (text or "").lower()
    text = re.sub(r",?\s*\b(1[89]\d\d|20\d\d)\b", " ", text)
    text = re.sub(r"[().,/'\-]", " ", text)
    text = re.sub(r"^\s*the\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def build_concordance() -> dict:
    """{"IPC>BNS": {"302": [{"section": "103", "sub": "103", "status": "Change", "title": ...}]}, ...}"""
    out: dict[str, dict] = {}
    for stem, (old, new) in MAPPING_FILES.items():
        path = RAW / f"{stem}.json"
        if not path.exists():
            continue
        table: dict[str, list] = {}
        for item in json.loads(path.read_text(encoding="utf-8")):
            key = str(item.get("lookup_key") or (item.get("source") or {}).get("section") or "").upper().replace(" ", "")
            target = item.get("target") or {}
            source = item.get("source") or {}
            entry = {
                "status": item.get("status") or "",
                "section": str(target.get("section") or "").upper() or None,
                "sub": target.get("sub_section") or None,
                "title": target.get("title") or "",
                "from_title": source.get("title") or "",
                "statement": item.get("mapping_statement") or "",
            }
            table.setdefault(key, [])
            if entry not in table[key]:
                table[key].append(entry)
        out[f"{old}>{new}"] = table
    # Well-known cases the dataset marks as "Deleted" without a pointer
    sedition = out.get("IPC>BNS", {}).get("124A")
    if sedition:
        for entry in sedition:
            entry["note"] = ("Sedition was not carried over. The closest new offence is BNS s. 152 "
                             "(acts endangering sovereignty, unity and integrity of India).")
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not RAW.exists():
        print(f"Raw files not found in {RAW}")
        return 1
    acts = build_acts()
    (OUT / "acts").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "acts").glob("*.json"):
        old.unlink()
    index = []
    total = 0
    for act in acts:
        (OUT / "acts" / f"{act['code']}.json").write_text(json.dumps(act, ensure_ascii=False, indent=1), encoding="utf-8")
        entry = {k: v for k, v in act.items() if k != "sections"}
        entry["sections"] = len(act["sections"])
        index.append(entry)
        total += len(act["sections"])
    index.sort(key=lambda a: (a["category"], a["name"]))
    (OUT / "index.json").write_text(json.dumps({"source": SOURCE, "acts": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    concordance = build_concordance()
    (OUT / "concordance.json").write_text(json.dumps(concordance, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(acts)} Acts, {total} sections -> {OUT}")
    print("Mappings:", {k: len(v) for k, v in concordance.items()})
    codes = [a["code"] for a in acts]
    duplicates = {c for c in codes if codes.count(c) > 1}
    if duplicates:
        print("WARNING duplicate codes:", duplicates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
