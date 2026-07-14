from django.test import SimpleTestCase

from notifications.fcm_android_channels import (
    android_notification_raw_sound_basename,
    ios_notification_sound_filename,
    tenant_chat_apns_collapse_id,
    tenant_chat_ios_sound_filename,
)


class FcmNotificationSoundMappingTests(SimpleTestCase):
    def test_ios_sound_matches_android_basename_with_wav_extension(self):
        self.assertEqual(ios_notification_sound_filename("new_lead"), "notif_leads.wav")
        self.assertEqual(
            android_notification_raw_sound_basename("new_lead"),
            "notif_leads",
        )

    def test_general_notifications_use_platform_default(self):
        self.assertIsNone(ios_notification_sound_filename("general"))
        self.assertIsNone(android_notification_raw_sound_basename("general"))

    def test_whatsapp_sound_filename(self):
        self.assertEqual(
            ios_notification_sound_filename("whatsapp_message_received"),
            "notif_whatsapp.wav",
        )

    def test_tenant_chat_ios_sound_and_collapse(self):
        self.assertEqual(tenant_chat_ios_sound_filename(), "notif_tenant_chat.wav")
        self.assertEqual(tenant_chat_apns_collapse_id(42), "tenant_chat_42")
        self.assertIsNone(tenant_chat_apns_collapse_id(""))
