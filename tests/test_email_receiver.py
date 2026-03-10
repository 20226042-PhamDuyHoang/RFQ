"""Unit tests for email_receiver — header decoding, body extraction, sender parsing."""
import pytest
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from backend.services.email_receiver import (
    decode_header_value,
    extract_sender_email,
    get_email_body,
)


class TestDecodeHeaderValue:
    def test_plain_ascii(self):
        assert decode_header_value("Hello World") == "Hello World"

    def test_none_returns_empty(self):
        assert decode_header_value(None) == ""

    def test_encoded_utf8(self):
        # RFC 2047 encoded header
        encoded = "=?UTF-8?B?TmfGsOG7nWkgZMO5bmc=?="
        result = decode_header_value(encoded)
        assert "Ng" in result  # Decoded Vietnamese text


class TestExtractSenderEmail:
    def test_angle_bracket_format(self):
        assert extract_sender_email("John <john@example.com>") == "john@example.com"

    def test_plain_email(self):
        assert extract_sender_email("john@example.com") == "john@example.com"

    def test_quoted_name(self):
        assert extract_sender_email('"John Smith" <john@example.com>') == "john@example.com"

    def test_case_normalization(self):
        assert extract_sender_email("John@Example.COM") == "john@example.com"

    def test_whitespace(self):
        assert extract_sender_email("  john@example.com  ") == "john@example.com"


class TestGetEmailBody:
    def test_plain_text(self):
        msg = MIMEText("Hello vendor", "plain")
        assert get_email_body(msg) == "Hello vendor"

    def test_multipart_prefers_plain(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("<html>Hello</html>", "html"))
        msg.attach(MIMEText("Hello plain", "plain"))
        # walk() order: multipart container, then html, then plain
        # But our function finds first text/plain
        body = get_email_body(msg)
        assert "Hello" in body

    def test_html_fallback(self):
        msg = MIMEMultipart()
        msg.attach(MIMEText("<html><body>Price is $500</body></html>", "html"))
        body = get_email_body(msg)
        assert "500" in body

    def test_empty_message(self):
        msg = MIMEText("", "plain")
        assert get_email_body(msg) == ""
