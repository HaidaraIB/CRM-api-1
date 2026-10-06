"""Meta Inbox sender profile enrichment."""

from datetime import timedelta

import pytest
import requests
from django.utils import timezone

from integrations.models import SocialContact, SocialChannel
from integrations.services.meta_inbox_profile import (
    compose_messenger_display_name,
    ensure_contact_profile,
    fetch_contact_profile,
    needs_profile_refresh,
    _parse_profile,
)

pytestmark = pytest.mark.django_db


class TestNameComposition:
    def test_first_and_last(self):
        assert compose_messenger_display_name('Ahmed', 'Hassan') == 'Ahmed Hassan'

    def test_first_only(self):
        assert compose_messenger_display_name('Ahmed', '') == 'Ahmed'

    def test_fallback_name_field(self):
        assert compose_messenger_display_name('', '', 'Solo Name') == 'Solo Name'


class TestParseProfile:
    def test_instagram_payload(self):
        parsed = _parse_profile(
            SocialChannel.INSTAGRAM,
            {'name': 'Sara Ali', 'username': 'sara.shop', 'profile_pic': 'https://cdn/p.jpg'},
        )
        assert parsed['name'] == 'Sara Ali'
        assert parsed['username'] == 'sara.shop'
        assert parsed['profile_pic_url'] == 'https://cdn/p.jpg'

    def test_messenger_payload(self):
        parsed = _parse_profile(
            SocialChannel.MESSENGER,
            {'first_name': 'Ahmed', 'last_name': 'Hassan', 'profile_pic': 'https://cdn/m.jpg'},
        )
        assert parsed['name'] == 'Ahmed Hassan'
        assert parsed['username'] == ''

    def test_messenger_payload_name_only(self):
        parsed = _parse_profile(
            SocialChannel.MESSENGER,
            {'name': 'Solo Name', 'profile_pic': 'https://cdn/m.jpg'},
        )
        assert parsed['name'] == 'Solo Name'

    def test_error_payload_returns_empty(self):
        assert _parse_profile(SocialChannel.MESSENGER, {'error': {'code': 100}}) == {}


class TestStaleness:
    def test_new_contact_needs_refresh(self, meta_inbox_connection):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000100',
        )
        assert needs_profile_refresh(contact) is True

    def test_ok_within_7_days_skips(self, meta_inbox_connection):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000101',
            name='Fresh',
            profile_pic_url='/media/x.jpg',
            avatar_path='social_profiles/1/x.jpg',
            profile_fetched_at=timezone.now() - timedelta(days=2),
            profile_fetch_status='ok',
        )
        assert needs_profile_refresh(contact) is False

    def test_ok_older_than_7_days_refreshes(self, meta_inbox_connection):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000102',
            name='Stale',
            profile_pic_url='/media/x.jpg',
            avatar_path='social_profiles/1/x.jpg',
            profile_fetched_at=timezone.now() - timedelta(days=8),
            profile_fetch_status='ok',
        )
        assert needs_profile_refresh(contact) is True

    def test_failed_retries_after_24h(self, meta_inbox_connection):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000103',
            profile_fetched_at=timezone.now() - timedelta(hours=2),
            profile_fetch_status='failed',
        )
        assert needs_profile_refresh(contact) is False
        contact.profile_fetched_at = timezone.now() - timedelta(hours=25)
        assert needs_profile_refresh(contact) is True


class TestManualNameProtection:
    def test_does_not_overwrite_manual_name(self, meta_inbox_connection, monkeypatch):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000200',
            name='Agent Override',
            name_manually_set=True,
        )

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            lambda *a, **k: {
                'parsed': {
                    'name': 'Graph Name',
                    'username': '',
                    'profile_pic_url': '',
                },
                'error': None,
            },
        )

        ensure_contact_profile(contact, meta_inbox_connection, force=True)
        contact.refresh_from_db()
        assert contact.name == 'Agent Override'
        assert contact.name_manually_set is True
        assert contact.profile_fetch_status == 'ok'


class TestGraphErrorHandling:
    def test_error_object_marks_failed(self, meta_inbox_connection, monkeypatch):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000300',
        )

        class FakeResponse:
            ok = False
            status_code = 400

            @staticmethod
            def json():
                return {
                    'error': {
                        'code': 100,
                        'message': '(#100) No profile available',
                    }
                }

        monkeypatch.setattr(requests, 'get', lambda *a, **k: FakeResponse())

        result = fetch_contact_profile(contact, page_token='page-token-xyz')
        assert result['parsed'] == {}
        assert result['error']['code'] == 100

        ensure_contact_profile(contact, meta_inbox_connection, force=True)
        contact.refresh_from_db()
        assert contact.profile_fetch_status == 'failed'
        assert contact.profile_fetched_at is not None
        assert contact.display_name.startswith('Messenger user')


