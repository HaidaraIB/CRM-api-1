"""
Seed fake WhatsApp calls for local Calls inbox + lead timeline UI review.

Does not call Meta. Softphone is not used.

Run:
  .\\.venv\\Scripts\\python.exe manage.py seed_whatsapp_calls --company-id 123 --user-id 142
  .\\.venv\\Scripts\\python.exe manage.py seed_whatsapp_calls --company-id 123 --user-id 142 --replace
"""
from __future__ import annotations

import struct
import wave
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from companies.models import Company
from crm.models import Client, ClientCall, ClientCallSource, ClientPhoneNumber
from integrations.models import (
    WhatsAppAccount,
    WhatsAppCall,
    WhatsAppCallDirection,
    WhatsAppCallRecordingStatus,
    WhatsAppCallStatus,
)
from integrations.services.whatsapp_calling import ensure_client_call_for_whatsapp_call
from integrations.storage.recordings import save_recording

SEED_MARKER = "[WA-CALL-SEED]"
SEED_META_PREFIX = "wacid.seed."


def _tiny_wav_bytes(duration_sec: float = 1.2, freq: float = 440.0) -> bytes:
    """Generate a short mono WAV tone for timeline / Calls inbox playback."""
    import math

    rate = 16000
    n = int(rate * duration_sec)
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            # Soft sine; fade out
            amp = 0.35 * (1.0 - i / n)
            sample = int(amp * 32767 * math.sin(2 * math.pi * freq * (i / rate)))
            frames += struct.pack("<h", sample)
        wf.writeframes(frames)
    return buf.getvalue()


