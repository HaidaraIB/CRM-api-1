# Backfill LeadWhatsAppMessage.phone_number_id for rows created before outbound/inbound
# persistence was wired (field added in 0037; writers landed the same day).

from django.db import migrations
from django.db.models import Q


def _backfill(apps, schema_editor):
    LeadWhatsAppMessage = apps.get_model('integrations', 'LeadWhatsAppMessage')
    WhatsAppAccount = apps.get_model('integrations', 'WhatsAppAccount')
    IntegrationAccount = apps.get_model('integrations', 'IntegrationAccount')

    # Prefer metadata.phone_number_id from the connected WhatsApp integration, else
    # the best connected WhatsAppAccount row for that company.
    company_pid = {}

    for acc in IntegrationAccount.objects.filter(platform='whatsapp', status='connected'):
        meta = acc.metadata if isinstance(acc.metadata, dict) else {}
        pid = str(meta.get('phone_number_id') or '').strip()
        if pid and acc.company_id not in company_pid:
            company_pid[acc.company_id] = pid

    for wa in (
        WhatsAppAccount.objects.filter(status='connected')
        .exclude(phone_number_id__startswith='seed_')
        .order_by('-updated_at')
    ):
        if wa.company_id not in company_pid and wa.phone_number_id:
            company_pid[wa.company_id] = str(wa.phone_number_id)

    # Fallback: any WhatsAppAccount (including disconnected) if still missing.
    for wa in WhatsAppAccount.objects.exclude(phone_number_id__startswith='seed_').order_by(
        '-updated_at'
    ):
        if wa.company_id not in company_pid and wa.phone_number_id:
            company_pid[wa.company_id] = str(wa.phone_number_id)

    for company_id, pid in company_pid.items():
        LeadWhatsAppMessage.objects.filter(
            client__company_id=company_id,
        ).filter(Q(phone_number_id__isnull=True) | Q(phone_number_id='')).update(
            phone_number_id=pid
        )


def _noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0037_leadwhatsappmessage_phone_number_id'),
    ]

    operations = [
        migrations.RunPython(_backfill, _noop),
    ]
