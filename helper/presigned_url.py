# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import hashlib
import hmac
import datetime
import urllib.parse

class AWSAPIWebSocketPresignedURL:
    def __init__(self, access_key: str, secret_key: str,session_token: str, endpoint: str, path: str, host: str, region: str = 'us-east-1'):
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.method = "GET"
        self.service = "execute-api"
        self.region = region
        self.endpoint = ""
        self.host = ""
        self.amz_date = ""
        self.datestamp = ""
        self.canonical_uri = path
        self.canonical_headers = ""
        self.signed_headers = "host"
        self.algorithm = "AWS4-HMAC-SHA256"
        self.credential_scope = ""
        self.canonical_querystring = ""
        self.payload_hash = ""
        self.canonical_request = ""
        self.string_to_sign = ""
        self.signature = ""
        self.request_url = ""
        self.endpoint = endpoint
        self.host = host
        
    def get_request_url(self) -> str:

        now = datetime.datetime.now(datetime.timezone.utc)
        self.amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        self.datestamp = now.strftime("%Y%m%d")

        self.canonical_headers = f"host:{self.host}\n"

        self.credential_scope = f"{self.datestamp}%2F{self.region}%2F{self.service}%2Faws4_request"

        self.create_canonical_querystring()
        self.create_payload_hash()
        self.create_canonical_request()
        self.create_string_to_sign()
        self.create_signature()
        self.create_url()

        return self.request_url

    def create_canonical_querystring(self):
        self.canonical_querystring = "X-Amz-Algorithm=" + self.algorithm
        self.canonical_querystring += "&X-Amz-Credential=" + self.access_key + "%2F" + self.credential_scope
        self.canonical_querystring += "&X-Amz-Date=" + self.amz_date
        self.canonical_querystring += "&X-Amz-Expires=300"
        if self.session_token:
            self.canonical_querystring += "&X-Amz-Security-Token=" + urllib.parse.quote(self.session_token, safe='')
        self.canonical_querystring += "&X-Amz-SignedHeaders=" + self.signed_headers

    def create_payload_hash(self):
        self.payload_hash = self.to_hex(self.hash(""))
        
    def create_canonical_request(self):
        self.canonical_request = f"{self.method}\n{self.canonical_uri}\n{self.canonical_querystring}\n{self.canonical_headers}\n{self.signed_headers}\n{self.payload_hash}"
        
    def create_string_to_sign(self):
        hashed_canonical_request = self.to_hex(self.hash(self.canonical_request))
        new_credential_scope = f"{self.datestamp}/{self.region}/{self.service}/aws4_request"
        
        self.string_to_sign = f"{self.algorithm}\n{self.amz_date}\n{new_credential_scope}\n{hashed_canonical_request}"
        
    def create_signature(self):
        signing_key = self.get_signature_key(self.secret_key, self.datestamp, self.region, self.service)
        self.signature = self.to_hex(self.get_keyed_hash(signing_key, self.string_to_sign))
        
    def create_url(self):
        self.canonical_querystring += "&X-Amz-Signature=" + self.signature
        self.request_url = self.endpoint + self.canonical_uri + "?" + self.canonical_querystring
    
    @staticmethod
    def hmac_sha256(data: str, key: bytes) -> bytes:
        return hmac.new(key, data.encode('utf-8'), hashlib.sha256).digest()
        
    @staticmethod
    def get_signature_key(key: str, date_stamp: str, region_name: str, service_name: str) -> bytes:
        k_secret = ("AWS4" + key).encode('utf-8')
        k_date = AWSAPIWebSocketPresignedURL.hmac_sha256(date_stamp, k_secret)
        k_region = AWSAPIWebSocketPresignedURL.hmac_sha256(region_name, k_date)
        k_service = AWSAPIWebSocketPresignedURL.hmac_sha256(service_name, k_region)
        k_signing = AWSAPIWebSocketPresignedURL.hmac_sha256("aws4_request", k_service)

        return k_signing
    
    @staticmethod
    def hash(value: str) -> bytes:
        return hashlib.sha256(value.encode('utf-8')).digest()
    
    @staticmethod
    def to_hex(data: bytes) -> str:
        return data.hex()
    
    @staticmethod
    def get_keyed_hash(key: bytes, value: str) -> bytes:
        mac = hmac.new(key, value.encode('utf-8'), hashlib.sha256)
        return mac.digest()