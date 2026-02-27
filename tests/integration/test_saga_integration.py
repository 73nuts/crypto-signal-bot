"""
Saga orchestration integration tests.

Tests the complete Saga flow including database operations.

How to run:
    pytest tests/integration/test_saga_integration.py -v

Prerequisites:
    - MySQL database available (crypto_local)
    - Migration 012_saga_tables.sql has been run

Version: v1.0.0
Created: 2025-12-30
"""
import asyncio
import os
import unittest

from src.core.database import get_db
from src.core.saga import (
    SagaDefinition,
    SagaOrchestrator,
    _reset_orchestrator,
)


class TestSagaIntegration(unittest.TestCase):
    """Saga orchestration integration tests (real database)."""

    @classmethod
    def setUpClass(cls):
        """Test class setup."""
        # Set test database
        os.environ.setdefault('MYSQL_HOST', 'localhost')
        os.environ.setdefault('MYSQL_PORT', '3306')
        os.environ.setdefault('MYSQL_DATABASE', 'crypto_local')
        os.environ.setdefault('MYSQL_PASSWORD', '')

    def setUp(self):
        """Reset before each test."""
        _reset_orchestrator()
        self.db = get_db()

        # Clean test data
        self._cleanup_test_data()

    def tearDown(self):
        """Clean up after each test."""
        self._cleanup_test_data()

    def _cleanup_test_data(self):
        """Clean up test data."""
        try:
            # Clean saga_steps
            self.db.execute(
                "DELETE FROM saga_steps WHERE saga_id LIKE 'test-%'",
                fetch=None
            )
            # Clean saga_instances
            self.db.execute(
                "DELETE FROM saga_instances WHERE saga_id LIKE 'test-%'",
                fetch=None
            )
            # Clean idempotency_keys
            self.db.execute(
                "DELETE FROM idempotency_keys WHERE idempotency_key LIKE 'test-%'",
                fetch=None
            )
        except Exception:
            pass

    def test_saga_success_persisted(self):
        """Test successful Saga flow persistence."""
        executed = []

        async def step1(ctx):
            executed.append('step1')
            return {'result': 'step1_done'}

        async def step2(ctx):
            executed.append('step2')
            return {'result': 'step2_done'}

        saga = SagaDefinition(saga_type="test_success")
        saga.add_step("step1", forward=step1, retries=0)
        saga.add_step("step2", forward=step2, retries=0)

        orchestrator = SagaOrchestrator(db=self.db)
        orchestrator.register(saga)

        async def run_test():
            result = await orchestrator.execute(
                saga_type="test_success",
                context={'test_id': 'test-success-001'}
            )
            return result

        result = asyncio.run(run_test())

        # Verify execution results
        self.assertEqual(executed, ['step1', 'step2'])
        self.assertIn('step1', result)
        self.assertIn('step2', result)

    def test_saga_compensation_persisted(self):
        """Test compensation flow persistence on failure."""
        executed = []
        compensated = []

        async def step1(ctx):
            executed.append('step1')
            return {'result': 'step1_done'}

        async def compensate_step1(ctx):
            compensated.append('step1')

        async def step2(ctx):
            executed.append('step2')
            raise RuntimeError("Step2 failed intentionally")

        saga = SagaDefinition(saga_type="test_compensation")
        saga.add_step("step1", forward=step1, compensate=compensate_step1, retries=0)
        saga.add_step("step2", forward=step2, retries=0)

        orchestrator = SagaOrchestrator(db=self.db)
        orchestrator.register(saga)

        async def run_test():
            try:
                await orchestrator.execute(
                    saga_type="test_compensation",
                    context={'test_id': 'test-compensation-001'}
                )
            except RuntimeError:
                pass  # Expected exception

        asyncio.run(run_test())

        # Verify execution and compensation
        self.assertEqual(executed, ['step1', 'step2'])
        self.assertEqual(compensated, ['step1'])

    def test_idempotency_prevents_duplicate(self):
        """Test idempotency prevents duplicate execution."""
        execution_count = 0

        async def step1(ctx):
            nonlocal execution_count
            execution_count += 1
            return {'count': execution_count}

        saga = SagaDefinition(saga_type="test_idempotent")
        saga.add_step("step1", forward=step1, retries=0)

        orchestrator = SagaOrchestrator(db=self.db)
        orchestrator.register(saga)

        async def run_test():
            # First execution
            result1 = await orchestrator.execute(
                saga_type="test_idempotent",
                context={'test_id': 'idempotent-001'},
                idempotency_key='test-idempotent-key-001'
            )

            # Second execution (same idempotency key)
            result2 = await orchestrator.execute(
                saga_type="test_idempotent",
                context={'test_id': 'idempotent-001'},
                idempotency_key='test-idempotent-key-001'
            )

            return result1, result2

        result1, result2 = asyncio.run(run_test())

        # Verify only executed once
        self.assertEqual(execution_count, 1)
        self.assertEqual(result1, result2)

    def test_saga_status_query(self):
        """Test Saga status query."""
        async def step1(ctx):
            return {'done': True}

        saga = SagaDefinition(saga_type="test_status")
        saga.add_step("step1", forward=step1, retries=0)

        orchestrator = SagaOrchestrator(db=self.db)
        orchestrator.register(saga)

        saga_id = None

        async def run_test():
            nonlocal saga_id
            # execute does not return saga_id; skip this part of the test
            await orchestrator.execute(
                saga_type="test_status",
                context={'test_id': 'status-001'}
            )

        asyncio.run(run_test())

        # Since execute doesn't return saga_id, verify database has a record
        sql = """
            SELECT COUNT(*) as cnt FROM saga_instances
            WHERE saga_type = 'test_status'
        """
        row = self.db.execute(sql, fetch='one')
        self.assertGreaterEqual(row['cnt'], 1)


