#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import json
import logging
from storage.base import DocumentStoreProvider
from utils.helpers import clean_filename
from utils.pdf import html_to_pdf

class FileSystemStoreProvider(DocumentStoreProvider):
    """Storage provider that stores documents in the file system."""

    TYPE_FOLDERS = {'receipt': 'Receipts', 'letter': 'Letters'}
    
    def __init__(self, base_dir, dry_run=False):
        """
        Initialize with base directory for storage.
        
        Args:
            base_dir (str): Base directory path for the personal mailbox. Company
                mailboxes are stored in sibling directories named after the company.
            dry_run (bool, optional): If True, don't actually store documents, just simulate
        """
        self.base_dir = base_dir
        self.dry_run = dry_run
        # Normalized company folder name -> mailbox name it is reserved for
        self._mailbox_folders = {}
        self._ensure_directories(self.base_dir)
        self.receipts_dir, self.receipts_json_dir = self._type_dirs(self.base_dir, 'receipt')
        self.letters_dir, self.letters_json_dir = self._type_dirs(self.base_dir, 'letter')

    @classmethod
    def _type_dirs(cls, root, doc_type):
        """Return (content_dir, json_dir) for a document type under a mailbox root."""
        type_dir = os.path.join(root, cls.TYPE_FOLDERS[doc_type])
        return type_dir, os.path.join(type_dir, "json")

    def _ensure_directories(self, root):
        """Create the Receipts/Letters directory structure under a mailbox root."""
        for doc_type in self.TYPE_FOLDERS:
            for path in self._type_dirs(root, doc_type):
                os.makedirs(path, exist_ok=True)

    @staticmethod
    def _mailbox_folder(mailbox):
        """Folder name for a company mailbox."""
        folder = clean_filename(mailbox, replace='').strip(' .')
        if not folder:
            raise ValueError(f"company mailbox {mailbox!r} has no usable folder name")
        return folder

    def _root_dir(self, mailbox):
        """Root directory for a mailbox; the personal mailbox uses base_dir."""
        if mailbox is None:
            return self.base_dir
        return os.path.join(os.path.dirname(self.base_dir), self._mailbox_folder(mailbox))

    def prepare_mailbox(self, mailbox):
        """
        Reserve a sibling directory for a company mailbox.

        Raises:
            ValueError: If the folder name is unusable or collides with the
                personal folder or another company mailbox
        """
        folder = os.path.normcase(self._mailbox_folder(mailbox))
        if folder == os.path.normcase(os.path.basename(self.base_dir)):
            raise ValueError(f"company mailbox folder {folder!r} collides with the personal folder")
        owner = self._mailbox_folders.setdefault(folder, mailbox)
        if owner != mailbox:
            raise ValueError(f"company mailbox folder {folder!r} collides with mailbox {owner!r}")
        self._ensure_directories(self._root_dir(mailbox))

    def _get_filepath(self, metadata):
        """
        Generate filepath based on metadata.
        
        Args:
            metadata (dict): Document metadata
            
        Returns:
            tuple: (directory_path, json_dir_path, filename_base)
        """
        doc_type = metadata.get('type')
        date = metadata.get('date', 'unknown_date')

        if doc_type == 'receipt':
            name = metadata.get('store_name', 'unknown_store')
        elif doc_type == 'letter':
            name = metadata.get('sender_name', 'unknown_sender')
        else:
            raise ValueError(f"Unknown document type: {doc_type}")

        # Group documents per store/sender
        safe_name = clean_filename(name)
        type_dir, type_json_dir = self._type_dirs(self._root_dir(metadata.get('mailbox')), doc_type)
        content_dir = os.path.join(type_dir, safe_name)
        json_dir = os.path.join(type_json_dir, safe_name)
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(json_dir, exist_ok=True)

        filename_base = f"{date}_{safe_name}_{metadata.get('key')}"

        # Add part index if specified (letters only)
        if doc_type == 'letter' and metadata.get('part_index') is not None:
            filename_base = f"{filename_base}_part{metadata.get('part_index')}"

        return content_dir, json_dir, filename_base
    
    def report_listing(self, doc_type, listing, mailbox=None):
        """
        Report a list of documents for logging or other purposes.
        
        Args:
            doc_type (str): Type of document (receipts/letters)
            listing (dict): Document listing data
            mailbox (str, optional): Company mailbox name; None for the personal mailbox
        """
        try:
            # Store listing as JSON
            if doc_type == 'receipts':
                _, json_dir = self._type_dirs(self._root_dir(mailbox), 'receipt')
            elif doc_type == 'letters':
                _, json_dir = self._type_dirs(self._root_dir(mailbox), 'letter')
            else:
                raise ValueError(f"Unknown listing type: {doc_type}")

            with open(os.path.join(json_dir, f"{doc_type}.json"), 'w', encoding='utf-8') as f:
                json.dump(listing, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Error storing document listing: {str(e)}")

    def exists(self, metadata):
        """
        Check if document already exists based on metadata.
        
        Args:
            metadata (dict): Document metadata
            
        Returns:
            bool: True if exists, False otherwise
        """
        try:
            file_dir, json_dir, filename_base = self._get_filepath(metadata)
            
            # For JSON metadata check
            if metadata.get('content_type') == 'application/json':
                json_path = os.path.join(json_dir, f"{filename_base}.json")
                return os.path.exists(json_path)
            
            # For content files check
            content_type = metadata.get('content_type', '')
            if content_type == 'application/pdf':
                file_path = os.path.join(file_dir, f"{filename_base}.pdf")
                return os.path.exists(file_path)
            elif content_type == 'text/plain':
                file_path = os.path.join(file_dir, f"{filename_base}.txt")
                return os.path.exists(file_path)
            elif content_type == 'text/html':
                file_path = os.path.join(file_dir, f"{filename_base}_html.pdf")
                return os.path.exists(file_path)
            
            return False
        except Exception as e:
            logging.error(f"Error checking if document exists: {str(e)}")
            return False
    
    def report_metadata(self, data, metadata):
        """
        Report document metadata by storing it as a JSON file.
        
        Args:
            data (dict): The document metadata as a dictionary
            metadata (dict): Document metadata
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            dir_path, json_dir, filename_base = self._get_filepath(metadata)
            
            # Store as JSON file
            json_path = os.path.join(json_dir, f"{filename_base}.json")
            
            if self.dry_run:
                logging.info(f"DRY RUN: Would store metadata to {json_path}")
                return True
                
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logging.error(f"Error storing document metadata: {str(e)}")
            return False
    
    def store(self, data, metadata):
        """
        Store document data and metadata in the file system.
        
        Args:
            data (bytes or dict): The document content
            metadata (dict): Document metadata
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            dir_path, json_dir, filename_base = self._get_filepath(metadata)
            content_type = metadata.get('content_type', '')
            
            # Store content files
            if content_type == 'application/pdf':
                file_path = os.path.join(dir_path, f"{filename_base}.pdf")
                if self.dry_run:
                    logging.info(f"DRY RUN: Would store PDF to {file_path}")
                    return True
                with open(file_path, 'wb') as f:
                    f.write(data)
                return True
            elif content_type == 'text/plain':
                file_path = os.path.join(dir_path, f"{filename_base}.txt")
                if self.dry_run:
                    logging.info(f"DRY RUN: Would store text to {file_path}")
                    return True
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(data)
                return True
            elif content_type == 'text/html':
                # Use the extracted HTML to PDF conversion utility
                pdf_data = html_to_pdf(data)
                
                if pdf_data:
                    file_path = os.path.join(dir_path, f"{filename_base}_html.pdf")
                    if self.dry_run:
                        logging.info(f"DRY RUN: Would store HTML as PDF to {file_path}")
                        return True
                    with open(file_path, 'wb') as f:
                        f.write(pdf_data)
                    return True
                else:
                    # Fallback to saving HTML source if conversion fails
                    file_path = os.path.join(dir_path, f"{filename_base}_html.html")
                    if self.dry_run:
                        logging.info(f"DRY RUN: Would store HTML source to {file_path}")
                        return True
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(data)
                    return False
            
            logging.warning(f"Unsupported content type: {content_type}")
            return False
        except Exception as e:
            logging.error(f"Error storing document: {str(e)}")
            return False
