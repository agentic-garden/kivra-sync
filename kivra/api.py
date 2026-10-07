#!/usr/bin/python3
# -*- coding: utf-8 -*-

import requests
import logging
import sys
import time
from urllib.parse import quote

class KivraApiClient:
    """Client for interacting with Kivra's API."""
    
    def __init__(self, access_token, actor_key, actor_type='user', personal_user_id=None,
                 request_interval_seconds=1.2):
        """
        Initialize the Kivra API client.
        
        Args:
            access_token (str): OAuth access token
            actor_key (str): Kivra user ID
        """
        self.access_token = access_token
        if actor_type not in ('user', 'company') or not actor_key:
            raise ValueError('A valid actor type and key are required')
        self.actor_key = actor_key
        self.actor_type = actor_type
        self.personal_user_id = personal_user_id or actor_key
        self.request_interval_seconds = max(1.0, float(request_interval_seconds))
        self._last_request_started = None
        self.graphql_url = "https://bff.kivra.com/graphql"
        self.session = requests.Session()

    def _pace_request(self):
        """Keep sequential API calls at least one second apart."""
        now = time.monotonic()
        if self._last_request_started is not None:
            remaining = self.request_interval_seconds - (now - self._last_request_started)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_started = time.monotonic()
    
    def get_headers(self):
        """
        Get common headers for API requests.
        
        Returns:
            dict: Headers for API requests
        """
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Origin': 'https://inbox.kivra.com',
            'Referer': 'https://inbox.kivra.com/',
            'Authorization': f'Bearer {self.access_token}',
            'X-Actor-Key': self.actor_key,
            'X-Actor-Type': self.actor_type,
            'X-Session-Actor': f'user_{self.personal_user_id}',
            'X-Kivra-Environment': 'production',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
            'Accept-Language': 'sv',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
    
    def graphql_query(self, operation_name, query, variables):
        """
        Execute a GraphQL query.
        
        Args:
            operation_name (str): Name of the GraphQL operation
            query (str): GraphQL query string
            variables (dict): Variables for the query
            
        Returns:
            dict: Query response data
        """
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables
        }
        
        logging.debug(f"GraphQL query: {operation_name}")
        
        self._pace_request()
        response = self.session.post(
            self.graphql_url,
            json=payload,
            headers=self.get_headers()
        )
        
        if response.status_code != 200:
            raise RuntimeError(f"GraphQL {operation_name} failed (HTTP {response.status_code})")
            
        data = response.json()
        if 'errors' in data:
            raise RuntimeError(f"GraphQL {operation_name} returned errors")
            
        return data
    
    def get_pdf(self, url):
        """
        Get a PDF document from Kivra.
        
        Args:
            url (str): URL to the PDF document
            
        Returns:
            bytes: PDF content
        """
        headers = {
            'Authorization': f'token {self.access_token}',
            'Accept': 'application/pdf'
        }
        
        self._pace_request()
        response = self.session.get(url, headers=headers)
        
        if response.status_code != 200:
            raise RuntimeError(f"PDF fetch failed (HTTP {response.status_code})")
        
        return response.content
    
    def get_content_details(self, content_key):
        """
        Get details for a content item (letter).
        
        Args:
            content_key (str): Content key
            
        Returns:
            dict: Content details
        """
        if self.actor_type == 'company':
            raise RuntimeError(
                'Company content detail operation is unknown; sender listing cannot supply parts. '
                'Confirm the authenticated company detail request before downloading.'
            )
        content_url = f"https://app.api.kivra.com/v1/content/{quote(str(content_key), safe='')}"
        headers = self._content_headers('application/json')

        self._pace_request()
        response = self.session.get(content_url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(f"Content detail fetch failed (HTTP {response.status_code})")
        
        return response.json()
    
    def get_content_file(self, content_key, file_key):
        """
        Get a file from a content item (letter).
        
        Args:
            content_key (str): Content key
            file_key (str): File key
            
        Returns:
            bytes: File content
        """
        content_path = quote(str(content_key), safe='')
        file_path = quote(str(file_key), safe='')
        if self.actor_type == 'company':
            actor_path = quote(str(self.actor_key), safe='')
            file_url = (f"https://app.api.kivra.com/v4/company/{actor_path}/"
                        f"content/{content_path}/parts/{file_path}/raw")
        else:
            file_url = f"https://app.api.kivra.com/v1/content/{content_path}/file/{file_path}/raw"
        headers = self._content_headers()

        self._pace_request()
        response = self.session.get(file_url, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(f"Content file fetch failed (HTTP {response.status_code})")
        
        return response.content

    def _content_headers(self, accept=None):
        headers = {'Authorization': f'token {self.access_token}'}
        if accept:
            headers['Accept'] = accept
        if self.actor_type == 'company':
            headers.update({
                'X-Actor-Type': self.actor_type,
                'X-Actor-Key': self.actor_key,
                'X-Session-Actor': f'user_{self.personal_user_id}'
            })
        return headers
