"""
One-time cleanup for WhatsApp numbers shared by more than one owner (created before
the "one number = one owner" rule). See docs/WHATSAPP_NUMBER_OWNERSHIP.md.

Who keeps a shared number:
  * the platform, if it is the platform number;
  * otherwise whoever connected it first;
  * override per number with --keep PHONE_NUMBER_ID=crm|inbox.

Everyone else loses the number: their connection is disconnected (messages are kept),
the integration shows the reason, and the company owner gets an in-app/push notice
telling them to connect a different number.

  python manage.py resolve_whatsapp_number_conflicts            # dry run (default)
  python manage.py resolve_whatsapp_number_conflicts --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from integrations.models import IntegrationLog
from integrations.services.whatsapp_number_ownership import (
    CONFLICT_MESSAGES,
    OWNER_CRM,
    OWNER_INBOX,
    OWNER_PLATFORM,
    NumberOwner,
    conflict_key_for,
    find_number_conflicts,
    pick_keeper,
)


class Command(BaseCommand):
    help = "Resolve WhatsApp numbers owned by more than one place (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Actually disconnect and notify.')
        parser.add_argument(
            '--keep',
            action='append',
            default=[],
            metavar='PHONE_NUMBER_ID=crm|inbox',
            help='Override who keeps a specific number.',
        )
        parser.add_argument('--no-notify', action='store_true', help='Do not notify company owners.')

    def handle(self, *args, **options):
        overrides = {}
        for item in options['keep']:
            pid, _, kind = item.partition('=')
            if not pid or kind not in (OWNER_CRM, OWNER_INBOX):
                raise CommandError(f'--keep expects PHONE_NUMBER_ID=crm|inbox, got {item!r}')
            overrides[pid.strip()] = kind

        conflicts = find_number_conflicts()
        if not conflicts:
            self.stdout.write(self.style.SUCCESS('No shared WhatsApp numbers. Nothing to do.'))
            return

        apply = options['apply']
        self.stdout.write(
            f"{'APPLYING' if apply else 'DRY RUN'}: {len(conflicts)} shared number(s)\n"
        )
        for pid, owners in conflicts:
            keeper = self._keeper(pid, owners, overrides)
            if keeper is None:
                continue
            losers = [o for o in owners if o is not keeper]
            self.stdout.write(
                f'  phone_number_id={pid}: keep {keeper.describe()}; '
                f"disconnect {', '.join(o.describe() for o in losers)}"
            )
            if apply:
                with transaction.atomic():
                    for loser in losers:
                        key = conflict_key_for(loser, keeper)
                        self._disconnect(loser, key)
                        if not options['no_notify']:
                            transaction.on_commit(
                                lambda loser=loser, key=key: _notify_owner(loser, key)
                            )
        if not apply:
            self.stdout.write('\nNothing changed. Re-run with --apply to resolve.')
        else:
            self.stdout.write(self.style.SUCCESS('\nDone.'))

    def _keeper(self, pid, owners, overrides):
        kind = overrides.get(pid)
        if not kind:
            return pick_keeper(owners)
        if any(o.kind == OWNER_PLATFORM for o in owners):
            self.stdout.write(
                self.style.ERROR(
                    f'  phone_number_id={pid}: is the platform number; a company cannot keep it. '
                    'Change Platform WhatsApp settings to a different number first, then re-run.'
                )
            )
            return None
        match = [o for o in owners if o.kind == kind]
        if not match:
            raise CommandError(f'--keep {pid}={kind}: no {kind} owner for this number')
        return match[0]

    def _disconnect(self, loser: NumberOwner, key: str):
        row = loser.row
        integration = row.integration_account
        row.set_access_token(None)
        row.status = 'disconnected'
        row.integration_account = None
        row.save(update_fields=['access_token', 'status', 'integration_account', 'updated_at'])
        if integration is None:
            return
        still_connected = type(row).objects.filter(
            integration_account=integration, status='connected'
        ).exists()
        if not still_connected:
            integration.status = 'error'
            integration.error_message = CONFLICT_MESSAGES[key]
            meta = dict(integration.metadata or {})
            meta['number_conflict_key'] = key
            integration.metadata = meta
            integration.save(update_fields=['status', 'error_message', 'metadata', 'updated_at'])
        IntegrationLog.objects.create(
            account=integration,
            action='whatsapp_number_conflict_resolved',
            status='error',
            message='WhatsApp number disconnected: it belongs to another owner',
            error_details=key,
            response_data={'phone_number_id': row.phone_number_id, 'kind': loser.kind},
        )


def _notify_owner(loser: NumberOwner, key: str) -> None:
    from accounts.utils import get_email_language_for_user
    from notifications.models import NotificationType
    from notifications.services import NotificationService

    company = loser.row.company
    owner = getattr(company, 'owner', None)
    if owner is None:
        return
    language = get_email_language_for_user(owner, request=None, default='ar')
    number = loser.row.display_phone_number or ''
    where_ar = 'صندوق وارد واتساب' if loser.kind == OWNER_INBOX else 'واتساب CRM'
    where_en = 'WhatsApp inbox' if loser.kind == OWNER_INBOX else 'CRM WhatsApp'
    if language == 'ar':
        title = f'تم فصل رقم {where_ar}'
        body = (
            f'تم فصل الرقم {number} لأنه مستخدم في مكان آخر — يمكن ربط كل رقم واتساب في مكان واحد فقط. '
            'اربط رقماً مختلفاً من التكاملات ← واتساب. محادثاتك السابقة محفوظة.'
        )
    else:
        title = f'Your {where_en} number was disconnected'
        body = (
            f'The number {number} is used elsewhere — each WhatsApp number can be connected in one place only. '
            'Connect a different number from Integrations → WhatsApp. Your previous conversations are kept.'
        )
    NotificationService.send_notification(
        user=owner,
        notification_type=NotificationType.INTEGRATION_TOKEN_EXPIRED,
        title=title,
        body=' '.join(body.split()),
        data={
            'platform': 'whatsapp_inbox' if loser.kind == OWNER_INBOX else 'whatsapp',
            'reason': 'whatsapp_number_conflict',
            'error_key': key,
        },
        language=language,
        skip_settings_check=True,
    )
