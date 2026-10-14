"""Language used when seeding per-company starter rows."""

from __future__ import annotations


def seed_language_for_company(company) -> str:
    """Owner preference at company creation. Arabic when none is stored."""
    owner = getattr(company, "owner", None)
    raw = str(getattr(owner, "language", "") or "").strip().lower()
    if raw.startswith("en"):
        return "en"
    return "ar"


def seed_label(lang: str, en: str, ar: str) -> str:
    return ar if lang == "ar" else en


# Canonical English seed name -> Arabic label. The database keeps the English
# key. Clients show the Arabic label when the UI language is Arabic.
SEED_LABELS = {
    "WhatsApp": "واتساب",
    "Phone": "الهاتف",
    "Website": "الموقع الإلكتروني",
    "Social media": "وسائل التواصل",
    "New inquiry": "استفسار جديد",
    "Qualification": "تأهيل",
    "Viewing scheduled": "معاينة مجدولة",
    "Negotiation": "تفاوض",
    "Closed": "مغلق",
    "New patient": "مريض جديد",
    "Registered": "مسجّل",
    "With doctor": "عند الطبيب",
    "Follow-up": "متابعة",
    "Completed": "مكتمل",
    "New lead": "عميل جديد",
    "Discovery": "استكشاف",
    "Proposal": "عرض سعر",
    "On-site appointment": "موعد ميداني",
    "Quote sent": "تم إرسال العرض",
    "Order placed": "تم الطلب",
    "Fulfillment": "تجهيز الطلب",
    "Contacted": "تم التواصل",
    "Qualified": "مؤهل",
    "Follow up": "متابعة",
    "Closed won": "مغلق رابح",
    "Closed lost": "مغلق خاسر",
    "Visited": "تمت الزيارة",
    "Phone call": "مكالمة هاتفية",
    "Video call": "مكالمة فيديو",
    "Property viewing": "معاينة عقار",
    "Office consultation": "استشارة في المكتب",
    "Virtual tour": "جولة افتراضية",
    "First consultation": "استشارة أولى",
    "Follow-up visit": "زيارة متابعة",
    "Procedure / treatment": "إجراء أو علاج",
    "On-site service": "خدمة ميدانية",
    "Office appointment": "موعد في المكتب",
    "Remote consultation": "استشارة عن بعد",
    "Sales": "المبيعات",
    "In progress": "قيد التنفيذ",
    "On hold": "معلّقة",
    "Won": "رابحة",
    "Lost": "خاسرة",
    "Cancelled": "ملغاة",
    "Price": "السعر",
    "Competitor": "منافس",
    "No response": "لا يوجد رد",
    "Not a fit": "غير مناسب",
    "Other": "أخرى",
}


def display_seed_name(name: str | None, lang: str) -> str | None:
    if not name or lang != "ar":
        return name
    return SEED_LABELS.get(name, name)


def canonical_seed_name(incoming: str | None, stored: str | None) -> str | None:
    """Keep the English seed key when a client submits its Arabic label."""
    if incoming is None or stored is None:
        return incoming
    text = incoming.strip()
    if text == stored or SEED_LABELS.get(stored) == text:
        return stored
    return incoming


def ui_language_from_request(request, default: str = "ar") -> str:
    """First UI language from X-Language or Accept-Language. Ignores the DB default."""
    if request is None:
        return default
    for key in ("HTTP_X_LANGUAGE", "HTTP_ACCEPT_LANGUAGE"):
        raw = (request.META.get(key) or "").strip().lower()
        primary = raw.split(",")[0].split(";")[0].strip()
        if primary.startswith("en"):
            return "en"
        if primary.startswith("ar"):
            return "ar"
    return default if default in ("ar", "en") else "ar"
