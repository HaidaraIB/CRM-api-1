"""
Seed fake WhatsApp chats for local UI review (no Meta connection required).

Run:
  .\\.venv\\Scripts\\python.exe manage.py seed_whatsapp_chats --company-id 123 --user-id 142
  .\\.venv\\Scripts\\python.exe manage.py seed_whatsapp_chats --company-id 123 --user-id 142 --replace
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from companies.models import Company
from crm.models import Client, ClientPhoneNumber
from integrations.models import (
    IntegrationAccount,
    IntegrationPlatform,
    LeadWhatsAppMessage,
    MessageSendSource,
    MessageTemplate,
    WhatsAppAccount,
)
from settings.models import Channel, LeadStatus

SEED_MARKER = "[WA-SEED]"
SEED_TEMPLATE_PREFIX = "wa_seed_"
FAKE_PHONE_NUMBER_ID = "seed_wa_phone_number_id_ui_review"


class Command(BaseCommand):
    help = (
        "Seed WhatsApp leads, messages, approved templates, and a fake connected "
        "account so /chats can be reviewed without Meta."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument(
            "--replace",
            action="store_true",
            help=f"Delete prior {SEED_MARKER} seed data for this company before inserting.",
        )

    def handle(self, *args, **options):
        company_id = options["company_id"]
        user_id = options["user_id"]
        replace = options["replace"]

        company = Company.objects.filter(id=company_id).first()
        if not company:
            raise CommandError(f"Company with id={company_id} was not found.")

        User = get_user_model()
        user = User.objects.filter(id=user_id).first()
        if not user:
            raise CommandError(f"User with id={user_id} was not found.")
        if user.company_id != company.id:
            raise CommandError(
                f"User {user_id} is linked to company {user.company_id}, expected {company.id}."
            )

        with transaction.atomic():
            if replace:
                deleted = self._replace_seed(company)
                self.stdout.write(f"Replaced prior seed: {deleted}")

            integ, wa = self._ensure_connected_account(company, user)
            templates = self._seed_templates(company)
            # Mute Client post_save signals (FCM assign notifications) during seed.
            with self._mute_model_signals():
                threads = self._seed_threads(company, user)

        self.stdout.write(self.style.SUCCESS("WhatsApp chats seeded successfully."))
        self.stdout.write(
            f"company_id={company.id}, user_id={user.id}, "
            f"threads={len(threads)}, templates={len(templates)}, "
            f"integration_account_id={integ.id}, whatsapp_account_id={wa.id}"
        )
        self.stdout.write(
            "Open /chats as this company user. Real Meta Send will still fail "
            "(fake token); history and composer UI states are ready for review."
        )

    def _mute_model_signals(self):
        """Temporarily clear model signals so seed does not push FCM notifications."""
        from contextlib import contextmanager

        from django.db.models import signals as model_signals

        @contextmanager
        def _ctx():
            targets = (
                model_signals.pre_save,
                model_signals.post_save,
                model_signals.pre_delete,
                model_signals.post_delete,
                model_signals.m2m_changed,
            )
            stashed = []
            for sig in targets:
                stashed.append((sig, list(sig.receivers)))
                sig.receivers = []
                if hasattr(sig, "sender_receivers_cache"):
                    sig.sender_receivers_cache.clear()
            try:
                yield
            finally:
                for sig, receivers in stashed:
                    sig.receivers = receivers
                    if hasattr(sig, "sender_receivers_cache"):
                        sig.sender_receivers_cache.clear()

        return _ctx()

    def _replace_seed(self, company: Company) -> dict:
        seeded_clients = Client.objects.filter(company=company, notes__contains=SEED_MARKER)
        client_ids = list(seeded_clients.values_list("id", flat=True))
        msg_deleted, _ = LeadWhatsAppMessage.objects.filter(client_id__in=client_ids).delete()
        clients_deleted, _ = seeded_clients.delete()
        tmpl_deleted, _ = MessageTemplate.objects.filter(
            company=company, name__startswith=SEED_TEMPLATE_PREFIX
        ).delete()
        # Keep IntegrationAccount; recreate WhatsAppAccount link in ensure step.
        wa_deleted, _ = WhatsAppAccount.objects.filter(
            company=company, phone_number_id=FAKE_PHONE_NUMBER_ID
        ).delete()
        return {
            "messages": msg_deleted,
            "clients": clients_deleted,
            "templates": tmpl_deleted,
            "whatsapp_accounts": wa_deleted,
        }

    def _ensure_connected_account(self, company: Company, user) -> tuple[IntegrationAccount, WhatsAppAccount]:
        integ, _ = IntegrationAccount.objects.update_or_create(
            company=company,
            platform=IntegrationPlatform.WHATSAPP,
            external_account_id="seed_wa_external_account",
            defaults={
                "name": f"{SEED_MARKER} Demo WhatsApp",
                "status": "connected",
                "is_active": True,
                "external_account_name": "Seed Demo Number",
                "metadata": {
                    "display_name_status": "APPROVED",
                    "display_name_approved": True,
                    "seed": True,
                },
                "created_by": user,
                "error_message": "",
            },
        )
        integ.set_access_token("seed-fake-access-token-not-for-meta")
        integ.save(update_fields=["access_token", "updated_at"])

        wa, created = WhatsAppAccount.objects.get_or_create(
            phone_number_id=FAKE_PHONE_NUMBER_ID,
            defaults={
                "company": company,
                "waba_id": "seed_waba_id",
                "business_id": "seed_business_id",
                "display_phone_number": "+9647700000000",
                "status": "connected",
                "integration_account": integ,
            },
        )
        if not created:
            wa.company = company
            wa.status = "connected"
            wa.integration_account = integ
            wa.display_phone_number = wa.display_phone_number or "+9647700000000"
            wa.waba_id = wa.waba_id or "seed_waba_id"
            wa.save(
                update_fields=[
                    "company",
                    "status",
                    "integration_account",
                    "display_phone_number",
                    "waba_id",
                    "updated_at",
                ]
            )
        wa.set_access_token("seed-fake-access-token-not-for-meta")
        wa.save(update_fields=["access_token", "updated_at"])
        return integ, wa

    def _seed_templates(self, company: Company) -> list[MessageTemplate]:
        specs = [
            {
                "name": f"{SEED_TEMPLATE_PREFIX}welcome_ar",
                "content": "مرحباً {{1}}، شكراً لتواصلك معنا. كيف يمكننا مساعدتك؟",
                "category": MessageTemplate.CATEGORY_UTILITY,
                "language": "ar",
                "header_text": "مرحباً بك",
                "footer": "فريق المبيعات",
            },
            {
                "name": f"{SEED_TEMPLATE_PREFIX}appointment_ar",
                "content": "تذكير: موعدك يوم {{1}} الساعة {{2}}. ننتظرك.",
                "category": MessageTemplate.CATEGORY_UTILITY,
                "language": "ar",
                "header_text": "تذكير بالموعد",
                "footer": "",
            },
            {
                "name": f"{SEED_TEMPLATE_PREFIX}followup_en",
                "content": "Hi {{1}}, just following up on your inquiry. Any questions?",
                "category": MessageTemplate.CATEGORY_MARKETING,
                "language": "en_US",
                "header_text": "Follow up",
                "footer": "CRM Team",
            },
        ]
        created: list[MessageTemplate] = []
        for spec in specs:
            tmpl, _ = MessageTemplate.objects.update_or_create(
                company=company,
                name=spec["name"],
                defaults={
                    "channel_type": MessageTemplate.CHANNEL_WHATSAPP_API,
                    "content": spec["content"],
                    "category": spec["category"],
                    "language": spec["language"],
                    "header_type": "text",
                    "header_text": spec["header_text"],
                    "footer": spec["footer"],
                    "buttons": [],
                    "meta_template_id": f"seed_meta_{spec['name']}",
                    "meta_status": "APPROVED",
                },
            )
            created.append(tmpl)
        return created

    def _seed_threads(self, company: Company, user) -> list[Client]:
        now = timezone.now()
        status = LeadStatus.objects.filter(company=company, is_active=True).first()
        channel = Channel.objects.filter(company=company, is_active=True).first()

        # Conversations covering Arabic names, phone-only titles, company names,
        # open/closed 24h session, delivery statuses, and placeholder bodies.
        # Media threads are the newest so the list shows Photo / Video /
        # Voice message / Document without a real WhatsApp send.
        thread_specs = [
            {
                "key": "media_video",
                "name": "فيديو الموقع",
                "phone": "+9647701001001",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "شوف الفيديو", -30, None),
                    {"direction": "inbound", "body": "", "minutes_ago": -1, "kind": "video", "filename": "site-tour.mp4"},
                ],
            },
            {
                "key": "media_document",
                "name": "مستندات العقد",
                "phone": "+9647701001002",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "أرسلت العرض", -25, None),
                    {
                        "direction": "outbound",
                        "body": "",
                        "minutes_ago": -2,
                        "delivery": "read",
                        "kind": "document",
                        "filename": "عرض-السعر.pdf",
                    },
                ],
            },
            {
                "key": "media_voice",
                "name": "رسالة صوتية",
                "phone": "+9647701001003",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("outbound", "تقدر تسجل ملاحظة؟", -20, "read"),
                    {"direction": "inbound", "body": "", "minutes_ago": -3, "kind": "audio", "filename": "voice-note.wav"},
                ],
            },
            {
                "key": "media_photo",
                "name": "صور العقار",
                "phone": "+9647701001004",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "هاي صورة الواجهة", -18, None),
                    ("inbound", "[image message]", -4, None),
                ],
            },
            {
                "key": "media_doc_caption",
                "name": "عرض السعر",
                "phone": "+9647701001005",
                "lead_company_name": "Oil and Gas",
                "session": "open",
                "messages": [
                    {
                        "direction": "outbound",
                        "body": "العقد المرفق",
                        "minutes_ago": -5,
                        "delivery": "delivered",
                        "kind": "document",
                        "filename": "contract.pdf",
                    },
                ],
            },
            {
                "key": "hassan",
                "name": "حسن السعدي",
                "phone": "+9647812113063",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "مرحباً", -120, None),
                    ("outbound", "السلام عليكم", -100, "read"),
                    ("inbound", "كيف الحال استاذ؟", -80, None),
                    ("outbound", "اهلا وسهلا", -60, "read"),
                    ("inbound", "تمام", -40, None),
                    ("outbound", "[button message]", -25, "delivered"),
                    ("inbound", "كيف يمكنني مساعدتك؟", -10, None),
                    ("outbound", "الحمدلله", -2, "read"),
                ],
            },
            {
                "key": "uk_phone",
                "name": "WhatsApp: 447710173736",
                "phone": "+447710173736",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "Hello, is this still available?", -90, None),
                    (
                        "outbound",
                        "Yes — *happy to help*. Any questions. We look forward to seeing you soon",
                        -5,
                        "delivered",
                    ),
                ],
            },
            {
                "key": "abhik",
                "name": "ABHIK Kuwait Energy",
                "phone": "+96550001234",
                "lead_company_name": "ABHIK Kuwait Energy",
                "session": "open",
                "messages": [
                    ("inbound", "Please send the brochure.", -50, None),
                    ("outbound", "Sure, sending it now.", -20, "read"),
                    ("inbound", "Thanks", -3, None),
                ],
            },
            {
                "key": "closed_session",
                "name": "أحمد الراوي",
                "phone": "+9647701122334",
                "lead_company_name": "شركة النور",
                "session": "closed",
                "messages": [
                    ("inbound", "هل العقار ما زال متاحاً؟", -60 * 30, None),
                    ("outbound", "نعم، متاح. هل تريد زيارة؟", -60 * 28, "read"),
                    ("inbound", "لاحقاً إن شاء الله", -60 * 26, None),
                ],
            },
            {
                "key": "failed_msg",
                "name": "سارة محمود",
                "phone": "+963944112233",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "مرحبا", -45, None),
                    ("outbound", "أهلاً سارة، كيف نقدر نساعدك؟", -30, "failed"),
                    ("outbound", "تجربة إعادة إرسال", -5, "sent"),
                ],
            },
            {
                "key": "formatted",
                "name": "ليلى حسن",
                "phone": "+16465550199",
                "lead_company_name": "NY Homes LLC",
                "session": "open",
                "messages": [
                    ("inbound", "Can you confirm the price?", -70, None),
                    (
                        "outbound",
                        "Sure — *price* is _negotiable_. See https://example.com/listing",
                        -40,
                        "read",
                    ),
                    ("inbound", "[image message]", -15, None),
                    ("outbound", "Received, thank you.", -4, "delivered"),
                ],
            },
            {
                "key": "omar",
                "name": "عمر الخطيب",
                "phone": "+9647813000456",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "السلام عليكم", -200, None),
                    ("outbound", "وعليكم السلام", -180, "read"),
                    ("inbound", "أبي تفاصيل المشروع", -8, None),
                ],
            },
            {
                "key": "noor",
                "name": "نور عبدالله",
                "phone": "+971501112233",
                "lead_company_name": "Dubai Vista",
                "session": "closed",
                "messages": [
                    ("inbound", "Interested in 2BR", -60 * 40, None),
                    ("outbound", "Great — I'll share options tomorrow.", -60 * 39, "delivered"),
                ],
            },
            {
                "key": "errors",
                "name": "كريم جواد",
                "phone": "+9647509988776",
                "lead_company_name": "",
                "session": "open",
                "messages": [
                    ("inbound", "؟", -55, None),
                    ("outbound", "[errors message]", -40, "failed"),
                    ("inbound", "ما فهمت", -6, None),
                ],
            },
            {
                "key": "zainab",
                "name": "زينب العلي",
                "phone": "+9647822113344",
                "lead_company_name": "معرض البيت",
                "session": "open",
                "messages": [
                    ("inbound", "موعد المعاينة؟", -33, None),
                    ("outbound", "غداً الساعة ٤ عصراً إن ناسبك.", -12, "read"),
                    ("inbound", "تمام", -1, None),
                ],
            },
        ]

        clients: list[Client] = []
        for spec in thread_specs:
            client = self._create_seed_client(
                company=company,
                user=user,
                name=spec["name"],
                phone=spec["phone"],
                lead_company_name=spec["lead_company_name"],
                status=status,
                channel=channel,
                key=spec["key"],
            )
            self._create_messages(
                client=client,
                user=user,
                phone=client.phone_number or spec["phone"],
                message_specs=spec["messages"],
                now=now,
            )
            clients.append(client)
        return clients

    def _create_seed_client(
        self,
        *,
        company: Company,
        user,
        name: str,
        phone: str,
        lead_company_name: str,
        status,
        channel,
        key: str,
    ) -> Client:
        # Avoid colliding with existing company phones: if taken, tweak last digits.
        phone_to_use = self._unique_phone(company, phone)

        client = Client(
            name=name,
            priority="medium",
            type="fresh",
            communication_way=channel,
            status=status,
            phone_number=phone_to_use,
            lead_company_name=lead_company_name or None,
            company=company,
            assigned_to=user,
            assigned_at=timezone.now(),
            source="whatsapp",
            notes=f"{SEED_MARKER} seed thread key={key}",
        )
        client.save()
        ClientPhoneNumber.objects.create(
            client=client,
            phone_number=phone_to_use,
            phone_type="mobile",
            is_primary=True,
        )
        return client

    def _unique_phone(self, company: Company, phone: str) -> str:
        from integrations.services.phone_match import canonical_phone_key

        base = phone
        key = canonical_phone_key(base)
        if not key:
            return base
        exists = ClientPhoneNumber.objects.filter(
            company=company, phone_normalized=key
        ).exists()
        if not exists:
            return base
        # Bump last digit until free (seed-only fallback).
        digits = "".join(ch for ch in base if ch.isdigit())
        prefix = "+" if base.startswith("+") else ""
        for i in range(1, 30):
            candidate_digits = digits[:-2] + f"{(int(digits[-2:]) + i) % 100:02d}"
            candidate = prefix + candidate_digits
            cand_key = canonical_phone_key(candidate)
            if cand_key and not ClientPhoneNumber.objects.filter(
                company=company, phone_normalized=cand_key
            ).exists():
                return candidate
        raise CommandError(f"Could not allocate unique phone near {phone}")

    def _create_messages(
        self,
        *,
        client: Client,
        user,
        phone: str,
        message_specs: list[tuple],
        now,
    ) -> None:
        from django.core.files.base import ContentFile

        # 1x1 PNG for seeded image bubbles (UI review without Meta).
        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
            b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        for idx, spec in enumerate(message_specs):
            direction, body, minutes_ago, delivery_status, kind, filename = self._parse_message_spec(spec)
            created_at = now + timedelta(minutes=minutes_ago)
            is_out = direction == "outbound"
            payload, mime = self._seed_media_bytes(kind, tiny_png) if kind else (None, "")
            msg = LeadWhatsAppMessage(
                client=client,
                phone_number=phone,
                body=body,
                direction=(
                    LeadWhatsAppMessage.DIRECTION_OUTBOUND
                    if is_out
                    else LeadWhatsAppMessage.DIRECTION_INBOUND
                ),
                whatsapp_message_id=f"seed_wamid_{client.id}_{idx}",
                phone_number_id=FAKE_PHONE_NUMBER_ID,
                delivery_status=delivery_status if is_out else None,
                delivery_error=(
                    "Message failed to send (seeded for UI review)"
                    if delivery_status == "failed"
                    else None
                ),
                created_by=user if is_out else None,
                send_source=MessageSendSource.MANUAL,
                # Outbound N/A; older inbound read; leave last inbound unread for badge demo.
                is_read=True if is_out else False,
                is_voice_note=kind == "audio",
            )
            if kind and payload is not None:
                msg.attachment_kind = kind
                msg.attachment_mime = mime
                msg.attachment_size = len(payload)
                msg.original_filename = filename or f"seed.{kind}"
                ext = {"image": "png", "document": "pdf", "audio": "wav", "video": "mp4"}.get(kind, "bin")
                if kind == "image":
                    msg.attachment_width = 1
                    msg.attachment_height = 1
                msg.attachment.save(
                    f"seed_{client.id}_{idx}.{ext}",
                    ContentFile(payload),
                    save=False,
                )
            msg.save()
            LeadWhatsAppMessage.objects.filter(pk=msg.pk).update(created_at=created_at)

        # Mark all but the latest inbound as read so the badge shows a realistic count.
        inbound_ids = list(
            LeadWhatsAppMessage.objects.filter(
                client=client,
                direction=LeadWhatsAppMessage.DIRECTION_INBOUND,
            )
            .order_by("-created_at")
            .values_list("id", flat=True)
        )
        if len(inbound_ids) > 1:
            LeadWhatsAppMessage.objects.filter(id__in=inbound_ids[1:]).update(is_read=True)

    def _parse_message_spec(self, spec):
        """4-tuples stay text. `[image message]` and dicts with kind become attachments."""
        sentinels = {
            "[image message]": ("image", "seed-photo.png"),
            "[video message]": ("video", "site-tour.mp4"),
            "[audio message]": ("audio", "voice-note.wav"),
            "[document message]": ("document", "عرض-السعر.pdf"),
        }
        if isinstance(spec, dict):
            return (
                spec["direction"],
                spec.get("body") or "",
                spec["minutes_ago"],
                spec.get("delivery"),
                spec.get("kind"),
                spec.get("filename"),
            )
        direction, body, minutes_ago, delivery = spec
        kind, filename = sentinels.get((body or "").strip(), (None, None))
        if kind:
            body = ""
        return direction, body, minutes_ago, delivery, kind, filename

    def _seed_media_bytes(self, kind: str, tiny_png: bytes) -> tuple[bytes, str]:
        if kind == "image":
            return tiny_png, "image/png"
        if kind == "document":
            pdf = (
                b"%PDF-1.1\n"
                b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]>>endobj\n"
                b"trailer<</Root 1 0 R>>\n%%EOF"
            )
            return pdf, "application/pdf"
        if kind == "audio":
            return self._silence_wav(), "audio/wav"
        # Not a decodable movie — enough for the list label and the video shell.
        return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32, "video/mp4"

    def _silence_wav(self) -> bytes:
        import struct

        sample_rate = 8000
        samples = b"\x00\x00" * 800  # 0.1s of silence
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + len(samples),
            b"WAVE",
            b"fmt ",
            16,
            1,
            1,
            sample_rate,
            sample_rate * 2,
            2,
            16,
            b"data",
            len(samples),
        )
        return header + samples

