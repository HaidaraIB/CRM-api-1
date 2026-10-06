"""Meta Inbox sender profile enrichment."""

import pytest
import requests

from integrations.models import SocialContact, SocialChannel
from integrations.services.meta_inbox_profile import (
    ensure_contact_profile,
    fetch_contact_profile,
    _parse_profile,
)

pytestmark = pytest.mark.django_db


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


class TestEnsureContactProfile:
    def test_instagram_profile_is_fetched_on_first_message(
        self, meta_inbox_connection, monkeypatch
    ):
        from integrations.services.meta_inbox_ingest import process_entry

        def fake_fetch(contact, *, page_token):
            assert page_token == 'page-token-xyz'
            assert contact.external_id == '4900000000000001'
            return {
                'name': 'Sara Ali',
                'username': 'sara.shop',
                'profile_pic_url': 'https://cdn.example/p.jpg',
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
        assert contact.profile_pic_url == 'https://cdn.example/p.jpg'
        assert contact.display_name == 'Sara Ali'
        assert contact.profile_fetched_at is not None

    def test_messenger_profile_is_fetched_on_first_message(
        self, meta_inbox_connection, monkeypatch
    ):
        from integrations.services.meta_inbox_ingest import process_entry

        monkeypatch.setattr(
            'integrations.services.meta_inbox_profile.fetch_contact_profile',
            lambda contact, *, page_token: {
                'name': 'Ahmed Hassan',
                'username': '',
                'profile_pic_url': 'https://cdn.example/m.jpg',
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
            return {'name': 'Once Only', 'username': '', 'profile_pic_url': ''}

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

    def test_graph_failure_does_not_stamp_profile_fetched_at(
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
            lambda *args, **kwargs: {},
        )

        ensure_contact_profile(contact, meta_inbox_connection)
        contact.refresh_from_db()
        assert contact.profile_fetched_at is None
        assert contact.display_name == 'Instagram user'

    def test_fetch_contact_profile_calls_graph(self, meta_inbox_connection, monkeypatch):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.INSTAGRAM,
            external_id='4900000000000888',
        )

        class FakeResponse:
            ok = True

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

        parsed = fetch_contact_profile(contact, page_token='page-token-xyz')
        assert parsed['name'] == 'Graph Name'
        assert parsed['username'] == 'graph_user'
        assert captured['url'].endswith('/4900000000000888')
        assert 'profile_pic' in captured['params']['fields']
        assert 'name' in captured['params']['fields']
        assert 'profile_picture_url' not in captured['params']['fields']

    def test_messenger_fields_exclude_profile_picture_url(self, meta_inbox_connection, monkeypatch):
        contact = SocialContact.objects.create(
            company=meta_inbox_connection.company,
            connection=meta_inbox_connection,
            channel=SocialChannel.MESSENGER,
            external_id='5900000000000888',
        )

        class FakeResponse:
            ok = True

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

        parsed = fetch_contact_profile(contact, page_token='page-token-xyz')
        assert parsed['name'] == 'Graph Full'
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
