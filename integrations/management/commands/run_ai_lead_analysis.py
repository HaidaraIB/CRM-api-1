"""
Run AI lead analysis for all companies with OpenAI integration enabled.
"""
from django.core.management.base import BaseCommand

from companies.models import Company
from integrations.models import OpenAISettings
from integrations.services.ai_lead_analysis import run_company_analysis


class Command(BaseCommand):
    help = "Analyze leads with OpenAI for tenants that have AI integration enabled."

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            help="Run for a single company id only",
        )

    def handle(self, *args, **options):
        company_id = options.get("company_id")
        if company_id:
            companies = Company.objects.filter(id=company_id)
        else:
            company_ids = OpenAISettings.objects.filter(
                is_enabled=True,
                auto_analyze_enabled=True,
            ).values_list("company_id", flat=True)
            companies = Company.objects.filter(id__in=company_ids)

        total_created = 0
        for company in companies:
            result = run_company_analysis(company)
            if result.get("created"):
                total_created += result["created"]
            self.stdout.write(f"Company {company.id}: {result}")

        self.stdout.write(self.style.SUCCESS(f"Done. Insights created: {total_created}"))
