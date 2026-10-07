#!/usr/bin/env python3
"""Local checks for mailbox storage, sender routing, and request pacing."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kivra.api import KivraApiClient
from kivra.letters import LetterFetcher
from kivra_sync import create_company_store
from storage.company_archive import CompanyArchive
from storage.filesystem import FileSystemStoreProvider
from storage.mailbox import mailbox_folder_name


class FakeResponse:
    status_code = 200
    content = b'%PDF-1.4 mock document'

    def json(self):
        return {'data': {'ok': True}}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        return FakeResponse()

    def get(self, url, **kwargs):
        self.calls.append(('get', url, kwargs))
        return FakeResponse()


class FakeLetterApi:
    def graphql_query(self, operation, query, variables):
        assert operation == 'ContentList'
        assert variables['senderKey'] == 'sender-1'
        return {'data': {'contents': {
            'existsMore': False,
            'list': [{
                'key': 'content-1',
                'receivedAt': '2025-05-14T09:30:00Z',
                'subject': 'Invoice 42',
                'sender': {'key': 'sender-1', 'name': 'Other Sender'},
            }],
        }}}

    def get_content_details(self, content_key):
        assert content_key == 'content-1'
        return {'parts': [{'content_type': 'application/pdf', 'key': 'part-1'}]}

    def get_content_file(self, content_key, file_key):
        assert (content_key, file_key) == ('content-1', 'part-1')
        return b'%PDF-1.4 mock document'


class CompanyMailboxStorageTests(unittest.TestCase):
    def test_mailbox_name_is_a_single_safe_component(self):
        self.assertEqual(mailbox_folder_name('BeeMobile AB'), 'BeeMobile AB')
        self.assertEqual(mailbox_folder_name(r'..BeeMobile/AB'), 'BeeMobileAB')
        with self.assertRaises(ValueError):
            mailbox_folder_name('..')

    def test_company_store_uses_upstream_layout_under_mailbox_name(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_company_store(directory, 'BeeMobile AB')
            self.assertIsInstance(store, FileSystemStoreProvider)
            self.assertEqual(Path(store.base_dir), Path(directory) / 'BeeMobile AB')
            self.assertTrue((Path(directory) / 'BeeMobile AB' / 'Letters').is_dir())

    def test_local_dated_layout_remains_available_on_feature_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_company_store(directory, 'BeeMobile AB', 'sender-1', layout='dated')
            self.assertIsInstance(store, CompanyArchive)
            self.assertEqual(store.base_dir, Path(directory) / 'BeeMobile AB')

    def test_selected_sender_is_stored_in_upstream_filesystem_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            store = create_company_store(directory, 'BeeMobile AB')
            stats = LetterFetcher(FakeLetterApi(), store).fetch_letters(sender_key='sender-1')

            self.assertEqual(stats['letters_total'], 1)
            self.assertEqual(stats['letters_stored'], 1)
            expected = (Path(directory) / 'BeeMobile AB' / 'Letters' / 'Other_Sender' /
                        '2025-05-14_Other_Sender_content-1.pdf')
            self.assertEqual(expected.read_bytes(), b'%PDF-1.4 mock document')
            listing = (Path(directory) / 'BeeMobile AB' / 'Letters' / 'json' / 'letters.json')
            self.assertEqual(json.loads(listing.read_text(encoding='utf-8'))[0]['key'], 'content-1')

    def test_html_is_preserved_when_native_pdf_renderer_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileSystemStoreProvider(str(Path(directory) / 'BeeMobile AB'))
            metadata = {'type': 'letter', 'date': '2025-05-14',
                        'sender_name': 'Other Sender', 'key': 'content-2',
                        'content_type': 'text/html'}
            self.assertTrue(store.store('<html><body>Mail</body></html>', metadata))
            html = (Path(directory) / 'BeeMobile AB' / 'Letters' / 'Other_Sender' /
                    '2025-05-14_Other_Sender_content-2_html.html')
            self.assertEqual(html.read_text(encoding='utf-8'), '<html><body>Mail</body></html>')
            self.assertTrue(store.exists(metadata))

    def test_api_uses_company_headers_and_company_raw_file_route(self):
        client = KivraApiClient('fake-token', 'company-key', 'company', 'user-key')
        session = FakeSession()
        client.session = session
        client.get_content_file('content/1', 'part/1')

        method, url, kwargs = session.calls[0]
        self.assertEqual(method, 'get')
        self.assertEqual(url, 'https://app.api.kivra.com/v4/company/company-key/content/content%2F1/parts/part%2F1/raw')
        self.assertEqual(kwargs['headers']['X-Actor-Type'], 'company')
        self.assertEqual(kwargs['headers']['X-Session-Actor'], 'user_user-key')

    def test_requests_are_spaced_and_floor_is_one_second(self):
        client = KivraApiClient('fake-token', 'user-key', request_interval_seconds=0)
        session = FakeSession()
        client.session = session
        with patch('kivra.api.time.monotonic', side_effect=[0.0, 0.0, 0.1, 0.1]), \
             patch('kivra.api.time.sleep') as sleep:
            client.graphql_query('One', 'query One { ok }', {})
            client.graphql_query('Two', 'query Two { ok }', {})
        self.assertEqual(len(session.calls), 2)
        sleep.assert_called_once_with(0.9)


if __name__ == '__main__':
    unittest.main()
