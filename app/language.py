"""
Questions in Hindi (देवनागरी) and Hinglish (Hindi written in English letters).

The search (and its embedding model) only understands English. So for a Hindi or
Hinglish question we first make an English version, and search with that:
  1. Claude translates it - a fast model, about 3 seconds - when AI answers are on.
  2. Otherwise (or if that fails) a small built-in glossary turns the legal words it
     knows into English, e.g. "giraftar" / "गिरफ्तार" -> "arrest".
Claude still sees the ORIGINAL question and answers in the same language
(see rule 8 of the system prompt in app/answer.py).
"""

from __future__ import annotations

import asyncio
import re

from app import config

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Common Hindi words written in English letters. Two or more of them in a question = Hinglish.
# (Words that are also English, like "the", "me", "to", "par", are left out on purpose.)
HINGLISH_WORDS = set(
    """kya kyaa hai hain ho hota hoti hote tha thi kaise kyun kyon kaun kab kahan kitna kitni kitne
    mera meri mere mujhe mujhko hum hume humein humko aap apna apni apne tum tumhara uska uski unka unki
    ka ki ke ko se mein aur bhi agar toh tho liye lie chahiye milta milti milte wala wali wale koi kuch
    kisi sab sabhi jab yeh ye woh vo nahi nahin kar karna karne karta karti karte kiya sakta sakti
    sakte paas bina matlab bataiye batao samjhaiye kaunsa kaunsi""".split()
)

