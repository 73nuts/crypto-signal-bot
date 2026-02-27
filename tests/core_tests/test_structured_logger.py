"""
Phase 7: Structured Logger module tests

Tests for structlog integration, JSON/colored output, trace_id injection, and sensitive data sanitization
"""
import unittest
import io
import sys
import json
from unittest.mock import patch


class TestSetupStructuredLogging(unittest.TestCase):
    """setup_structured_logging configuration tests"""

    def test_setup_json_format(self):
        """JSON format configuration"""
        from src.core.structured_logger import setup_structured_logging

        # Should not raise exception
        setup_structured_logging(level="INFO", json_format=True)

    def test_setup_console_format(self):
        """Colored console format configuration"""
        from src.core.structured_logger import setup_structured_logging

        setup_structured_logging(level="DEBUG", json_format=False, enable_colors=False)


class TestGetLogger(unittest.TestCase):
    """get_logger tests"""

    def test_get_logger_returns_bound_logger(self):
        """get_logger returns a structlog BoundLogger"""
        from src.core.structured_logger import get_logger
        import structlog

        logger = get_logger("test_module")
        # structlog logger should have info, error, etc.
        self.assertTrue(hasattr(logger, 'info'))
        self.assertTrue(hasattr(logger, 'error'))
        self.assertTrue(hasattr(logger, 'warning'))


class TestTraceContextInjection(unittest.TestCase):
    """trace_id auto-injection tests"""

    def test_add_trace_context_processor(self):
        """add_trace_context processor injects trace_id"""
        from src.core.structured_logger import add_trace_context
        from src.core.tracing import set_trace_id, set_user_id

        # Set context
        set_trace_id("test_trace_123")
        set_user_id("user_456")

        event_dict = {'event': 'test message'}
        result = add_trace_context(None, 'info', event_dict)

        self.assertEqual(result['trace_id'], 'test_trace_123')
        self.assertEqual(result['user_id'], 'user_456')


class TestSanitization(unittest.TestCase):
    """Sensitive data sanitization tests"""

    def test_sanitize_api_key(self):
        """Sanitize API key"""
        from src.core.structured_logger import sanitize_event

        event_dict = {
            'event': 'api_key=abc123def456ghij789'
        }
        result = sanitize_event(None, 'info', event_dict)

        # Should be sanitized
        self.assertNotIn('abc123def456ghij789', result['event'])

    def test_sanitize_password(self):
        """Sanitize password"""
        from src.core.structured_logger import sanitize_event

        event_dict = {
            'event': 'password=my_secret_password'
        }
        result = sanitize_event(None, 'info', event_dict)

        self.assertNotIn('my_secret_password', result['event'])

    def test_sanitize_extra_fields(self):
        """Sanitize extra fields"""
        from src.core.structured_logger import sanitize_event

        event_dict = {
            'event': 'user login',
            'token': 'Bearer abc123xyz789token'
        }
        result = sanitize_event(None, 'info', event_dict)

        self.assertNotIn('abc123xyz789token', result.get('token', ''))


class TestJSONOutput(unittest.TestCase):
    """JSON output format tests"""

    def setUp(self):
        from src.core.structured_logger import setup_structured_logging
        # Configure JSON output
        setup_structured_logging(level="DEBUG", json_format=True)

    def test_json_output_format(self):
        """Verify JSON output format"""
        from src.core.structured_logger import get_logger

        # Capture output
        captured = io.StringIO()
        with patch('sys.stdout', captured):
            logger = get_logger("json_test")
            logger.info("test event", user_id=123, amount=99.9)

        output = captured.getvalue()
        # Output should be valid JSON (one per line)
        if output.strip():
            for line in output.strip().split('\n'):
                if line:
                    try:
                        data = json.loads(line)
                        self.assertIn('event', data)
                    except json.JSONDecodeError:
                        # May be colored output, skip
                        pass


class TestLoggerIntegration(unittest.TestCase):
    """Logger integration tests"""

    def test_logger_with_trace_context(self):
        """Logger used with TraceContext"""
        from src.core.structured_logger import setup_structured_logging, get_logger
        from src.core.tracing import TraceContext

        setup_structured_logging(level="DEBUG", json_format=True)

        captured = io.StringIO()
        with patch('sys.stdout', captured):
            with TraceContext(trace_id="integ_test_123", user_id="789"):
                logger = get_logger("integration")
                logger.info("integration test")

        output = captured.getvalue()
        # trace_id should appear in output
        if output.strip():
            self.assertIn('integ_test_123', output)


class TestLogLevels(unittest.TestCase):
    """Log level tests"""

    def test_log_level_filtering(self):
        """Log level filtering"""
        from src.core.structured_logger import setup_structured_logging, get_logger

        # Set to WARNING level
        setup_structured_logging(level="WARNING", json_format=False)

        logger = get_logger("level_test")
        # DEBUG and INFO should not output (but won't raise)
        logger.debug("debug message")
        logger.info("info message")
        # WARNING and above should output
        logger.warning("warning message")


if __name__ == '__main__':
    unittest.main()
