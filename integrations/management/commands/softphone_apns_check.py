"""
Print softphone / APNs VoIP configuration status (no secrets).
Run: .venv\\Scripts\\python.exe manage.py softphone_apns_check
"""
from django.core.management.base import BaseCommand

from integrations.services.softphone_push import apns_voip_status


class Command(BaseCommand):
    help = "Verify APNS_VOIP_* env vars and iOS VoIP push readiness (no secret values printed)."

    def handle(self, *args, **options):
        status = apns_voip_status()
        self.stdout.write("Softphone iOS VoIP push configuration:")
        self.stdout.write(f"  configured: {status['configured']}")
        self.stdout.write(f"  APNS_VOIP_KEY_ID set: {status['key_id_set']}")
        self.stdout.write(f"  APNS_VOIP_TEAM_ID set: {status['team_id_set']}")
        self.stdout.write(f"  key source: {status['key_source']}")
        self.stdout.write(f"  key readable: {status['key_readable']}")
        self.stdout.write(f"  APNS_BUNDLE_ID: {status['bundle_id']}")
        self.stdout.write(f"  APNs topic: {status['voip_topic']}")
        self.stdout.write(f"  APNS_VOIP_USE_SANDBOX: {status['use_sandbox']}")
        self.stdout.write(f"  APNs host: {status['apns_host']}")

        if status["configured"]:
            if status["use_sandbox"]:
                self.stdout.write(
                    self.style.WARNING(
                        "  Sandbox APNs is enabled — production App Store builds will not receive pushes."
                    )
                )
            else:
                self.stdout.write(self.style.SUCCESS("  Ready for production VoIP pushes."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "  Missing APNS_VOIP_* configuration. "
                    "Set APNS_VOIP_KEY_CONTENT or APNS_VOIP_KEY_PATH, "
                    "APNS_VOIP_KEY_ID, APNS_VOIP_TEAM_ID, and APNS_BUNDLE_ID."
                )
            )