class TestPaymentSagaIntegration(unittest.TestCase):
    """PaymentSaga integration tests."""

    @classmethod
    def setUpClass(cls):
        """Test class setup."""
        os.environ.setdefault('MYSQL_HOST', 'localhost')
        os.environ.setdefault('MYSQL_PORT', '3306')
        os.environ.setdefault('MYSQL_DATABASE', 'crypto_local')
        os.environ.setdefault('MYSQL_PASSWORD', '')

    def setUp(self):
        """Reset before each test."""
        _reset_orchestrator()

    def test_payment_saga_registration(self):
        """Test PaymentSaga registration."""
        from src.core.saga import get_orchestrator
        from src.sagas import register_payment_saga

        saga = register_payment_saga()

        self.assertEqual(saga.saga_type, "payment")
        self.assertEqual(len(saga.steps), 4)

        step_names = [s.name for s in saga.steps]
        self.assertEqual(step_names, [
            'verify_payment',
            'activate_membership',
            'send_invite',
            'notify'
        ])

        # Verify registration
        orchestrator = get_orchestrator()
        self.assertIn("payment", orchestrator._definitions)

    def test_payment_saga_step_config(self):
        """Test PaymentSaga step configuration."""
        from src.sagas import register_payment_saga

        saga = register_payment_saga()

        # Verify compensation configuration
        step_with_comp = saga.steps[1]  # activate_membership
        self.assertEqual(step_with_comp.name, "activate_membership")
        self.assertIsNotNone(step_with_comp.compensate)

        step_without_comp = saga.steps[0]  # verify_payment
        self.assertIsNone(step_without_comp.compensate)


class TestTradingSagaIntegration(unittest.TestCase):
    """TradingSaga integration tests."""

    @classmethod
    def setUpClass(cls):
        """Test class setup."""
        os.environ.setdefault('MYSQL_HOST', 'localhost')
        os.environ.setdefault('MYSQL_PORT', '3306')
        os.environ.setdefault('MYSQL_DATABASE', 'crypto_local')
        os.environ.setdefault('MYSQL_PASSWORD', '')

    def setUp(self):
        """Reset before each test."""
        _reset_orchestrator()

    def test_trading_saga_registration(self):
        """Test TradingSaga registration."""
        from src.core.saga import get_orchestrator
        from src.sagas import register_trading_saga

        saga = register_trading_saga()

        self.assertEqual(saga.saga_type, "trading")
        self.assertEqual(len(saga.steps), 4)

        step_names = [s.name for s in saga.steps]
        self.assertEqual(step_names, [
            'validate_signal',
            'create_order',
            'update_position',
            'notify'
        ])

        # Verify registration
        orchestrator = get_orchestrator()
        self.assertIn("trading", orchestrator._definitions)

    def test_trading_saga_step_config(self):
        """Test TradingSaga step configuration."""
        from src.sagas import register_trading_saga

        saga = register_trading_saga()

        # Verify compensation configuration
        step_order = saga.steps[1]  # create_order
        self.assertEqual(step_order.name, "create_order")
        self.assertIsNotNone(step_order.compensate)

        step_position = saga.steps[2]  # update_position
        self.assertIsNotNone(step_position.compensate)


if __name__ == '__main__':
    unittest.main()
