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
