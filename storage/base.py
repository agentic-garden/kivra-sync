#!/usr/bin/python3
# -*- coding: utf-8 -*-

from abc import ABC, abstractmethod

class DocumentStoreProvider(ABC):
    """
    Abstract base class for document storage providers.

    Metadata dicts passed to the methods below may carry a 'mailbox' key with
    the name of the company mailbox a document came from. It is absent for the
    personal mailbox. Each provider decides how to represent it (e.g. a folder
    or a tag).
    """

    def prepare_mailbox(self, mailbox):
        """
        Validate and prepare storage for a company mailbox before fetching from it.

        Args:
            mailbox (str): Company mailbox name

        Raises:
            ValueError: If the provider cannot store documents for this mailbox
        """
        pass

    @abstractmethod
    def report_listing(self, doc_type, listing, mailbox=None):
        """
        Report a list of documents for logging or other purposes.

        Args:
            doc_type (str): Type of document (receipt/letter)
            listing (dict): Document listing data
            mailbox (str, optional): Company mailbox name; None for the personal mailbox
        """
        pass
    
    @abstractmethod
    def exists(self, metadata):
        """
        Check if a document already exists in the store.
        
        Args:
            metadata (dict): Document metadata including type (receipt/letter),
                            date, sender/store name, document key, etc.
        
        Returns:
            bool: True if document exists, False otherwise
        """
        pass
    
    @abstractmethod
    def store(self, data, metadata):
        """
        Store document data and metadata.
        
        Args:
            data (bytes or dict): The document content (PDF bytes, text, HTML, or JSON)
            metadata (dict): Document metadata including type (receipt/letter),
                            date, sender/store name, document key, content_type, etc.
        
        Returns:
            bool: True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def report_metadata(self, data, metadata):
        """
        Report document metadata. This is separate from store() to allow different
        handling of metadata vs. actual document content.
        
        Args:
            data (dict): The document metadata as a dictionary
            metadata (dict): Document metadata including type (receipt/letter),
                            date, sender/store name, document key, etc.
        
        Returns:
            bool: True if successful, False otherwise
        """
        pass