class TestEnsureContactProfile:
    def test_instagram_profile_is_fetched_on_first_message(
        self, meta_inbox_connection, monkeypatch
    ):
        from integrations.services.meta_inbox_ingest import process_entry

        def fake_fetch(contact, *, page_token):
            assert page_token == 'page-token-xyz'
            assert contact.external_id == '4900000000000001'
            return {
                'parsed': {
                    'name': 'Sara Ali',
                    'username': 'sara.shop',
                    'profile_pic_url': '',
                },
                'error': None,
            }

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            fake_fetch,
        )

        entry = {
            'id': meta_inbox_connection.ig_user_id,
            'messaging': [
                {
                    'sender': {'id': '4900000000000001'},
                    'recipient': {'id': meta_inbox_connection.ig_user_id},
                    'timestamp': 1700000000000,
                    'message': {'mid': 'mid-profile-1', 'text': 'hello'},
                }
            ],
        }
        assert process_entry('instagram', entry) == 1

        contact = SocialContact.objects.get(external_id='4900000000000001')
        assert contact.name == 'Sara Ali'
        assert contact.username == 'sara.shop'
        assert contact.display_name == 'Sara Ali'
        assert contact.profile_fetched_at is not None
        assert contact.profile_fetch_status == 'ok'

    def test_messenger_profile_is_fetched_on_first_message(
        self, meta_inbox_connection, monkeypatch
    ):
        from integrations.services.meta_inbox_ingest import process_entry

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            lambda contact, *, page_token: {
                'parsed': {
                    'name': 'Ahmed Hassan',
                    'username': '',
                    'profile_pic_url': '',
                },
                'error': None,
            },
        )

        entry = {
            'id': meta_inbox_connection.page_id,
            'messaging': [
                {
                    'sender': {'id': '5900000000000001'},
                    'recipient': {'id': meta_inbox_connection.page_id},
                    'timestamp': 1700000000000,
                    'message': {'mid': 'mid-profile-m1', 'text': 'hi'},
                }
            ],
        }
        assert process_entry('page', entry) == 1

        contact = SocialContact.objects.get(
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000001',
        )
        assert contact.name == 'Ahmed Hassan'
        assert contact.display_name == 'Ahmed Hassan'

    def test_profile_fetch_runs_once(self, meta_inbox_connection, monkeypatch):
        from integrations.services.meta_inbox_ingest import process_entry

        calls = {'count': 0}

        def counting_fetch(contact, *, page_token):
            calls['count'] += 1
            return {
                'parsed': {'name': 'Once Only', 'username': '', 'profile_pic_url': ''},
                'error': None,
            }

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            counting_fetch,
        )

        entry = {
            'id': meta_inbox_connection.ig_user_id,
            'messaging': [
                {
                    'sender': {'id': '4900000000000001'},
                    'recipient': {'id': meta_inbox_connection.ig_user_id},
                    'timestamp': 1700000000000,
                    'message': {'mid': 'mid-a', 'text': 'one'},
                }
            ],
        }
        process_entry('instagram', entry)
        entry['messaging'][0]['message']['mid'] = 'mid-b'
        entry['messaging'][0]['message']['text'] = 'two'
        process_entry('instagram', entry)

        assert calls['count'] == 1

    def test_graph_failure_stamps_failed_status(
        self, meta_inbox_connection, monkeypatch
    ):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.INSTAGRAM,
            external_id='4900000000000999',
        )

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            lambda *args, **kwargs: {
                'parsed': {},
                'error': {'code': 100, 'message': 'fail'},
            },
        )

        ensure_contact_profile(contact, meta_inbox_connection)
        contact.refresh_from_db()
        assert contact.profile_fetched_at is not None
        assert contact.profile_fetch_status == 'failed'
        assert 'Instagram user' in contact.display_name

    def test_fetch_contact_profile_calls_graph(self, meta_inbox_connection, monkeypatch):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.INSTAGRAM,
            external_id='4900000000000888',
        )

        class FakeResponse:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {
                    'name': 'Graph Name',
                    'username': 'graph_user',
                    'profile_pic': 'https://cdn.example/g.jpg',
                }

        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured['url'] = url
            captured['params'] = params
            return FakeResponse()

        monkeypatch.setattr(requests, 'get', fake_get)

        result = fetch_contact_profile(contact, page_token='page-token-xyz')
        parsed = result['parsed']
        assert parsed['name'] == 'Graph Name'
        assert parsed['username'] == 'graph_user'
        assert captured['url'].endswith('/4900000000000888')
        assert 'profile_pic' in captured['params']['fields']
        assert 'name' in captured['params']['fields']
        assert 'profile_picture_url' not in captured['params']['fields']

    def test_messenger_fields_exclude_profile_picture_url(
        self, meta_inbox_connection, monkeypatch
    ):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000888',
        )

        class FakeResponse:
            ok = True
            status_code = 200

            @staticmethod
            def json():
                return {
                    'name': 'Graph Full',
                    'first_name': 'Graph',
                    'last_name': 'Full',
                    'profile_pic': 'https://cdn.example/m.jpg',
                }

        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured['params'] = params
            return FakeResponse()

        monkeypatch.setattr(requests, 'get', fake_get)

        result = fetch_contact_profile(contact, page_token='page-token-xyz')
        assert result['parsed']['name'] == 'Graph Full'
        assert set(captured['params']['fields'].split(',')) == {
            'first_name',
            'last_name',
            'profile_pic',
        }

    def test_absolute_profile_pic_url_prefixes_api_base(self, settings):
        from integrations.services.meta_inbox_profile import absolute_profile_pic_url

        settings.API_BASE_URL = 'https://api.example.com'
        assert absolute_profile_pic_url('/media/x.jpg') == 'https://api.example.com/media/x.jpg'
        assert absolute_profile_pic_url('https://cdn/p.jpg') == 'https://cdn/p.jpg'

    def test_display_name_includes_external_id_suffix(self, meta_inbox_connection):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000009999',
        )
        assert contact.display_name == 'Messenger user · 9999'