# Legal words -> English, for the fallback translation (Devanagari and Hinglish spellings)
GLOSSARY: dict[str, str] = {
    # the Constitution itself
    "संविधान": "constitution", "samvidhan": "constitution", "अनुच्छेद": "article", "anuchhed": "article",
    "अनुसूची": "schedule", "anusuchi": "schedule", "संशोधन": "amendment", "sanshodhan": "amendment",
    "मौलिक": "fundamental", "maulik": "fundamental", "मूल": "fundamental",
    "अधिकार": "rights", "अधिकारों": "rights", "adhikar": "rights", "haq": "rights", "हक": "rights",
    "कर्तव्य": "duties", "कर्तव्यों": "duties", "kartavya": "duties",
    "निदेशक": "directive", "तत्व": "principles", "प्रस्तावना": "preamble",
    # police, courts, crime
    "पुलिस": "police", "गिरफ्तार": "arrest", "गिरफ्तारी": "arrest", "giraftar": "arrest", "giraftari": "arrest",
    "girftar": "arrest", "हिरासत": "custody detention", "hirasat": "custody detention",
    "जमानत": "bail", "zamanat": "bail", "jamanat": "bail", "वारंट": "warrant",
    "बिना": "without", "bina": "without", "अदालत": "court", "adalat": "court", "न्यायालय": "court",
    "उच्चतम": "supreme", "सर्वोच्च": "supreme", "उच्च": "high", "न्यायाधीश": "judges", "जज": "judges",
    "वकील": "lawyer advocate", "vakil": "lawyer advocate", "मजिस्ट्रेट": "magistrate",
    "सजा": "punishment", "सज़ा": "punishment", "saza": "punishment", "saja": "punishment",
    "फांसी": "death penalty", "फाँसी": "death penalty", "phansi": "death penalty", "fansi": "death penalty",
    "मृत्युदंड": "death penalty", "चोरी": "theft", "chori": "theft", "हत्या": "murder", "hatya": "murder",
    "याचिका": "petition", "रिट": "writ", "यातना": "torture",
    # rights and freedoms
    "समानता": "equality", "samanta": "equality", "बराबरी": "equality", "स्वतंत्रता": "freedom",
    "आज़ादी": "freedom", "आजादी": "freedom", "azadi": "freedom", "aazadi": "freedom",
    "बोलने": "speech", "bolne": "speech", "अभिव्यक्ति": "expression", "निजता": "privacy",
    "nijta": "privacy", "गोपनीयता": "privacy", "जीवन": "life", "jeevan": "life", "zindagi": "life",
    "धर्म": "religion", "धार्मिक": "religious", "dharm": "religion", "dharma": "religion",
    "जाति": "caste", "jaati": "caste", "jati": "caste", "छुआछूत": "untouchability", "अस्पृश्यता": "untouchability",
    "भेदभाव": "discrimination", "bhedbhav": "discrimination", "आरक्षण": "reservation",
    "aarakshan": "reservation", "arakshan": "reservation", "शिक्षा": "education", "shiksha": "education",
    "padhai": "education", "बच्चों": "children", "बच्चे": "children", "bachche": "children", "bacche": "children",
    "महिला": "women", "महिलाओं": "women", "mahila": "women", "aurat": "women", "auraton": "women",
    "संपत्ति": "property", "सम्पत्ति": "property", "sampatti": "property", "जमीन": "land", "ज़मीन": "land",
    "zameen": "land", "jameen": "land", "भूमि": "land", "नौकरी": "employment jobs", "naukri": "employment jobs",
    "मजदूर": "labour workers", "मजदूरी": "wages labour", "श्रम": "labour", "बंधुआ": "bonded labour",
    "तस्करी": "trafficking", "विरोध": "protest", "प्रदर्शन": "protest", "इंटरनेट": "internet",
    "प्रेस": "press", "अखबार": "newspaper", "akhbar": "newspaper",
    "नागरिकता": "citizenship", "nagrikta": "citizenship", "नागरिक": "citizen", "nagrik": "citizen",
    "अल्पसंख्यक": "minority", "भाषा": "language", "भाषाओं": "languages", "bhasha": "language",
    "तलाक": "divorce talaq", "talaq": "divorce talaq", "शादी": "marriage", "shaadi": "marriage",
    "shadi": "marriage", "विवाह": "marriage", "vivah": "marriage", "दहेज": "dowry", "dahej": "dowry",
    "उम्र": "age", "umar": "age", "umra": "age", "आयु": "age", "न्यूनतम": "minimum",
    # government
    "सरकार": "government", "sarkar": "government", "राज्य": "state", "rajya": "state",
    "केंद्र": "union centre", "kendra": "union centre", "कानून": "law", "क़ानून": "law", "कानूनों": "laws",
    "kanoon": "law", "kanun": "law", "qanoon": "law", "संसद": "parliament", "sansad": "parliament",
    "लोकसभा": "lok sabha", "राज्यसभा": "rajya sabha", "विधानसभा": "legislative assembly",
    "राष्ट्रपति": "president", "rashtrapati": "president", "प्रधानमंत्री": "prime minister",
    "प्रधान": "prime", "मंत्री": "minister", "मुख्यमंत्री": "chief minister", "mukhyamantri": "chief minister",
    "राज्यपाल": "governor", "rajyapal": "governor", "चुनाव": "election", "chunav": "election",
    "मतदान": "voting", "matdan": "voting", "वोट": "vote", "आपातकाल": "emergency", "aapatkal": "emergency",
    "दलबदल": "defection", "dalbadal": "defection", "पंचायत": "panchayat", "नगरपालिका": "municipality",
    "टैक्स": "tax", "पुलिसकर्मी": "police",
    # ordinals, for "सातवीं अनुसूची" (Seventh Schedule)
    "पहली": "first", "दूसरी": "second", "तीसरी": "third", "चौथी": "fourth", "पांचवीं": "fifth",
    "पाँचवीं": "fifth", "छठी": "sixth", "सातवीं": "seventh", "आठवीं": "eighth", "नौवीं": "ninth",
    "दसवीं": "tenth", "ग्यारहवीं": "eleventh", "बारहवीं": "twelfth",
}

TRANSLATE_PROMPT = (
    "You turn questions about Indian law into English for a search engine. The question may be in Hindi, "
    "Hinglish (Hindi written in English letters) or another Indian language (Tamil, Telugu, Bengali, Marathi, "
    "Gujarati, Kannada, Malayalam, Punjabi, Odia, Urdu, Assamese). Reply with ONE short English sentence "
    "(at most 30 words) that asks exactly the same thing. Translate faithfully: keep legal terms, Article, "
    "section and Schedule numbers, Act names and names of people, and add nothing that isn't in the question "
    "(no 'under Indian law', no extra sub-questions). Reply with the English sentence only."
)

