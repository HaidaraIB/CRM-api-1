"""Company creation seeds per-tenant settings (channels, stages, statuses, etc.)."""

import pytest

from companies.models import Company, Specialization
from settings.lead_status_automation import VISITED_AUTOMATION_KEY
from settings.models import CallMethod, Channel, LeadStage, LeadStatus, VisitType


@pytest.mark.django_db
def test_seed_on_company_create_real_estate(owner_user):
    owner_user.company = None
    owner_user.save(update_fields=["company"])
    company = Company.objects.create(
        name="RE Co",
        domain="re-seed.example.com",
        owner=owner_user,
        specialization=Specialization.REAL_ESTATE.value,
    )
    owner_user.company = company
    owner_user.save(update_fields=["company"])

    assert Channel.objects.filter(company=company).count() >= 4
    assert Channel.objects.filter(company=company, is_default=True).count() == 1

    assert LeadStage.objects.filter(company=company).count() >= 5
    assert LeadStage.objects.filter(company=company, is_default=True).count() == 1

    assert LeadStatus.objects.filter(company=company).count() >= 7
    assert (
        LeadStatus.objects.filter(company=company, is_default=True, is_hidden=False).count()
        == 1
    )
    visited = LeadStatus.objects.filter(
        company=company, automation_key=VISITED_AUTOMATION_KEY
    ).first()
    assert visited is not None
    assert visited.name == "Visited"

    assert CallMethod.objects.filter(company=company).count() >= 3
    assert CallMethod.objects.filter(company=company, is_default=True).count() == 1

    assert VisitType.objects.filter(company=company).count() == 3
    assert VisitType.objects.filter(company=company, is_default=True).count() == 1


@pytest.mark.django_db
def test_seed_on_company_create_services(other_owner_user):
    other_owner_user.company = None
    other_owner_user.save(update_fields=["company"])
    company = Company.objects.create(
        name="Svc Co",
        domain="svc-seed.example.com",
        owner=other_owner_user,
        specialization=Specialization.SERVICES.value,
    )
    other_owner_user.company = company
    other_owner_user.save(update_fields=["company"])

    assert VisitType.objects.filter(company=company).count() == 3
    visited = LeadStatus.objects.filter(
        company=company, automation_key=VISITED_AUTOMATION_KEY
    ).first()
    assert visited is not None


@pytest.mark.django_db
def test_seed_on_company_create_products(other_owner_user):
    other_owner_user.company = None
    other_owner_user.save(update_fields=["company"])
    company = Company.objects.create(
        name="Prod Co",
        domain="prod-seed.example.com",
        owner=other_owner_user,
        specialization=Specialization.PRODUCTS.value,
    )
    other_owner_user.company = company
    other_owner_user.save(update_fields=["company"])

    assert VisitType.objects.filter(company=company).count() == 0
    assert (
        LeadStatus.objects.filter(
            company=company, automation_key=VISITED_AUTOMATION_KEY
        ).exists()
        is False
    )
    assert Channel.objects.filter(company=company, is_default=True).exists()
    assert LeadStatus.objects.filter(company=company, is_default=True).exists()


@pytest.mark.django_db
def test_seed_idempotent(owner_user):
    owner_user.company = None
    owner_user.save(update_fields=["company"])
    company = Company.objects.create(
        name="Idem Co",
        domain="idem-seed.example.com",
        owner=owner_user,
        specialization=Specialization.REAL_ESTATE.value,
    )
    n1 = Channel.objects.filter(company=company).count()
    from settings.company_defaults import seed_company_settings

    seed_company_settings(company)
    n2 = Channel.objects.filter(company=company).count()
    assert n1 == n2


