import os
import sys

# Ensure 'backend' package is importable when running tests from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Override env vars BEFORE importing settings
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rfq.db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("SMTP_USERNAME", "test@example.com")
os.environ.setdefault("SMTP_PASSWORD", "test")
os.environ.setdefault("SMTP_FROM_EMAIL", "test@example.com")
os.environ.setdefault("IMAP_USERNAME", "test@example.com")
os.environ.setdefault("IMAP_PASSWORD", "test")
