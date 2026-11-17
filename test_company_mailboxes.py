"""Focused checks for the company mailbox adapter and local namespace."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kivra.api import KivraApiClient
from kivra.company import CompanyApiClient, CompanyLetterFetcher, fetch_company_mailboxes
from kivra.models import KivraLetter
from kivra_sync import fetch_documents, main
from interaction.web import WebInteractionProvider
from storage.filesystem import FileSystemStoreProvider
from storage.paperless import PaperlessNgxStoreProvider


class Response:
    def __init__(self, *, data=None, content=b'', status_code=200):
        self.data = data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self.data


class Session:
    def __init__(self, detail, raw, raw_status=200):
        self.detail = detail
        self.raw = raw
        self.raw_status = raw_status
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(('post', url, kwargs))
        return Response(data=self.detail)

    def get(self, url, **kwargs):
        self.calls.append(('get', url, kwargs))
        return Response(content=self.raw, status_code=self.raw_status)


class CompanyApiTests(unittest.TestCase):
    def test_company_detail_and_returned_raw_path(self):
        client = CompanyApiClient('token-value', 'personal-id', 'company-id')
        client.session = Session({
            'data': {'content': {'parts': [
                {'name': 'invoice', 'mimeType': 'application/pdf',
                 'path': '/opaque/pdf-part?download=1'},
                {'name': 'message', 'mimeType': 'text/plain',
                 'path': '/opaque/text-part'},
            ]}},
        }, b'Hello company')

        details = client.get_content_details('letter-1')
        self.assertEqual(details['parts'][0]['content_type'], 'application/pdf')
        self.assertEqual(details['parts'][0]['key'], '/opaque/pdf-part?download=1')
        self.assertEqual(details['parts'][1]['body'], 'Hello company')
        self.assertEqual(client.get_content_file('letter-1', details['parts'][0]['key']),
                         b'Hello company')

        operation, _, graphql = client.session.calls[0]
        self.assertEqual(operation, 'post')
        self.assertEqual(graphql['json']['operationName'], 'ContentDetails')
        self.assertEqual(graphql['json']['variables'], {'key': 'letter-1'})
        self.assertIn('contentV2(key: $key)', graphql['json']['query'])
        self.assertEqual(graphql['headers']['Authorization'], 'Bearer token-value')
        self.assertEqual(graphql['headers']['X-Actor-Key'], 'company-id')
        self.assertEqual(graphql['headers']['X-Actor-Type'], 'company')
        self.assertEqual(graphql['headers']['X-Session-Actor'], 'user_personal-id')
        self.assertEqual(
            [call[1] for call in client.session.calls[1:]],
            ['https://app.api.kivra.com/opaque/text-part',
             'https://app.api.kivra.com/opaque/pdf-part?download=1'],
        )
        for operation, _, kwargs in client.session.calls[1:]:
            self.assertEqual(operation, 'get')
            self.assertEqual(kwargs['headers'], {'Authorization': 'token token-value'})

    def test_detail_or_raw_failure_is_visible(self):
        client = CompanyApiClient('token', 'personal', 'company')
        client.session = Session({'data': {'content': None}}, b'')
        with self.assertRaisesRegex(RuntimeError, 'no parts list'):
            client.get_content_details('letter')
        with self.assertRaisesRegex(ValueError, 'Invalid company content path'):
            client.get_content_file('letter', 'https://example.com/file')
        self.assertEqual(len(client.session.calls), 1)
        client.session = Session({}, b'', raw_status=404)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
            client.get_content_file('letter', '/part/path')


class Store:
    def __init__(self, failed_part=False):
        self.failed_part = failed_part
        self.metadata_calls = 0
        self.parts = []
        self.events = []
        self.listings = []
        self.prepared = []

    def prepare_mailbox(self, mailbox):
        self.prepared.append(mailbox)

    def report_listing(self, kind, listing, mailbox=None):
        self.listings.append((kind, mailbox))

    def exists(self, metadata):
        return False

    def store(self, data, metadata):
        self.parts.append(metadata)
        self.events.append('part')
        return not self.failed_part or len(self.parts) == 1

    def report_metadata(self, data, metadata):
        self.metadata_calls += 1
        self.events.append('metadata')
        return True


class Api:
    def graphql_query(self, operation, query, variables):
        return {'data': {'contents': {'existsMore': False, 'list': [
            {'key': 'letter-1', 'receivedAt': '2025-01-02T12:00:00Z',
             'sender': {'name': 'Sender'}}
        ]}}}

    def get_content_details(self, content_key):
        return {'parts': [
            {'content_type': 'application/pdf', 'key': '/pdf'},
            {'content_type': 'text/plain', 'body': 'Letter'},
        ]}

    def get_content_file(self, content_key, file_key):
        return b'%PDF-1.4 document'


class CompanyFetcherTests(unittest.TestCase):
    def test_all_parts_before_metadata_marker(self):
        store = Store()
        stats = CompanyLetterFetcher(Api(), store).fetch_letters()
        self.assertEqual(stats['letters_stored'], 1)
        self.assertEqual(len(store.parts), 2)
        self.assertEqual(store.metadata_calls, 1)
        self.assertEqual(store.events, ['part', 'part', 'metadata'])

    def test_mailbox_is_carried_in_all_metadata(self):
        store = Store()
        CompanyLetterFetcher(Api(), store, mailbox='One AB').fetch_letters()
        self.assertEqual(store.listings, [('letters', 'One AB')])
        self.assertEqual([part['mailbox'] for part in store.parts], ['One AB', 'One AB'])
        self.assertEqual([part['part_index'] for part in store.parts], [0, 1])

    def test_personal_metadata_has_no_mailbox_key(self):
        self.assertNotIn('mailbox', KivraLetter('key', '2025-01-02', 'Sender').get_metadata())

    def test_partial_store_fails_without_metadata_marker(self):
        store = Store(failed_part=True)
        with self.assertRaisesRegex(RuntimeError, 'stored 1 of 2 parts'):
            CompanyLetterFetcher(Api(), store).fetch_letters()
        self.assertEqual(store.metadata_calls, 0)


class CliTests(unittest.TestCase):
    def test_original_cli_and_personal_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            for option in ([], ['--base-dir', directory]):
                argv = ['kivra_sync.py', '199001011234', *option]
                with patch.object(sys, 'argv', argv), \
                     patch('kivra_sync.os.getcwd', return_value=directory), \
                     patch('kivra_sync.fetch_documents', return_value=0) as fetch:
                    with self.assertRaises(SystemExit) as exit_code:
                        main()
                self.assertEqual(exit_code.exception.code, 0)
                args, _, store, _ = fetch.call_args.args
                self.assertEqual(args.base_dir, directory if option else None)
                self.assertEqual(args.fetch_receipts, True)
                self.assertEqual(args.fetch_letters, True)
                self.assertFalse(hasattr(args, 'company_key'))
                self.assertFalse(args.include_company_mailboxes)
                self.assertEqual(Path(store.base_dir), Path(directory) / '199001011234')
                self.assertTrue((Path(store.base_dir) / 'Letters').is_dir())

        argv = ['kivra_sync.py', '199001011234', '--include-company-mailboxes']
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, 'argv', argv), \
             patch('kivra_sync.os.getcwd', return_value=directory), \
             patch('kivra_sync.fetch_documents', return_value=0) as fetch:
            with self.assertRaises(SystemExit):
                main()
        self.assertTrue(fetch.call_args.args[0].include_company_mailboxes)

    def test_original_personal_fetch_order_and_aggregate_completion(self):
        interaction = SimpleNamespace(report_completion=Mock(), report_error=Mock())
        store = Store()
        events = []
        with patch('kivra_sync.KivraAuth') as auth, \
             patch('kivra_sync.LetterFetcher') as personal_fetcher, \
             patch('kivra_sync.ReceiptFetcher') as receipt_fetcher, \
             patch('kivra_sync.fetch_company_mailboxes') as companies:
            auth.return_value.authenticate.return_value = {
                'access_token': 'token', 'actor_key': 'personal-id'
            }
            def receipts(**kwargs):
                events.append('personal receipts')
                return {'receipts_total': 2, 'receipts_fetched': 2, 'receipts_stored': 2}

            def letters(**kwargs):
                events.append('personal letters')
                return {'letters_total': 3, 'letters_fetched': 3, 'letters_stored': 3}

            def company_sync(*args, **kwargs):
                events.append('company letters')
                return ({'letters_total': 6, 'letters_fetched': 4, 'letters_stored': 4}, [])

            receipt_fetcher.return_value.fetch_receipts.side_effect = receipts
            personal_fetcher.return_value.fetch_letters.side_effect = letters
            companies.side_effect = company_sync
            args = SimpleNamespace(ssn='199001011234', fetch_receipts=True,
                                   fetch_letters=True, max_letters=2, max_receipts=0,
                                   include_company_mailboxes=False)
            self.assertEqual(fetch_documents(args, interaction, store, '/tmp'), 0)
            companies.assert_not_called()
            self.assertEqual(events, ['personal receipts', 'personal letters'])

            events.clear()
            args.include_company_mailboxes = True
            self.assertEqual(fetch_documents(args, interaction, store, '/tmp'), 0)
            self.assertEqual(events, ['personal receipts', 'personal letters', 'company letters'])
            client = personal_fetcher.call_args.args[0]
            self.assertIs(type(client), KivraApiClient)
            self.assertEqual(client.get_headers()['X-Session-Actor'], 'user_personal-id')
            self.assertIs(personal_fetcher.call_args.args[1], store)
            self.assertIs(receipt_fetcher.call_args.args[1], store)
            self.assertEqual(personal_fetcher.return_value.fetch_letters.call_args.kwargs,
                             {'max_count': 2})
            self.assertEqual(companies.call_args.args[1:], (store,))
            self.assertEqual(companies.call_args.kwargs, {'max_count': 2})
            self.assertEqual(interaction.report_completion.call_args.args[0], {
                'receipts_total': 2, 'receipts_fetched': 2, 'receipts_stored': 2,
                'letters_total': 9, 'letters_fetched': 7, 'letters_stored': 7,
            })

            companies.return_value = ({'letters_total': 0, 'letters_fetched': 0,
                                       'letters_stored': 0}, ['failed company'])
            companies.side_effect = None
            interaction.report_completion.reset_mock()
            self.assertEqual(fetch_documents(args, interaction, store, '/tmp'), 1)
            interaction.report_completion.assert_not_called()
            interaction.report_error.assert_called_once()

            web = WebInteractionProvider(host='127.0.0.1')
            self.assertEqual(fetch_documents(args, web, store, '/tmp'), 1)
            self.assertEqual(web.current_state['status'], 'error')

            companies.side_effect = RuntimeError('inbox discovery failed')
            self.assertEqual(fetch_documents(args, web, store, '/tmp'), 1)
            self.assertEqual(web.current_state['status'], 'error')

            args.fetch_letters = False
            companies.reset_mock()
            companies.side_effect = None
            self.assertEqual(fetch_documents(args, interaction, store, '/tmp'), 0)
            companies.assert_not_called()


class DiscoveryTests(unittest.TestCase):
    def _personal_client(self, inboxes):
        client = KivraApiClient('token', 'personal-id')
        client.graphql_query = Mock(return_value={'data': {'inboxes': inboxes}})
        return client

    def test_three_company_mailboxes_are_separate_and_limit_per_mailbox(self):
        inboxes = [
            {'key': 'personal-id', 'name': 'Personal', 'type': 'user'},
            {'key': 'company-1', 'name': 'One AB', 'type': 'company'},
            {'key': 'company-2', 'name': 'Two AB', 'type': 'company'},
            {'key': 'company-3', 'name': 'Three AB', 'type': 'non-user-fixture'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            personal_store = FileSystemStoreProvider(
                str(Path(directory) / '199001011234'), dry_run=True
            )
            with patch('kivra.company.CompanyLetterFetcher') as fetcher:
                fetcher.return_value.fetch_letters.return_value = {
                    'letters_total': 5, 'letters_fetched': 2, 'letters_stored': 2
                }
                totals, errors = fetch_company_mailboxes(
                    self._personal_client(inboxes), personal_store, max_count=2,
                )
            self.assertEqual(errors, [])
            self.assertEqual(totals, {'letters_total': 15,
                                      'letters_fetched': 6, 'letters_stored': 6})
            self.assertEqual(fetcher.call_count, 3)
            self.assertEqual(fetcher.return_value.fetch_letters.call_count, 3)
            for call in fetcher.return_value.fetch_letters.call_args_list:
                self.assertEqual(call.kwargs, {'max_count': 2})
            for call, name, key in zip(fetcher.call_args_list,
                                       ('One AB', 'Two AB', 'Three AB'),
                                       ('company-1', 'company-2', 'company-3')):
                client, store = call.args
                self.assertIsInstance(client, CompanyApiClient)
                self.assertEqual(client.company_key, key)
                self.assertEqual(client.get_headers()['X-Session-Actor'], 'user_personal-id')
                self.assertIs(store, personal_store)
                self.assertEqual(call.kwargs, {'mailbox': name})
                self.assertTrue((Path(directory) / name / 'Letters').is_dir())
            self.assertTrue((Path(directory) / '199001011234' / 'Letters').is_dir())

    def test_zero_companies_and_invalid_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileSystemStoreProvider(str(Path(directory) / '199001011234'))
            personal = self._personal_client([
                {'key': 'personal-id', 'name': 'Personal', 'type': 'user'}
            ])
            self.assertEqual(fetch_company_mailboxes(personal, store),
                             ({'letters_total': 0, 'letters_fetched': 0,
                               'letters_stored': 0}, []))
            personal.graphql_query.return_value = {'data': {}}
            with self.assertRaisesRegex(RuntimeError, 'inbox list'):
                fetch_company_mailboxes(personal, store)

    def test_any_provider_is_reused_and_prepared(self):
        store = Store()
        inboxes = [{'key': 'company-1', 'name': 'One AB', 'type': 'company'}]
        with patch('kivra.company.CompanyLetterFetcher') as fetcher:
            fetcher.return_value.fetch_letters.return_value = {
                'letters_total': 0, 'letters_fetched': 0, 'letters_stored': 0
            }
            totals, errors = fetch_company_mailboxes(self._personal_client(inboxes), store)
        self.assertEqual(errors, [])
        self.assertEqual(totals['letters_total'], 0)
        self.assertIs(fetcher.call_args.args[1], store)
        self.assertEqual(fetcher.call_args.kwargs, {'mailbox': 'One AB'})
        self.assertEqual(store.prepared, ['One AB'])

    def test_collision_and_mailbox_error_do_not_stop_later_company(self):
        inboxes = [
            {'key': 'bad', 'name': '199001011234', 'type': 'company'},
            {'key': 'one', 'name': 'Same AB', 'type': 'company'},
            {'key': 'two', 'name': 'Same AB', 'type': 'company'},
            {'key': 'three', 'name': 'Last AB', 'type': 'company'},
        ]
        with tempfile.TemporaryDirectory() as directory:
            store = FileSystemStoreProvider(str(Path(directory) / '199001011234'))
            with patch('kivra.company.CompanyLetterFetcher') as fetcher:
                fetcher.return_value.fetch_letters.side_effect = [
                    RuntimeError('first company failed'),
                    {'letters_total': 1, 'letters_fetched': 1, 'letters_stored': 1},
                ]
                totals, errors = fetch_company_mailboxes(
                    self._personal_client(inboxes), store
                )
            self.assertEqual(fetcher.call_count, 2)
            self.assertEqual(fetcher.return_value.fetch_letters.call_count, 2)
            self.assertEqual(len(errors), 3)
            self.assertEqual(totals['letters_stored'], 1)
            self.assertFalse((Path(directory) / 'Same AB' / 'Letters' / 'json' /
                              'letters.json').exists())
            self.assertTrue((Path(directory) / 'Last AB' / 'Letters').is_dir())


class FileSystemMailboxTests(unittest.TestCase):
    def test_company_documents_and_listing_go_to_company_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileSystemStoreProvider(str(Path(directory) / '199001011234'))
            store.prepare_mailbox('One AB')
            metadata = KivraLetter('key-1', '2025-01-02', 'Sender',
                                   mailbox='One AB').get_metadata('text/plain')
            self.assertTrue(store.store('Hello', metadata))
            self.assertTrue(store.exists(metadata))
            store.report_listing('letters', ['company'], mailbox='One AB')
            store.report_listing('letters', ['personal'])

            company = Path(directory) / 'One AB' / 'Letters'
            personal = Path(directory) / '199001011234' / 'Letters'
            self.assertTrue((company / 'Sender' / '2025-01-02_Sender_key-1.txt').is_file())
            self.assertFalse((personal / 'Sender').exists())
            self.assertEqual((company / 'json' / 'letters.json').read_text().split(),
                             ['[', '"company"', ']'])
            self.assertEqual((personal / 'json' / 'letters.json').read_text().split(),
                             ['[', '"personal"', ']'])
            self.assertFalse(store.exists(KivraLetter('key-1', '2025-01-02', 'Sender')
                                          .get_metadata('text/plain')))

    def test_folder_collisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FileSystemStoreProvider(str(Path(directory) / '199001011234'))
            with self.assertRaisesRegex(ValueError, 'personal folder'):
                store.prepare_mailbox('199001011234')
            with self.assertRaisesRegex(ValueError, 'no usable folder name'):
                store.prepare_mailbox('...')
            store.prepare_mailbox('Foo AB')
            with self.assertRaisesRegex(ValueError, "collides with mailbox 'Foo AB'"):
                store.prepare_mailbox('Foo: AB')
            # A repeated sync (listening mode) prepares the same mailbox again
            store.prepare_mailbox('Foo AB')


class PaperlessMailboxTests(unittest.TestCase):
    def _store(self):
        store = PaperlessNgxStoreProvider.__new__(PaperlessNgxStoreProvider)
        store.api_url = 'http://paperless/api'
        store.tags = ['kivra']
        store.tag_ids = [1]
        store.dry_run = False
        store._mailbox_tag_ids = {}
        store.session = Mock()
        store.session.get.return_value = Response(data={'count': 1, 'results': [{'id': 7}]})
        store.session.post.return_value = Response(data={})
        store._get_correspondent_id = Mock(return_value=None)
        store._get_document_type_id = Mock(return_value=None)
        return store

    def test_company_documents_get_mailbox_tag(self):
        store = self._store()
        store.prepare_mailbox('One & Two AB')
        store.session.get.assert_called_once_with(
            'http://paperless/api/tags/',
            params={'name__iexact': 'kivra-sync: One & Two AB'},
        )
        metadata = KivraLetter('key-1', '2025-01-02', 'Sender',
                               mailbox='One & Two AB').get_metadata('application/pdf')
        self.assertTrue(store.store(b'%PDF', metadata))
        self.assertEqual(store.session.post.call_args.kwargs['data']['tags'], [1, 7])
        self.assertEqual(store.session.get.call_count, 1)

    def test_personal_documents_only_get_global_tags(self):
        store = self._store()
        metadata = KivraLetter('key-1', '2025-01-02', 'Sender').get_metadata('application/pdf')
        self.assertTrue(store.store(b'%PDF', metadata))
        self.assertEqual(store.session.post.call_args.kwargs['data']['tags'], [1])
        store.session.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
