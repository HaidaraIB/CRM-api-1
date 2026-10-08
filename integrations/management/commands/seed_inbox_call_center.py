"""
Seed Omni-Channel Inbox + call-center demo data for local UI review.

Run:
  .\\.venv\\Scripts\\python.exe manage.py seed_inbox_call_center --company-id 123 --user-id 142 --replace
"""
from __future__ import annotations

import math
import struct
import wave
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from companies.models import Company
from crm.models import Client
from integrations.models import (
    IntegrationAccount,
    IntegrationPlatform,
    MessageTemplate,
    MetaInboxConnection,
    QuickReply,
    SocialChannel,
    SocialContact,
    SocialConversation,
    SocialMessage,
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
    WhatsAppConversationStatus,
    WhatsAppPurpose,
)
from integrations.storage.recordings import save_recording
from settings.models import Channel, LeadStatus


def _tiny_wav_bytes(duration_sec: float = 2.0, freq: float = 440.0) -> bytes:
    """Short mono WAV tone for Calls UI playback demos."""
    rate = 16000
    n = int(rate * duration_sec)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            amp = 0.35 * (1.0 - i / max(n, 1))
            sample = int(amp * 32767 * math.sin(2 * math.pi * freq * (i / rate)))
            frames += struct.pack("<h", sample)
        wf.writeframes(frames)
    return buf.getvalue()

SEED_MARKER = "[INBOX-SEED]"
SEED_PAGE_ID = "seed_meta_inbox_page_ui_review"
SEED_IG_ID = "seed_meta_inbox_ig_ui_review"
SEED_WA_INBOX_PID = "seed_wa_inbox_phone_number_id_ui_review"
SEED_WA_CRM_PID = "seed_wa_phone_number_id_ui_review"
SEED_AGENT_PREFIX = "cc_seed_"
SEED_TEMPLATE_PREFIX = "wa_seed_"