@pytest.mark.django_db
def test_seed_stores_english_keys_for_either_owner_language(owner_user, other_owner_user):
    from settings.models import DealPipeline, DealStage

    owner_user.language = "en"
    owner_user.save(update_fields=["language"])
    english = Company.objects.create(
        name="EN Co",
        domain="en-seed.example.com",
        owner=owner_user,
        specialization=Specialization.REAL_ESTATE.value,
    )
    assert LeadStatus.objects.get(company=english, is_default=True).name == "New lead"
    assert Channel.objects.filter(company=english, name="Phone").exists()
    pipeline = DealPipeline.objects.get(company=english, is_default=True)
    assert pipeline.name == "Sales"
    assert DealStage.objects.get(pipeline=pipeline, system_key="on_hold").name == "On hold"

    other_owner_user.language = "ar"
    other_owner_user.save(update_fields=["language"])
    arabic = Company.objects.create(
        name="AR Co",
        domain="ar-seed.example.com",
        owner=other_owner_user,
        specialization=Specialization.PRODUCTS.value,
    )
    assert LeadStatus.objects.get(company=arabic, is_default=True).name == "New lead"
    assert Channel.objects.filter(company=arabic, name="Phone").exists()
    assert LeadStage.objects.filter(company=arabic, name="Quote sent").exists()
    ar_pipeline = DealPipeline.objects.get(company=arabic, is_default=True)
    assert ar_pipeline.name == "Sales"
    assert DealStage.objects.get(pipeline=ar_pipeline, system_key="won").name == "Won"


@pytest.mark.django_db
def test_localize_untouched_english_seed_names(owner_user):
    from settings.company_defaults import localize_existing_seed_names
    from settings.models import DealLostReason, DealPipeline, DealStage, LeadStage

    owner_user.language = "en"
    owner_user.save(update_fields=["language"])
    company = Company.objects.create(
        name="Backfill Co",
        domain="backfill-seed.example.com",
        owner=owner_user,
        specialization=Specialization.REAL_ESTATE.value,
    )
    LeadStatus.objects.create(
        company=company, name="VIP", category="active", color="#111111"
    )
    LeadStage.objects.filter(company=company, name="New inquiry").update(name="Custom stage")

    owner_user.language = "ar"
    owner_user.save(update_fields=["language"])
    assert localize_existing_seed_names(company) > 0

    assert Channel.objects.filter(company=company, name="الهاتف").exists()
    assert not Channel.objects.filter(company=company, name="Phone").exists()
    assert LeadStatus.objects.filter(company=company, name="عميل جديد", is_default=True).exists()
    assert LeadStatus.objects.filter(company=company, name="تمت الزيارة").exists()
    assert LeadStatus.objects.filter(company=company, name="VIP").exists()
    assert LeadStage.objects.filter(company=company, name="Custom stage").exists()
    assert not LeadStage.objects.filter(company=company, name="استفسار جديد").exists()
    assert LeadStage.objects.filter(company=company, name="تأهيل").exists()

    pipeline = DealPipeline.objects.get(company=company, is_default=True)
    assert pipeline.name == "المبيعات"
    assert DealStage.objects.get(pipeline=pipeline, system_key="on_hold").name == "معلّقة"
    assert DealLostReason.objects.filter(company=company, name="السعر").exists()
    assert not DealLostReason.objects.filter(company=company, name="Price").exists()

    assert localize_existing_seed_names(company) == 0

    from settings.company_defaults import restore_canonical_seed_names

    assert restore_canonical_seed_names(company) > 0
    assert Channel.objects.filter(company=company, name="Phone").exists()
    assert not Channel.objects.filter(company=company, name="الهاتف").exists()
    assert LeadStatus.objects.filter(company=company, name="New lead", is_default=True).exists()
    assert LeadStatus.objects.filter(company=company, name="VIP").exists()
    assert LeadStage.objects.filter(company=company, name="Custom stage").exists()
    assert DealPipeline.objects.get(company=company, is_default=True).name == "Sales"
    assert restore_canonical_seed_names(company) == 0


@pytest.mark.django_db
def test_localize_skips_english_owner_and_taken_names(owner_user):
    from settings.company_defaults import localize_existing_seed_names

    owner_user.language = "en"
    owner_user.save(update_fields=["language"])
    company = Company.objects.create(
        name="Keep EN Co",
        domain="keep-en-seed.example.com",
        owner=owner_user,
        specialization=Specialization.SERVICES.value,
    )
    assert localize_existing_seed_names(company) == 0
    assert Channel.objects.filter(company=company, name="Phone").exists()

    owner_user.language = "ar"
    owner_user.save(update_fields=["language"])
    Channel.objects.create(
        company=company, name="الهاتف", type="Phone", priority="high", is_active=True
    )
    localize_existing_seed_names(company)
    assert Channel.objects.filter(company=company, name="Phone").exists()
    assert LeadStatus.objects.filter(company=company, name="عميل جديد").exists()
