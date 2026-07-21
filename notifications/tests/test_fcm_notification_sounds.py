from django.test import SimpleTestCase

from notifications.fcm_android_channels import (
    android_notification_channel_id,
    android_notification_raw_sound_basename,
    ios_notification_sound_filename,
    team_activity_channel_for_action,
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

    def test_team_activity_reuses_category_sounds_by_action(self):
        cases = {
            "status_change": ("leads", "notif_leads", "notif_leads.wav"),
            "assignment": ("leads", "notif_leads", "notif_leads.wav"),
            "edit": ("leads", "notif_leads", "notif_leads.wav"),
            "lead_created": ("leads", "notif_leads", "notif_leads.wav"),
            "no_follow_up": ("leads", "notif_leads", "notif_leads.wav"),
            "call_logged": ("tasks", "notif_tasks", "notif_tasks.wav"),
            "visit_logged": ("tasks", "notif_tasks", "notif_tasks.wav"),
            "field_visit_logged": ("tasks", "notif_tasks", "notif_tasks.wav"),
            "task_created": ("tasks", "notif_tasks", "notif_tasks.wav"),
            "deal_won": ("deals", "notif_deals", "notif_deals.wav"),
        }
        for action, (channel, android_sound, ios_sound) in cases.items():
            self.assertEqual(team_activity_channel_for_action(action), channel)
            self.assertEqual(
                android_notification_channel_id("team_activity", action=action),
                channel,
            )
            self.assertEqual(
                android_notification_raw_sound_basename("team_activity", action=action),
                android_sound,
            )
            self.assertEqual(
                ios_notification_sound_filename("team_activity", action=action),
                ios_sound,
            )

    def test_team_activity_unknown_action_uses_team_activity_fallback(self):
        self.assertEqual(
            android_notification_channel_id("team_activity", action="unknown"),
            "team_activity",
        )
        self.assertEqual(
            ios_notification_sound_filename("team_activity", action=None),
            "notif_team_activity.wav",
        )

    def test_whatsapp_sound_filename(self):
        self.assertEqual(
            ios_notification_sound_filename("whatsapp_message_received"),
            "notif_whatsapp.wav",
        )

    def test_tenant_chat_ios_sound_and_collapse(self):
        self.assertEqual(tenant_chat_ios_sound_filename(), "notif_tenant_chat.wav")
        self.assertEqual(tenant_chat_apns_collapse_id(42), "tenant_chat_42")
        self.assertIsNone(tenant_chat_apns_collapse_id(""))