# Other Indian scripts (Unicode blocks). Devanagari is Hindi unless Marathi words show up.
SCRIPTS: dict[str, re.Pattern] = {
    "bengali": re.compile(r"[ঀ-৿]"),
    "punjabi": re.compile(r"[਀-੿]"),
    "gujarati": re.compile(r"[઀-૿]"),
    "odia": re.compile(r"[଀-୿]"),
    "tamil": re.compile(r"[஀-௿]"),
    "telugu": re.compile(r"[ఀ-౿]"),
    "kannada": re.compile(r"[ಀ-೿]"),
    "malayalam": re.compile(r"[ഀ-ൿ]"),
    "urdu": re.compile(r"[؀-ۿݐ-ݿ]"),
}
ASSAMESE_LETTERS = re.compile(r"[ৰৱ]")
MARATHI_WORDS = {"आहे", "नाही", "काय", "मला", "आणि", "करू", "शकतो", "शकते", "का?", "कसे", "माझ्या", "माझा", "माझी",
                 "पोलीस", "आहेत", "होईल", "कोणते", "मिळेल"}
# How each language is named in the answer instructions
LANGUAGE_NAMES = {
    "english": "English", "hindi": "Hindi (in Devanagari script)", "hinglish": "Hinglish (Hindi written in English letters)",
    "marathi": "Marathi", "bengali": "Bengali", "assamese": "Assamese", "punjabi": "Punjabi (Gurmukhi script)",
    "gujarati": "Gujarati", "odia": "Odia", "tamil": "Tamil", "telugu": "Telugu", "kannada": "Kannada",
    "malayalam": "Malayalam", "urdu": "Urdu",
}
NON_LATIN = re.compile(r"[؀-ۿऀ-ൿ]")


def detect_language(text: str) -> str:
    """'english', 'hindi', 'hinglish', or another Indian language found by its script ('tamil', 'bengali'...)."""
    counts = {name: len(pattern.findall(text)) for name, pattern in SCRIPTS.items()}
    best = max(counts, key=counts.get)
    devanagari = len(DEVANAGARI.findall(text))
    if counts[best] >= 3 and counts[best] > devanagari:
        if best == "bengali" and ASSAMESE_LETTERS.search(text):
            return "assamese"
        return best
    if devanagari >= 3:
        words = set(re.findall(r"[ऀ-ॿ]+\??", text))
        return "marathi" if len(words & MARATHI_WORDS) >= 2 else "hindi"
    words = re.findall(r"[a-z]+", text.lower())
    if len({w for w in words if w in HINGLISH_WORDS}) >= 2:
        return "hinglish"
    return "english"


def glossary_translate(text: str) -> str:
    """
    A rough English version using only the glossary: the legal words it knows, plus
    numbers and English words already in the question. Good enough for the search.
    """
    out: list[str] = []
    for token in re.findall(r"[ऀ-ॿ]+|[A-Za-z]+|\d+[A-Za-z]{0,3}", text):
        lower = token.lower()
        if lower in GLOSSARY:
            out.append(GLOSSARY[lower])
        elif DEVANAGARI.search(token) or lower in HINGLISH_WORDS:
            continue  # a Hindi word we don't know (or a small word like "kya") - skip it
        else:
            out.append(token)  # English words ("police", "warrant") and numbers ("21")
    return " ".join(out)


async def ai_translate(question: str) -> str:
    """Ask Claude (a fast model) for an English version. Returns '' if that isn't possible."""
    engine = config.answer_engine()
    if engine == "claude_code":
        from app import claude_code  # imported here to avoid a circular import

        reply = await asyncio.to_thread(claude_code.ask_once, question, TRANSLATE_PROMPT, "haiku", 45)
    elif engine == "api":
        from app.answer import get_client

        message = await get_client().messages.create(
            model=config.FAST_MODEL,
            max_tokens=200,
            system=TRANSLATE_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        reply = "".join(getattr(block, "text", "") for block in message.content)
    else:
        return ""
    lines = [line.strip().strip('"') for line in reply.strip().splitlines() if line.strip()]
    english = lines[0] if lines else ""
    return "" if NON_LATIN.search(english) else english[:400]


async def english_query(question: str) -> tuple[str, str, str]:
    """
    The text to search with: (query, language, how it was translated).
    how = "none" (already English), "claude" or "glossary".
    """
    language = detect_language(question)
    if language == "english":
        return question, language, "none"
    try:
        english = await ai_translate(question)
    except Exception:  # no AI, no internet, usage limit... the glossary still works
        english = ""
    if english:
        return english, language, "claude"
    return glossary_translate(question) or question, language, "glossary"
