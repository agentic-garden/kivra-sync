#!/usr/bin/python3
# -*- coding: utf-8 -*-

import sys
import os
import logging
import argparse
import base64
import tempfile
import getpass
from collections import Counter

from __version__ import __version__
from kivra.auth import KivraAuth
from kivra.api import KivraApiClient
from kivra.receipts import ReceiptFetcher
from kivra.letters import LetterFetcher
from storage.company_archive import CompanyArchive
from storage.mailbox import mailbox_folder_name
from interaction.local import LocalInteractionProvider
from interaction.ntfy import NtfyInteractionProvider
from interaction.web import WebInteractionProvider


def create_company_store(base_dir, mailbox_name, sender_key=None,
                         layout='standard', dry_run=False):
    mailbox_dir = os.path.join(base_dir, mailbox_folder_name(mailbox_name))
    if layout == 'dated':
        return CompanyArchive(mailbox_dir, sender_key, dry_run=dry_run)
    if layout != 'standard':
        raise ValueError('Unknown company storage layout')
    from storage.filesystem import FileSystemStoreProvider
    return FileSystemStoreProvider(mailbox_dir, dry_run=dry_run)


def fetch_documents(args, interaction_provider, document_store, temp_dir):
    """
    Fetch documents from Kivra.
    
    Args:
        args: Command line arguments
        interaction_provider: Interaction provider to use
        document_store: Document storage provider to use
        temp_dir: Temporary directory for QR codes and other files
        
    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    try:
        # Authenticate with Kivra
        auth = KivraAuth(temp_dir, interaction_provider)
        token_info = auth.authenticate(args.ssn)
        
        # Extract tokens
        access_token = token_info['access_token']
        personal_user_id = token_info['actor_key']
        actor_key = getpass.getpass('Company actor key: ').strip() if args.company else personal_user_id
        if not actor_key:
            raise ValueError('Company actor key is required')
        
        # Initialize API client
        api_client = KivraApiClient(access_token, actor_key,
                                    actor_type='company' if args.company else 'user',
                                    personal_user_id=personal_user_id,
                                    request_interval_seconds=args.request_interval)
        if args.list_senders:
            letters = LetterFetcher(api_client, document_store).list_letters()
            counts = Counter(((item.get('sender') or {}).get('key'),
                              (item.get('sender') or {}).get('name')) for item in letters)
            for (key, name), count in sorted(counts.items(), key=lambda entry: (entry[0][1] or '', entry[0][0] or '')):
                print(f'{name or "Unknown"}\t{key or "Missing key"}\t{count}')
            return 0
        
        # Initialize statistics
        stats = {
            'receipts_total': 0,
            'receipts_fetched': 0,
            'receipts_stored': 0,
            'letters_total': 0,
            'letters_fetched': 0,
            'letters_stored': 0
        }
        
        # Fetch receipts if enabled
        if args.fetch_receipts:
            receipt_fetcher = ReceiptFetcher(api_client, document_store)
            receipt_stats = receipt_fetcher.fetch_receipts(max_count=None if args.max_receipts == 0 else args.max_receipts)
            stats.update(receipt_stats)
        
        # Fetch letters if enabled
        if args.fetch_letters:
            letter_fetcher = LetterFetcher(api_client, document_store)
            letter_stats = letter_fetcher.fetch_letters(max_count=None if args.max_letters == 0 else args.max_letters,
                                                        sender_key=args.sender_key)
            stats.update(letter_stats)
            if (args.company and args.company_layout == 'dated' and
                    not args.dry_run and document_store.sender_dir):
                from storage.company_index import rebuild_index
                rebuild_index(document_store.sender_dir)
        
        # Report completion
        interaction_provider.report_completion(stats)
        
        return 0
    except Exception as e:
        logging.error("%s: %s", type(e).__name__, str(e) if isinstance(e, (ValueError, RuntimeError)) else 'operation failed')
        return 1

def main():
    """Main function to fetch receipts and letters from Kivra."""
    
    # Configure logging
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description=f'Fetch receipts and letters from Kivra. (version {__version__})',
        prog='kivra-sync'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('ssn', nargs='?', help='Personal identity number (YYYYMMDDXXXX); prompted if omitted')
    parser.add_argument('--company', action='store_true', help='Use a company actor; prompts for its key after BankID')
    parser.add_argument('--mailbox-name', help='Company/mailbox name used as a local storage subfolder')
    parser.add_argument('--company-layout', choices=['standard', 'dated'], default='standard',
                        help='Company storage layout: upstream filesystem layout or local year/month archive')
    parser.add_argument('--list-senders', action='store_true', help='List observed inbox sender names, keys and counts')
    parser.add_argument('--sender-key', help='Sync only this sender key from the current actor mailbox')
    
    # Storage provider selection
    parser.add_argument('--storage-provider', choices=['filesystem', 'paperless'], default='filesystem',
                        help='Storage provider to use (default: filesystem)')
    parser.add_argument('--root-dir', '--base-dir', dest='root_dir',
                        help='Root directory for local documents (default: current working directory; --base-dir alias)')
    parser.add_argument('--request-interval', type=float, default=1.2,
                        help='Minimum seconds between Kivra API requests (minimum 1.0; default: 1.2)')
    
    # Interaction provider selection
    parser.add_argument('--interaction-provider', choices=['local', 'ntfy', 'web'], default='local',
                        help='Interaction provider to use (default: local)')
    
    # ntfy provider options
    parser.add_argument('--ntfy-topic', help='ntfy topic to send notifications to')
    parser.add_argument('--ntfy-server', default='https://ntfy.sh', help='ntfy server URL (default: https://ntfy.sh)')
    parser.add_argument('--ntfy-user', help='ntfy username for authentication')
    parser.add_argument('--ntfy-pass', help='ntfy password for authentication')
    parser.add_argument('--trigger-message', default='run now', 
                        help='Message that triggers the script when using a listening interaction provider (default: "run now")')
    
    # Web provider options
    parser.add_argument('--web-port', type=int, default=8080, 
                        help='Port for web interface (default: 8080)')
    parser.add_argument('--web-host', default='0.0.0.0',
                        help='Host for web interface (default: 0.0.0.0)')
    
    # Paperless provider options
    parser.add_argument('--paperless-url', help='Paperless API URL (e.g., http://localhost:8000/api)')
    parser.add_argument('--paperless-token', help='Paperless API token')
    parser.add_argument('--paperless-tags', help='Comma-separated list of tags to apply to all documents')
    parser.add_argument('--dry-run', action='store_true', help='Do not actually store documents, just simulate')
    
    # Fetch options
    parser.add_argument('--fetch-receipts', action='store_true', default=True, help='Fetch receipts')
    parser.add_argument('--no-fetch-receipts', action='store_false', dest='fetch_receipts', help='Do not fetch receipts')
    parser.add_argument('--fetch-letters', action='store_true', default=True, help='Fetch letters')
    parser.add_argument('--no-fetch-letters', action='store_false', dest='fetch_letters', help='Do not fetch letters')
    parser.add_argument('--max-receipts', type=int, default=0, help='Maximum number of receipts to fetch (default: 0, 0 for unlimited)')
    parser.add_argument('--max-letters', type=int, default=0, help='Maximum number of letters to fetch (default: 0, 0 for unlimited)')
    
    args = parser.parse_args()
    if args.request_interval < 1.0:
        parser.error('--request-interval must be at least 1.0 second')
    if args.company:
        if args.interaction_provider != 'local' or args.storage_provider != 'filesystem':
            parser.error('Company mode requires local interaction and filesystem storage')
        if not args.root_dir:
            parser.error('Company mode requires --root-dir for the archive destination')
        if not args.list_senders and not args.sender_key:
            parser.error('Company mode requires --list-senders or --sender-key')
        if not args.mailbox_name:
            args.mailbox_name = input('Company/mailbox name for local archive: ').strip()
        try:
            args.mailbox_name = mailbox_folder_name(args.mailbox_name)
        except ValueError as exc:
            parser.error(str(exc))
        args.fetch_receipts = False
    elif args.mailbox_name or args.company_layout != 'standard':
        parser.error('--mailbox-name and --company-layout require --company')
    if args.sender_key and not args.company:
        parser.error('--sender-key requires --company')
    if not args.ssn:
        args.ssn = getpass.getpass('Personal identity number (YYYYMMDDXXXX): ').strip()
    
    # Create temp directory for QR codes and other temporary files
    # Prefer env overrides and OS temp; avoid writing into read-only installs
    script_dir = os.path.dirname(os.path.abspath(__file__))
    temp_base = (
        os.environ.get("XDG_RUNTIME_DIR")
        or tempfile.gettempdir()
    )
    temp_dir = os.path.join(temp_base, "kivra-sync")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Initialize the document storage provider
    if args.storage_provider == 'filesystem':
        # Use root_dir if provided, otherwise use the current directory.
        root_dir = (
            args.root_dir
            if args.root_dir
            else os.getcwd()
        )
        if args.company:
            document_store = create_company_store(root_dir, args.mailbox_name,
                                                  args.sender_key, args.company_layout,
                                                  dry_run=args.dry_run)
        else:
            # Import lazily: the personal filesystem provider imports WeasyPrint,
            # which requires native GTK libraries not needed for company archives.
            from storage.filesystem import FileSystemStoreProvider
            document_store = FileSystemStoreProvider(os.path.join(root_dir, args.ssn), dry_run=args.dry_run)
    elif args.storage_provider == 'paperless':
        # Check if required paperless options are provided
        if not args.paperless_url or not args.paperless_token:
            parser.error("--paperless-url and --paperless-token are required when using the paperless storage provider")
        
        from storage.paperless import PaperlessNgxStoreProvider
        tags = args.paperless_tags.split(',') if args.paperless_tags else None
        document_store = PaperlessNgxStoreProvider(
            api_url=args.paperless_url,
            api_token=args.paperless_token,
            tags=tags,
            dry_run=args.dry_run
        )
    
    # Initialize the interaction provider
    if args.interaction_provider == 'local':
        interaction_provider = LocalInteractionProvider()
    elif args.interaction_provider == 'ntfy':
        if not args.ntfy_topic:
            parser.error("--ntfy-topic is required when using the ntfy interaction provider")
        
        # Set up authentication headers if provided
        ntfy_headers = {}
        if args.ntfy_user and args.ntfy_pass:
            auth_str = f"{args.ntfy_user}:{args.ntfy_pass}"
            encoded_auth = base64.b64encode(auth_str.encode()).decode()
            ntfy_headers["Authorization"] = f"Basic {encoded_auth}"
        
        interaction_provider = NtfyInteractionProvider(
            topic=args.ntfy_topic,
            server=args.ntfy_server,
            headers=ntfy_headers,
            trigger_message=args.trigger_message
        )
    elif args.interaction_provider == 'web':
        interaction_provider = WebInteractionProvider(
            port=args.web_port,
            host=args.web_host
        )
    
    # Check if the provider can listen
    if interaction_provider.can_listen:
        # Start listening
        print(f"Listening for triggers via {args.interaction_provider}...")
        interaction_provider.listen(lambda: fetch_documents(args, interaction_provider, document_store, temp_dir), temp_dir=temp_dir)
    else:
        # Execute immediately
        exit_code = fetch_documents(args, interaction_provider, document_store, temp_dir)
        sys.exit(exit_code)

if __name__ == "__main__":
    main()
