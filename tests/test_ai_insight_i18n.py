from integrations.ai_insight_i18n import extract_bilingual_pair, pick_insight_text


def test_extract_bilingual_pair_from_legacy_key():
    en, ar = extract_bilingual_pair(
        {"summary": "English only", "summary_ar": "عربي"},
        "summary",
        max_len=500,
    )
    assert en == "English only"
    assert ar == "عربي"


def test_pick_insight_text_arabic_prefers_ar():
    text = pick_insight_text(
        language="ar",
        text_en="Hello",
        text_ar="مرحبا",
        legacy="Legacy",
    )
    assert text == "مرحبا"


def test_pick_insight_text_english_prefers_en():
    text = pick_insight_text(
        language="en",
        text_en="Hello",
        text_ar="مرحبا",
    )
    assert text == "Hello"
