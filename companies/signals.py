from django.db.models.signals import post_save
from django.dispatch import receiver

from companies.models import Company


@receiver(post_save, sender=Company)
def seed_settings_on_company_create(sender, instance, created, **kwargs):
    if not created:
        return
    from settings.company_defaults import seed_company_settings

    seed_company_settings(instance)