class Command(BaseCommand):
    help = (
        "Seed call-center agents, Meta/WhatsApp inbox connections, conversations, "
        "messages, quick replies, and inbox calls for Inbox / Calls Settings review."
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
        company = Company.objects.filter(id=options["company_id"]).first()
        if not company:
            raise CommandError(f"Company id={options['company_id']} not found.")

        User = get_user_model()
        owner = User.objects.filter(id=options["user_id"]).first()
        if not owner:
            raise CommandError(f"User id={options['user_id']} not found.")
        if owner.company_id != company.id:
            raise CommandError(
                f"User {owner.id} belongs to company {owner.company_id}, expected {company.id}."
            )

        with transaction.atomic():
            if options["replace"]:
                self.stdout.write(f"Replaced prior seed: {self._replace_seed(company)}")

            agents = self._seed_agents(company)
            meta_conn = self._ensure_meta_inbox(company, owner)
            wa_inbox = self._ensure_wa_inbox(company, owner)
            wa_crm = self._ensure_wa_crm(company, owner)
            templates = self._seed_templates(company)
            quick = self._seed_quick_replies(company, owner)
            with self._mute_model_signals():
                threads = self._seed_conversations(company, owner, agents, meta_conn, wa_inbox)
                calls = self._seed_calls(company, wa_inbox, agents, threads)

        self.stdout.write(self.style.SUCCESS("Inbox call-center seed complete."))
        self.stdout.write(
            f"company_id={company.id} owner={owner.username} agents={[a.username for a in agents]} "
            f"threads={len(threads)} quick_replies={len(quick)} templates={len(templates)} "
            f"calls={len(calls)} wa_inbox_id={wa_inbox.id} wa_crm_id={wa_crm.id}"
        )
        self.stdout.write(
            "Log in as memo (owner) for full Inbox + Calls Settings, or as "
            "cc_seed_sara / cc_seed_ali (password: testpass123) for call-center Ready/Away."
        )

    def _mute_model_signals(self):
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
            stashed = [(sig, list(sig.receivers)) for sig in targets]
            for sig, _ in stashed:
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
        User = get_user_model()
        contacts = SocialContact.objects.filter(
            company=company, external_id__startswith="seed_"
        )
        contact_ids = list(contacts.values_list("id", flat=True))
        convs = SocialConversation.objects.filter(
            company=company, contact_id__in=contact_ids
        )
        conv_ids = list(convs.values_list("id", flat=True))
        msg_n, _ = SocialMessage.objects.filter(conversation_id__in=conv_ids).delete()
        call_n, _ = WhatsAppCall.objects.filter(
            company=company, meta_call_id__startswith="wacid.inbox.seed."
        ).delete()
        conv_n, _ = convs.delete()
        contact_n, _ = contacts.delete()
        qr_n, _ = QuickReply.objects.filter(
            company=company, title__startswith=SEED_MARKER
        ).delete()
        # Also wipe unmarked seed titles from an earlier partial seed.
        qr_n2, _ = QuickReply.objects.filter(
            company=company, body__contains=SEED_MARKER
        ).delete()
        agent_n, _ = User.objects.filter(
            company=company, username__startswith=SEED_AGENT_PREFIX
        ).delete()
        MetaInboxConnection.objects.filter(page_id=SEED_PAGE_ID).delete()
        WhatsAppAccount.objects.filter(phone_number_id=SEED_WA_INBOX_PID).delete()
        Client.objects.filter(company=company, notes__contains=SEED_MARKER).delete()
        return {
            "messages": msg_n,
            "conversations": conv_n,
            "contacts": contact_n,
            "calls": call_n,
            "quick_replies": qr_n + qr_n2,
            "agents": agent_n,
        }

    def _seed_agents(self, company: Company):
        User = get_user_model()
        specs = [
            ("cc_seed_sara", "Sara", "Hassan"),
            ("cc_seed_ali", "Ali", "Karim"),
            ("cc_seed_noor", "Noor", "Salim"),
        ]
        agents = []
        for username, first, last in specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@example.com",
                    "first_name": first,
                    "last_name": last,
                    "company": company,
                    "role": "call_center",
                    "is_active": True,
                },
            )
            if not created:
                user.company = company
                user.role = "call_center"
                user.is_active = True
                user.first_name = first
                user.last_name = last
                user.save(
                    update_fields=[
                        "company",
                        "role",
                        "is_active",
                        "first_name",
                        "last_name",
                    ]
                )
            user.set_password("testpass123")
            user.save(update_fields=["password"])
            agents.append(user)
        return agents

    def _ensure_meta_inbox(self, company: Company, owner) -> MetaInboxConnection:
        integ, _ = IntegrationAccount.objects.update_or_create(
            company=company,
            platform=IntegrationPlatform.META_INBOX,
            external_account_id="seed_meta_inbox_external",
            defaults={
                "name": f"{SEED_MARKER} Instagram & Messenger",
                "status": "connected",
                "is_active": True,
                "external_account_name": "Seed Demo Page",
                "created_by": owner,
                "error_message": "",
                "metadata": {"seed": True},
            },
        )
        integ.set_access_token("seed-fake-meta-inbox-token")
        integ.save(update_fields=["access_token", "updated_at"])

        conn, _ = MetaInboxConnection.objects.update_or_create(
            page_id=SEED_PAGE_ID,
            defaults={
                "company": company,
                "integration_account": integ,
                "page_name": "Seed Demo Page",
                "ig_user_id": SEED_IG_ID,
                "ig_username": "seed_demo_shop",
                "status": "connected",
                "instagram_subscribed": True,
                "messenger_subscribed": True,
                "error_message": "",
            },
        )
        conn.set_page_access_token("seed-fake-page-token")
        conn.save(update_fields=["page_access_token", "updated_at"])
        return conn

    def _ensure_wa_inbox(self, company: Company, owner) -> WhatsAppAccount:
        integ, _ = IntegrationAccount.objects.update_or_create(
            company=company,
            platform=IntegrationPlatform.WHATSAPP_INBOX,
            external_account_id="seed_wa_inbox_external",
            defaults={
                "name": f"{SEED_MARKER} Inbox WhatsApp",
                "status": "connected",
                "is_active": True,
                "external_account_name": "Inbox Seed Number",
                "created_by": owner,
                "error_message": "",
                "metadata": {"seed": True},
            },
        )
        integ.set_access_token("seed-fake-wa-inbox-token")
        integ.save(update_fields=["access_token", "updated_at"])

        wa, created = WhatsAppAccount.objects.get_or_create(
            phone_number_id=SEED_WA_INBOX_PID,
            defaults={
                "company": company,
                "purpose": WhatsAppPurpose.INBOX,
                "waba_id": "seed_inbox_waba",
                "business_id": "seed_inbox_biz",
                "display_phone_number": "+9647701112222",
                "status": "connected",
                "integration_account": integ,
                "calling_enabled": True,
                "call_hours_enabled": True,
            },
        )
        if not created:
            wa.company = company
            wa.purpose = WhatsAppPurpose.INBOX
            wa.status = "connected"
            wa.integration_account = integ
            wa.display_phone_number = wa.display_phone_number or "+9647701112222"
            wa.calling_enabled = True
            wa.call_hours_enabled = True
            wa.save()
        wa.set_access_token("seed-fake-wa-inbox-token")
        wa.save(update_fields=["access_token", "updated_at"])
        return wa

    def _ensure_wa_crm(self, company: Company, owner) -> WhatsAppAccount:
        integ, _ = IntegrationAccount.objects.update_or_create(
            company=company,
            platform=IntegrationPlatform.WHATSAPP,
            external_account_id="seed_wa_external_account",
            defaults={
                "name": f"{SEED_MARKER} CRM WhatsApp",
                "status": "connected",
                "is_active": True,
                "external_account_name": "CRM Seed Number",
                "created_by": owner,
                "error_message": "",
                "metadata": {
                    "display_name_status": "APPROVED",
                    "display_name_approved": True,
                    "seed": True,
                },
            },
        )
        integ.set_access_token("seed-fake-wa-crm-token")
        integ.save(update_fields=["access_token", "updated_at"])

        wa, created = WhatsAppAccount.objects.get_or_create(
            phone_number_id=SEED_WA_CRM_PID,
            defaults={
                "company": company,
                "purpose": WhatsAppPurpose.CRM,
                "waba_id": "seed_waba_id",
                "business_id": "seed_business_id",
                "display_phone_number": "+9647700000000",
                "status": "connected",
                "integration_account": integ,
                "calling_enabled": True,
                "call_hours_enabled": True,
            },
        )
        if not created:
            wa.company = company
            wa.purpose = WhatsAppPurpose.CRM
            wa.status = "connected"
            wa.integration_account = integ
            wa.calling_enabled = True
            wa.call_hours_enabled = True
            wa.save()
        wa.set_access_token("seed-fake-wa-crm-token")
        wa.save(update_fields=["access_token", "updated_at"])
        return wa

    def _seed_templates(self, company: Company) -> list[MessageTemplate]:
        specs = [
            (
                f"{SEED_TEMPLATE_PREFIX}welcome_ar",
                "مرحباً {{1}}، شكراً لتواصلك معنا.",
                "ar",
            ),
            (
                f"{SEED_TEMPLATE_PREFIX}followup_en",
                "Hi {{1}}, just following up on your inquiry.",
                "en_US",
            ),
        ]
        rows = []
        for name, content, language in specs:
            tmpl, _ = MessageTemplate.objects.update_or_create(
                company=company,
                name=name,
                defaults={
                    "channel_type": MessageTemplate.CHANNEL_WHATSAPP_API,
                    "content": content,
                    "category": MessageTemplate.CATEGORY_UTILITY,
                    "language": language,
                    "header_type": "text",
                    "header_text": "Seed",
                    "footer": "",
                    "buttons": [],
                    "meta_template_id": f"seed_meta_{name}",
                    "meta_status": "APPROVED",
                },
            )
            rows.append(tmpl)
        return rows

    def _seed_quick_replies(self, company: Company, owner) -> list[QuickReply]:
        specs = [
            ("Greeting", "Hello! Thanks for reaching out — how can I help today?"),
            ("Hours", "We're available Sat–Thu, 9:00–18:00 Baghdad time."),
            ("Pricing", "I can share our current packages. Which service are you interested in?"),
            ("Arabic welcome", "مرحباً بك، كيف يمكننا مساعدتك اليوم؟"),
        ]
        rows = []
        for title, body in specs:
            row, _ = QuickReply.objects.update_or_create(
                company=company,
                title=title,
                defaults={"body": f"{body}\n{SEED_MARKER}", "created_by": owner},
            )
            rows.append(row)
        return rows

    def _seed_conversations(self, company, owner, agents, meta_conn, wa_inbox):
        now = timezone.now()
        sara, ali, noor = agents
        avatar = "https://i.pravatar.cc/150?u=seed_{}"

        specs = [
            {
                "key": "ig_sara",
                "channel": SocialChannel.INSTAGRAM,
                "name": "Lina Mahmoud",
                "username": "lina.m",
                "external_id": "seed_ig_lina",
                "assignee": sara,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": True,
                "unread": 2,
                "inbound_min": 5,
                "messages": [
                    ("inbound", "Hi, is the blue dress still available?", 40),
                    ("outbound", "Yes — size M is in stock.", 30),
                    ("inbound", "Can you hold it until tomorrow?", 5),
                ],
            },
            {
                "key": "ig_unassigned",
                "channel": SocialChannel.INSTAGRAM,
                "name": "Omar Faris",
                "username": "omar.faris",
                "external_id": "seed_ig_omar",
                "assignee": None,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": False,
                "unread": 1,
                "inbound_min": 2,
                "messages": [
                    ("inbound", "Price for delivery to Erbil?", 2),
                ],
            },
            {
                "key": "msg_ali",
                "channel": SocialChannel.MESSENGER,
                "name": "Huda Al-Rashid",
                "username": "",
                "external_id": "seed_msg_huda",
                "assignee": ali,
                "status": WhatsAppConversationStatus.PENDING,
                "starred": False,
                "unread": 0,
                "inbound_min": 90,
                "messages": [
                    ("inbound", "I filled the contact form yesterday.", 120),
                    ("outbound", "Thanks Huda — a specialist will call you.", 90),
                ],
            },
            {
                "key": "msg_done",
                "channel": SocialChannel.MESSENGER,
                "name": "Karim Nouri",
                "username": "karim.n",
                "external_id": "seed_msg_karim",
                "assignee": noor,
                "status": WhatsAppConversationStatus.DONE,
                "starred": False,
                "unread": 0,
                "inbound_min": 400,
                "messages": [
                    ("inbound", "All set, thank you!", 400),
                    ("outbound", "Glad we could help.", 390),
                ],
            },
            {
                "key": "wa_sara",
                "channel": SocialChannel.WHATSAPP,
                "name": "Zainab Saleh",
                "username": "",
                "external_id": "9647705551001",
                "assignee": sara,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": True,
                "unread": 3,
                "inbound_min": 8,
                "messages": [
                    ("inbound", "السلام عليكم، أبغى استفسر عن الشقق", 60),
                    ("outbound", "وعليكم السلام، تفضل.", 45),
                    ("inbound", "هل عندكم وحدات في المنصور؟", 8),
                ],
            },
            {
                "key": "wa_unassigned",
                "channel": SocialChannel.WHATSAPP,
                "name": "",
                "username": "",
                "external_id": "9647705551002",
                "assignee": None,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": False,
                "unread": 1,
                "inbound_min": 1,
                "messages": [
                    ("inbound", "Hello, are you open now?", 1),
                ],
            },
            {
                "key": "wa_template_window",
                "channel": SocialChannel.WHATSAPP,
                "name": "Yusuf Habib",
                "username": "",
                "external_id": "9647705551003",
                "assignee": ali,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": False,
                "unread": 0,
                "inbound_min": 60 * 30,  # outside 24h → template required
                "messages": [
                    ("inbound", "Interested in your offer.", 60 * 30),
                    ("outbound", "I'll send details shortly.", 60 * 29),
                ],
            },
            {
                "key": "ig_converted",
                "channel": SocialChannel.INSTAGRAM,
                "name": "Maya Quinn",
                "username": "maya.q",
                "external_id": "seed_ig_maya",
                "assignee": noor,
                "status": WhatsAppConversationStatus.OPEN,
                "starred": False,
                "unread": 0,
                "inbound_min": 15,
                "convert": True,
                "messages": [
                    ("inbound", "Please create a lead for me.", 20),
                    ("outbound", "Done — our team will follow up.", 15),
                ],
            },
        ]

        threads = []
        for spec in specs:
            channel = spec["channel"]
            is_wa = channel == SocialChannel.WHATSAPP
            contact, _ = SocialContact.objects.update_or_create(
                company=company,
                channel=channel,
                external_id=spec["external_id"],
                defaults={
                    "connection": None if is_wa else meta_conn,
                    "wa_inbox_number": wa_inbox if is_wa else None,
                    "name": spec["name"],
                    "username": spec["username"],
                    "profile_pic_url": avatar.format(spec["key"]) if spec["name"] else "",
                    "profile_fetched_at": now,
                },
            )
            last_inbound = now - timedelta(minutes=spec["inbound_min"])
            last_msg = spec["messages"][-1]
            last_at = now - timedelta(minutes=last_msg[2])
            client = None
            if spec.get("convert"):
                status = LeadStatus.objects.filter(company=company, is_active=True).first()
                ch = Channel.objects.filter(company=company, is_active=True).first()
                client = Client.objects.filter(
                    company=company, notes__contains=SEED_MARKER, phone_number="+9647705551999"
                ).first()
                if not client:
                    client = Client(
                        name=spec["name"] or "Seed Lead",
                        priority="medium",
                        type="fresh",
                        communication_way=ch,
                        status=status,
                        phone_number="+9647705551999",
                        company=company,
                        assigned_to=spec["assignee"] or owner,
                        assigned_at=now,
                        source="instagram",
                        notes=f"{SEED_MARKER} converted from inbox",
                        created_by=owner,
                    )
                    client.save()

            conv, _ = SocialConversation.objects.update_or_create(
                company=company,
                contact=contact,
                defaults={
                    "connection": None if is_wa else meta_conn,
                    "wa_inbox_number": wa_inbox if is_wa else None,
                    "channel": channel,
                    "status": spec["status"],
                    "is_starred": spec["starred"],
                    "assigned_to": spec["assignee"],
                    "client": client,
                    "converted_at": now if client else None,
                    "converted_by": owner if client else None,
                    "last_message_at": last_at,
                    "last_message_direction": last_msg[0],
                    "last_message_preview": last_msg[1][:280],
                    "last_inbound_at": last_inbound,
                    "unread_count": spec["unread"],
                },
            )
            SocialMessage.objects.filter(conversation=conv).delete()
            for direction, body, minutes_ago in spec["messages"]:
                sent_at = now - timedelta(minutes=minutes_ago)
                msg = SocialMessage.objects.create(
                    conversation=conv,
                    direction=direction,
                    body=body,
                    external_message_id=f"seed_mid_{spec['key']}_{minutes_ago}",
                    delivery_status="delivered" if direction == "outbound" else "",
                    sent_at=sent_at,
                    is_read=direction != "inbound" or spec["unread"] == 0,
                )
                SocialMessage.objects.filter(pk=msg.pk).update(created_at=sent_at)
            threads.append(conv)
        return threads

    def _seed_calls(self, company, wa_inbox, agents, threads):
        now = timezone.now()
        wa_threads = [c for c in threads if c.channel == SocialChannel.WHATSAPP]
        if not wa_threads:
            return []
        sara = agents[0]
        specs = [
            {
                "meta": "wacid.inbox.seed.missed.1",
                "status": WhatsAppCallStatus.MISSED,
                "direction": WhatsAppCallDirection.INBOUND,
                "agent": sara,
                "conv": wa_threads[0],
                "minutes_ago": 45,
                "duration": 0,
                "recording": False,
                "tone_hz": 440.0,
            },
            {
                "meta": "wacid.inbox.seed.ended.1",
                "status": WhatsAppCallStatus.ENDED,
                "direction": WhatsAppCallDirection.OUTBOUND,
                "agent": agents[1],
                "conv": wa_threads[0],
                "minutes_ago": 120,
                "duration": 185,
                "recording": True,
                "tone_hz": 440.0,
            },
            {
                "meta": "wacid.inbox.seed.ended.2",
                "status": WhatsAppCallStatus.ENDED,
                "direction": WhatsAppCallDirection.INBOUND,
                "agent": sara,
                "conv": wa_threads[0],
                "minutes_ago": 30,
                "duration": 94,
                "recording": True,
                "tone_hz": 523.25,
            },
        ]
        rows = []
        for spec in specs:
            started = now - timedelta(minutes=spec["minutes_ago"])
            answered = bool(spec["duration"])
            call, _ = WhatsAppCall.objects.update_or_create(
                company=company,
                meta_call_id=spec["meta"],
                defaults={
                    "whatsapp_account": wa_inbox,
                    "social_conversation": spec["conv"],
                    "direction": spec["direction"],
                    "status": spec["status"],
                    "peer_phone": spec["conv"].contact.external_id,
                    "peer_name": spec["conv"].contact.name or spec["conv"].contact.external_id,
                    "agent": spec["agent"],
                    "started_at": started,
                    "answered_at": started + timedelta(seconds=8) if answered else None,
                    "ended_at": started + timedelta(seconds=spec["duration"] or 20),
                    "duration_sec": spec["duration"],
                    "notes": SEED_MARKER,
                    "raw_payload": {"seed": True},
                    "recording_status": WhatsAppCallRecordingStatus.NONE,
                    "recording_storage_key": "",
                },
            )
            if spec.get("recording") and answered:
                key = save_recording(
                    company_id=company.id,
                    linkedid=call.meta_call_id,
                    file_bytes=_tiny_wav_bytes(duration_sec=2.5, freq=float(spec["tone_hz"])),
                    original_filename="seed_inbox_call.wav",
                    prefix="whatsapp_calls",
                )
                call.recording_storage_key = key
                call.recording_status = WhatsAppCallRecordingStatus.READY
                call.save(
                    update_fields=[
                        "recording_storage_key",
                        "recording_status",
                        "updated_at",
                    ]
                )
            rows.append(call)
        return rows
