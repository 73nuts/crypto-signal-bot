"""
Security module tests.

Test coverage:
1. SecretsManager config loading
2. Log sanitization filter
3. Sensitive information protection

Run: pytest tests/test_security_modules.py -v
"""

import os
import logging
import pytest
from unittest.mock import patch


class TestSecretsManager:
    """SecretsManager tests."""

    def test_settings_singleton(self):
        """Test settings singleton pattern."""
        from src.core.config import settings, get_settings

        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2, "settings should be a singleton"

    def test_default_values(self):
        """Test default values."""
        from src.core.config import settings

        assert settings.MYSQL_HOST == "localhost" or settings.MYSQL_HOST
        assert settings.MYSQL_PORT == 3306 or settings.MYSQL_PORT
        assert settings.ENVIRONMENT_PREFIX  # should have a value

    def test_get_secret_method(self):
        """Test get_secret method."""
        from src.core.config import settings

        # Non-existent key should return default value
        result = settings.get_secret('NON_EXISTENT_KEY', 'default')
        assert result == 'default'

    def test_mysql_config(self):
        """Test MySQL config retrieval."""
        from src.core.config import settings

        config = settings.get_mysql_config()
        assert 'host' in config
        assert 'port' in config
        assert 'user' in config
        assert 'password' in config
        assert 'database' in config

    def test_binance_keys(self):
        """Test Binance key retrieval."""
        from src.core.config import settings

        # Testnet keys
        key, secret = settings.get_binance_keys(testnet=True)
        # Only verify return types, not specific values
        assert isinstance(key, str)
        assert isinstance(secret, str)

    def test_l2_credentials_validation(self):
        """Test L2 credential validation."""
        from src.core.config import settings

        status = settings.validate_l2_credentials()
        assert isinstance(status, dict)
        assert 'telegram' in status
        assert 'email' in status
        assert 'feishu' in status


class TestSensitiveFilter:
    """Log sanitization filter tests."""

    def test_api_key_masking(self):
        """Test API key masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        # Test api_key format
        text = "api_key=abc123def456ghi789jkl012mno345"
        result = f._sanitize(text)
        assert "abc123def456" not in result
        assert "******" in result

    def test_password_masking(self):
        """Test password masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        text = "password=mysecretpassword123"
        result = f._sanitize(text)
        assert "mysecretpassword123" not in result
        assert "******" in result

    def test_bearer_token_masking(self):
        """Test Bearer token masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        text = "Authorization: Bearer abc123xyz789token"
        result = f._sanitize(text)
        assert "abc123xyz789token" not in result
        assert "******" in result

    def test_private_key_masking(self):
        """Test private key masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        # 64-character hex private key
        text = "private_key: 0x" + "a" * 64
        result = f._sanitize(text)
        assert "a" * 64 not in result
        assert "PRIVATE_KEY" in result or "******" in result

    def test_address_partial_masking(self):
        """Test address partial masking (preserving start and end)."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        # 40-character hex address
        text = "address: 0x1234567890abcdef1234567890abcdef12345678"
        result = f._sanitize(text)
        # Should preserve first 6 and last 4 characters
        assert "0x1234" in result
        assert "5678" in result
        assert "..." in result

    def test_phone_masking(self):
        """Test phone number masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        text = "phone: 13812345678"
        result = f._sanitize(text)
        assert "12345" not in result
        assert "138" in result
        assert "5678" in result

    def test_json_field_masking(self):
        """Test JSON field masking."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        text = '{"api_key": "mysecretapikey123456"}'
        result = f._sanitize(text)
        assert "mysecretapikey123456" not in result

    def test_filter_record(self):
        """Test Filter processing a LogRecord."""
        from src.core.logger import SensitiveFilter

        f = SensitiveFilter()

        # Create a LogRecord
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=1,
            msg='api_key=secretkey123456789012345',
            args=(),
            exc_info=None
        )

        result = f.filter(record)
        assert result is True  # always returns True
        assert "secretkey123456789012345" not in record.msg


class TestLoggingSetup:
    """Logging setup tests."""

    def test_setup_logging(self):
        """Test logging initialization."""
        from src.core.logger import setup_logging, SensitiveFilter

        setup_logging(enable_sanitizer=True)

        # Check if root logger has SensitiveFilter
        root_logger = logging.getLogger()
        has_filter = any(
            isinstance(f, SensitiveFilter)
            for f in root_logger.filters
        )
        # Filter may be on the handler
        for handler in root_logger.handlers:
            if any(isinstance(f, SensitiveFilter) for f in handler.filters):
                has_filter = True
                break

        assert has_filter, "SensitiveFilter should be added to the logging system"

    def test_get_logger(self):
        """Test getting a logger."""
        from src.core.logger import get_logger, SensitiveFilter

        logger = get_logger('test_module')
        assert logger.name == 'test_module'

        # Check if logger has SensitiveFilter
        has_filter = any(
            isinstance(f, SensitiveFilter)
            for f in logger.filters
        )
        assert has_filter, "logger should have SensitiveFilter"


class TestAuditLogger:
    """Audit logger tests."""

    def test_audit_log_creation(self, tmp_path):
        """Test audit log creation."""
        from src.core.logger import AuditLogger
        import json

        log_file = tmp_path / "audit.log"
        audit = AuditLogger(log_file=str(log_file))

        audit.log(
            action='test_action',
            user_id='user123',
            details={'key': 'value'},
            result='success'
        )

        # Verify log file
        assert log_file.exists()

        with open(log_file) as f:
            line = f.readline()
            entry = json.loads(line)

        assert entry['action'] == 'test_action'
        assert entry['user_id'] == 'user123'
        assert entry['result'] == 'success'
        assert 'timestamp' in entry


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
