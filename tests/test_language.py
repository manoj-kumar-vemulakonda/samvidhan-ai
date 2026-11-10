"""Tests for Hindi / Hinglish questions (no internet or Claude needed: the glossary path is used)."""

import asyncio

import pytest

from app import config, language
from app.language import detect_language, english_query, glossary_translate

HINDI = "क्या पुलिस मुझे बिना वारंट के गिरफ्तार कर सकती है?"
HINGLISH = "Kya police mujhe bina warrant ke giraftar kar sakti hai?"


def test_detect_language():
    assert detect_language(HINDI) == "hindi"
    assert detect_language(HINGLISH) == "hinglish"
    assert detect_language("Kya mujhe privacy ka adhikar hai?") == "hinglish"
    assert detect_language("Can the police arrest me without a warrant?") == "english"
    assert detect_language("What is Article 21?") == "english"


def test_glossary_translation():
    assert glossary_translate(HINDI) == "police without warrant arrest"
    assert glossary_translate(HINGLISH) == "police without warrant arrest"
    assert glossary_translate("सातवीं अनुसूची में पुलिस") == "seventh schedule police"
    assert "article 21" in glossary_translate("अनुच्छेद 21 क्या है?")


@pytest.mark.parametrize("question, expected", [(HINDI, "22"), (HINGLISH, "22"), ("Kya mujhe privacy ka adhikar hai?", "21")])
def test_hindi_questions_find_the_right_article(engine, monkeypatch, question, expected):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")  # no AI: the glossary does the translating
    query, lang, how = asyncio.run(english_query(question))
    assert lang in ("hindi", "hinglish") and how == "glossary"
    assert expected in [h.article.id for h in engine.search(query)[:3]]


@pytest.mark.parametrize("text, language", [
    ("போலீஸ் என்னை வாரண்ட் இல்லாமல் கைது செய்ய முடியுமா?", "tamil"),
    ("పోలీసులు వారెంట్ లేకుండా అరెస్ట్ చేయవచ్చా?", "telugu"),
    ("পুলিশ কি ওয়ারেন্ট ছাড়া গ্রেফতার করতে পারে?", "bengali"),
    ("पोलीस मला वॉरंटशिवाय अटक करू शकतात का? माझा काय अधिकार आहे", "marathi"),
    ("ਕੀ ਪੁਲਿਸ ਬਿਨਾਂ ਵਾਰੰਟ ਗ੍ਰਿਫਤਾਰ ਕਰ ਸਕਦੀ ਹੈ?", "punjabi"),
    ("શું પોલીસ વોરંટ વિના ધરપકડ કરી શકે?", "gujarati"),
    ("ಪೊಲೀಸರು ವಾರಂಟ್ ಇಲ್ಲದೆ ಬಂಧಿಸಬಹುದೇ?", "kannada"),
    ("പോലീസിന് വാറന്റില്ലാതെ അറസ്റ്റ് ചെയ്യാമോ?", "malayalam"),
    ("କଣ ପୋଲିସ ୱାରେଣ୍ଟ ବିନା ଗିରଫ କରିପାରିବ?", "odia"),
    ("کیا پولیس وارنٹ کے بغیر گرفتار کر سکتی ہے؟", "urdu"),
    ("क्या पुलिस बिना वारंट गिरफ्तार कर सकती है?", "hindi"),
])
def test_indian_languages_are_detected(text, language):
    assert detect_language(text) == language


def test_claude_translation_is_used_when_available(monkeypatch):
    async def fake_translate(question):
        return "Can the police arrest me without a warrant?"

    monkeypatch.setattr(language, "ai_translate", fake_translate)
    assert asyncio.run(english_query(HINDI)) == ("Can the police arrest me without a warrant?", "hindi", "claude")


def test_failed_translation_falls_back_to_the_glossary(monkeypatch):
    async def broken(question):
        raise RuntimeError("usage limit")

    monkeypatch.setattr(language, "ai_translate", broken)
    assert asyncio.run(english_query(HINDI)) == ("police without warrant arrest", "hindi", "glossary")


def test_english_questions_are_not_translated():
    question = "Can the police arrest me without a warrant?"
    assert asyncio.run(english_query(question)) == (question, "english", "none")