class Command(BaseCommand):
    help = (
        "Seed WhatsAppCall rows (ringing / answered / missed / etc.) with optional "
        "recording + ClientCall timeline entries for Calls UI review."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company-id", type=int, required=True)
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument(
            "--replace",
            action="store_true",
            help=f"Delete prior {SEED_MARKER} WhatsApp calls for this company first.",
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

        wa = (
            WhatsAppAccount.objects.filter(company=company, status="connected")
            .order_by("-updated_at")
            .first()
        )
        if not wa:
            raise CommandError(
                "No connected WhatsAppAccount for this company. "
                "Run seed_whatsapp_chats first or connect WhatsApp in Integrations."
            )

        with transaction.atomic():
            if replace:
                deleted = self._replace_seed(company)
                self.stdout.write(f"Replaced prior seed: {deleted}")

            wa.calling_enabled = True
            wa.save(update_fields=["calling_enabled", "updated_at"])

            clients = self._pick_or_create_clients(company, user)
            created = self._seed_calls(company, wa, user, clients)

        self.stdout.write(self.style.SUCCESS("WhatsApp calls seeded successfully."))
        self.stdout.write(
            f"company_id={company.id}, user_id={user.id}, whatsapp_account_id={wa.id}, "
            f"calling_enabled={wa.calling_enabled}, calls={len(created)}"
        )
        self.stdout.write(
            "Open /calls as this user. Live Meta dial/accept still needs a real WABA; "
            "list filters, detail drawer, and lead timeline playback are ready."
        )
        ringing = [c for c in created if c.status == WhatsAppCallStatus.RINGING]
        if ringing:
            self.stdout.write(
                self.style.WARNING(
                    f"Seeded {len(ringing)} ringing inbound call(s) — "
                    "incoming modal may appear while polling (Accept will fail against Meta)."
                )
            )

    def _replace_seed(self, company: Company) -> dict:
        qs = WhatsAppCall.objects.filter(
            company=company, meta_call_id__startswith=SEED_META_PREFIX
        )
        client_call_ids = list(
            qs.exclude(client_call_id=None).values_list("client_call_id", flat=True)
        )
        calls_deleted, _ = qs.delete()
        cc_deleted = 0
        if client_call_ids:
            cc_deleted, _ = ClientCall.objects.filter(
                id__in=client_call_ids, source=ClientCallSource.WHATSAPP
            ).delete()
        seed_clients = Client.objects.filter(company=company, notes__contains=SEED_MARKER)
        clients_deleted, _ = seed_clients.delete()
        return {
            "whatsapp_calls": calls_deleted,
            "client_calls": cc_deleted,
            "seed_clients": clients_deleted,
        }

    def _pick_or_create_clients(self, company: Company, user) -> list[Client]:
        existing = list(
            Client.objects.filter(company=company)
            .exclude(phone_number="")
            .order_by("-id")[:4]
        )
        if len(existing) >= 3:
            return existing[:4]

        specs = [
            ("Dr. Fatima Al-Otaibi", "+966559876543"),
            ("Khaled Al-Zahrani", "+966501234567"),
            ("Sara Khalid", "+9647701112233"),
        ]
        created: list[Client] = []
        for name, phone in specs:
            phone_to_use = self._unique_phone(company, phone)
            client = Client(
                name=name,
                priority="medium",
                type="fresh",
                phone_number=phone_to_use,
                company=company,
                assigned_to=user,
                assigned_at=timezone.now(),
                source="whatsapp",
                notes=f"{SEED_MARKER} demo patient for WhatsApp calls UI",
            )
            client.save()
            ClientPhoneNumber.objects.create(
                client=client,
                phone_number=phone_to_use,
                phone_type="mobile",
                is_primary=True,
            )
            created.append(client)
        return existing + created

    def _unique_phone(self, company: Company, phone: str) -> str:
        from integrations.services.phone_match import canonical_phone_key

        key = canonical_phone_key(phone)
        if key and not ClientPhoneNumber.objects.filter(
            company=company, phone_normalized=key
        ).exists():
            return phone
        digits = "".join(ch for ch in phone if ch.isdigit())
        prefix = "+" if phone.startswith("+") else ""
        for i in range(1, 40):
            candidate = prefix + digits[:-2] + f"{(int(digits[-2:] or 0) + i) % 100:02d}"
            cand_key = canonical_phone_key(candidate)
            if cand_key and not ClientPhoneNumber.objects.filter(
                company=company, phone_normalized=cand_key
            ).exists():
                return candidate
        raise CommandError(f"Could not allocate unique phone near {phone}")

    def _seed_calls(
        self,
        company: Company,
        wa: WhatsAppAccount,
        user,
        clients: list[Client],
    ) -> list[WhatsAppCall]:
        now = timezone.now()
        wav = _tiny_wav_bytes()
        c0 = clients[0]
        c1 = clients[1] if len(clients) > 1 else clients[0]
        c2 = clients[2] if len(clients) > 2 else clients[0]
        c3 = clients[3] if len(clients) > 3 else clients[0]

        specs = [
            {
                "meta_id": f"{SEED_META_PREFIX}ringing.1",
                "direction": WhatsAppCallDirection.INBOUND,
                # Use missed (not ringing) so seed data does not spam the incoming modal / pending poll.
                "status": WhatsAppCallStatus.MISSED,
                "client": c0,
                "agent": None,
                "started_at": now - timedelta(seconds=25),
                "answered_at": None,
                "ended_at": now - timedelta(seconds=5),
                "duration_sec": 0,
                "notes": "",
                "peer_name": c0.name,
                "recording": False,
                "offer_sdp": "",
            },
            {
                "meta_id": f"{SEED_META_PREFIX}ended.record.1",
                "direction": WhatsAppCallDirection.INBOUND,
                "status": WhatsAppCallStatus.ENDED,
                "client": c0,
                "agent": user,
                "started_at": now - timedelta(hours=2),
                "answered_at": now - timedelta(hours=2) + timedelta(seconds=8),
                "ended_at": now - timedelta(hours=2) + timedelta(minutes=4, seconds=35),
                "duration_sec": 275,
                "notes": "Patient asked about appointment availability.",
                "peer_name": c0.name,
                "recording": True,
            },
            {
                "meta_id": f"{SEED_META_PREFIX}outbound.ended.1",
                "direction": WhatsAppCallDirection.OUTBOUND,
                "status": WhatsAppCallStatus.ENDED,
                "client": c1,
                "agent": user,
                "started_at": now - timedelta(hours=5),
                "answered_at": now - timedelta(hours=5) + timedelta(seconds=12),
                "ended_at": now - timedelta(hours=5) + timedelta(minutes=7, seconds=30),
                "duration_sec": 450,
                "notes": "Follow-up callback after WhatsApp chat.",
                "peer_name": c1.name,
                "recording": True,
            },
            {
                "meta_id": f"{SEED_META_PREFIX}missed.1",
                "direction": WhatsAppCallDirection.INBOUND,
                "status": WhatsAppCallStatus.MISSED,
                "client": c2,
                "agent": None,
                "started_at": now - timedelta(hours=1),
                "answered_at": None,
                "ended_at": now - timedelta(hours=1) + timedelta(seconds=40),
                "duration_sec": 0,
                "notes": "",
                "peer_name": c2.name,
                "recording": False,
            },
            {
                "meta_id": f"{SEED_META_PREFIX}no_answer.1",
                "direction": WhatsAppCallDirection.OUTBOUND,
                "status": WhatsAppCallStatus.NO_ANSWER,
                "client": c2,
                "agent": user,
                "started_at": now - timedelta(days=1),
                "answered_at": None,
                "ended_at": now - timedelta(days=1) + timedelta(seconds=32),
                "duration_sec": 0,
                "notes": "",
                "peer_name": c2.name,
                "recording": False,
            },
            {
                "meta_id": f"{SEED_META_PREFIX}rejected.1",
                "direction": WhatsAppCallDirection.INBOUND,
                "status": WhatsAppCallStatus.REJECTED,
                "client": c3,
                "agent": user,
                "started_at": now - timedelta(hours=8),
                "answered_at": None,
                "ended_at": now - timedelta(hours=8) + timedelta(seconds=6),
                "duration_sec": 0,
                "notes": "",
                "peer_name": c3.name,
                "recording": False,
            },
            {
                "meta_id": f"{SEED_META_PREFIX}answered.live.1",
                "direction": WhatsAppCallDirection.INBOUND,
                "status": WhatsAppCallStatus.ANSWERED,
                "client": c1,
                "agent": user,
                "started_at": now - timedelta(minutes=3),
                "answered_at": now - timedelta(minutes=2, seconds=50),
                "ended_at": None,
                "duration_sec": 0,
                "notes": "Still on the line (seed active-looking row).",
                "peer_name": c1.name,
                "recording": False,
            },
        ]

        created: list[WhatsAppCall] = []
        for spec in specs:
            peer = (spec["client"].phone_number or "").replace(" ", "")
            call, _ = WhatsAppCall.objects.update_or_create(
                whatsapp_account=wa,
                meta_call_id=spec["meta_id"],
                defaults={
                    "company": company,
                    "direction": spec["direction"],
                    "status": spec["status"],
                    "peer_phone": "".join(ch for ch in peer if ch.isdigit()) or "966500000000",
                    "peer_name": spec["peer_name"] or "",
                    "client": spec["client"],
                    "agent": spec["agent"],
                    "offer_sdp": spec.get("offer_sdp") or "",
                    "answer_sdp": "",
                    "started_at": spec["started_at"],
                    "answered_at": spec["answered_at"],
                    "ended_at": spec["ended_at"],
                    "duration_sec": spec["duration_sec"],
                    "notes": spec["notes"],
                    "recording_status": WhatsAppCallRecordingStatus.NONE,
                    "recording_storage_key": "",
                    "error_message": "",
                    "raw_payload": {"seed": True, "marker": SEED_MARKER},
                },
            )

            if spec["recording"]:
                key = save_recording(
                    company_id=company.id,
                    linkedid=call.meta_call_id,
                    file_bytes=wav,
                    original_filename="seed_call.wav",
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

            if call.status in (
                WhatsAppCallStatus.ENDED,
                WhatsAppCallStatus.MISSED,
                WhatsAppCallStatus.REJECTED,
                WhatsAppCallStatus.NO_ANSWER,
                WhatsAppCallStatus.ANSWERED,
            ):
                ensure_client_call_for_whatsapp_call(call)
                call.refresh_from_db()

            created.append(call)

        return created
