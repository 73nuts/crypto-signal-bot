"""
Saga orchestration engine unit tests.

TDD: write tests first, then implement.

How to run:
    pytest tests/core_tests/test_saga.py -v
"""
import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch


class TestSagaDefinition(unittest.TestCase):
    """Saga definition tests."""

    def test_create_saga_definition(self):
        """Create a Saga definition."""
        from src.core.saga import SagaDefinition

        saga = SagaDefinition(saga_type="payment")
        self.assertEqual(saga.saga_type, "payment")
        self.assertEqual(len(saga.steps), 0)
        self.assertEqual(saga.timeout, 300)  # Default 5 minutes

    def test_add_step_chain(self):
        """Add steps in a chain."""
        from src.core.saga import SagaDefinition

        async def step1(ctx): return {"step1": True}
        async def step2(ctx): return {"step2": True}
        async def comp1(ctx): pass

        saga = SagaDefinition(saga_type="payment")
        saga.add_step("step1", forward=step1, compensate=comp1, timeout=10)
        saga.add_step("step2", forward=step2, timeout=20)

        self.assertEqual(len(saga.steps), 2)
        self.assertEqual(saga.steps[0].name, "step1")
        self.assertEqual(saga.steps[0].timeout, 10)
        self.assertIsNotNone(saga.steps[0].compensate)
        self.assertEqual(saga.steps[1].name, "step2")
        self.assertIsNone(saga.steps[1].compensate)

    def test_fluent_api(self):
        """Fluent API."""
        from src.core.saga import SagaDefinition

        async def noop(ctx): pass

        saga = (
            SagaDefinition(saga_type="test")
            .add_step("s1", forward=noop)
            .add_step("s2", forward=noop)
            .add_step("s3", forward=noop)
        )

        self.assertEqual(len(saga.steps), 3)


class TestSagaOrchestrator(unittest.TestCase):
    """Saga orchestrator tests."""

    def setUp(self):
        """Set up mock database."""
        self.db_patcher = patch('src.core.saga.get_db')
        self.mock_get_db = self.db_patcher.start()
        self.mock_db = MagicMock()
        self.mock_db.execute = MagicMock(return_value=None)
        self.mock_db.execute_insert = MagicMock(return_value=1)
        self.mock_get_db.return_value = self.mock_db

    def tearDown(self):
        """Clean up."""
        self.db_patcher.stop()

    def _get_orchestrator(self):
        """Create an orchestrator instance."""
        from src.core.saga import SagaOrchestrator
        return SagaOrchestrator()

    def test_register_saga(self):
        """Register a Saga definition."""
        from src.core.saga import SagaDefinition

        async def noop(ctx): pass

        orchestrator = self._get_orchestrator()
        saga = SagaDefinition(saga_type="payment")
        saga.add_step("step1", forward=noop)

        orchestrator.register(saga)
        self.assertIn("payment", orchestrator._definitions)

    def test_execute_success(self):
        """Normal flow executes successfully."""
        from src.core.saga import SagaDefinition

        orchestrator = self._get_orchestrator()
        executed = []

        async def step1(ctx):
            executed.append("step1")
            return {"result": 1}

        async def step2(ctx):
            executed.append("step2")
            return {"result": 2}

        saga = SagaDefinition(saga_type="test")
        saga.add_step("step1", forward=step1)
        saga.add_step("step2", forward=step2)

        orchestrator.register(saga)

        async def run_test():
            return await orchestrator.execute("test", {"input": "data"})

        result = asyncio.run(run_test())

        self.assertEqual(executed, ["step1", "step2"])
        self.assertIn("step1", result)
        self.assertIn("step2", result)

    def test_execute_unregistered_saga_raises(self):
        """Executing an unregistered Saga raises an exception."""
        orchestrator = self._get_orchestrator()

        async def run_test():
            await orchestrator.execute("unknown", {})

        with self.assertRaises(ValueError) as cm:
            asyncio.run(run_test())

        self.assertIn("Unregistered saga type", str(cm.exception))

    def test_compensation_on_failure(self):
        """Step failure triggers compensation."""
        from src.core.saga import SagaDefinition

        orchestrator = self._get_orchestrator()
        executed = []
        compensated = []

        async def step1(ctx):
            executed.append("step1")
            return {"s1": True}

        async def comp1(ctx):
            compensated.append("step1")

        async def step2(ctx):
            executed.append("step2")
            raise RuntimeError("Step 2 failed")

        async def comp2(ctx):
            compensated.append("step2")

        saga = SagaDefinition(saga_type="test")
        saga.add_step("step1", forward=step1, compensate=comp1, retries=0)
        saga.add_step("step2", forward=step2, compensate=comp2, retries=0)

        orchestrator.register(saga)

        async def run_test():
            await orchestrator.execute("test", {})

        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(run_test())

        self.assertIn("Step 2 failed", str(cm.exception))

        # Verify compensation runs in reverse order
        # (step2 didn't complete so it doesn't need compensation; only step1 is compensated)
        self.assertEqual(executed, ["step1", "step2"])
        self.assertEqual(compensated, ["step1"])

    def test_context_passed_between_steps(self):
        """Context is passed between steps."""
        from src.core.saga import SagaDefinition

        orchestrator = self._get_orchestrator()

        async def step1(ctx):
            ctx['step1_done'] = True
            return {"order_id": 123}

        async def step2(ctx):
            # Can access initial context
            assert ctx.get('initial') == 'value'
            # Can access previous step result
            assert 'step_0_result' in ctx
            return {"confirmed": True}

        saga = SagaDefinition(saga_type="test")
        saga.add_step("step1", forward=step1)
        saga.add_step("step2", forward=step2)

        orchestrator.register(saga)

        async def run_test():
            await orchestrator.execute("test", {"initial": "value"})

        asyncio.run(run_test())

    def test_idempotency_key_cache(self):
        """Idempotency key hit returns cached result."""
        from src.core.saga import SagaDefinition

        orchestrator = self._get_orchestrator()
        execution_count = 0

        async def step1(ctx):
            nonlocal execution_count
            execution_count += 1
            return {"count": execution_count}

        saga = SagaDefinition(saga_type="test")
        saga.add_step("step1", forward=step1)
        orchestrator.register(saga)

        # Mock idempotency check returning cached result
        self.mock_db.execute.side_effect = [
            # First call: idempotency check returns None (no cache)
            None,
            # Subsequent calls: saga state operations
            None, None, None, None, None, None,
        ]

        async def run_test():
            nonlocal execution_count

            # First execution
            result1 = await orchestrator.execute("test", {}, idempotency_key="key1")
            assert result1["step1"]["count"] == 1

            # Mock second idempotency check returning cache
            self.mock_db.execute.side_effect = None
            self.mock_db.execute.return_value = {
                'response': json.dumps({"step1": {"count": 1}}),
                'status': 'COMPLETED'
            }

            # Second execution (should hit cache)
            result2 = await orchestrator.execute("test", {}, idempotency_key="key1")
            assert result2["step1"]["count"] == 1
            assert execution_count == 1  # Only executed once

        asyncio.run(run_test())


class TestSagaStatus(unittest.TestCase):
    """Saga status enum tests."""

    def test_saga_status_values(self):
        """Saga status enum values."""
        from src.core.saga import SagaStatus

        self.assertEqual(SagaStatus.RUNNING.value, "RUNNING")
        self.assertEqual(SagaStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(SagaStatus.COMPENSATING.value, "COMPENSATING")
        self.assertEqual(SagaStatus.FAILED.value, "FAILED")
        self.assertEqual(SagaStatus.COMPENSATED.value, "COMPENSATED")

    def test_step_status_values(self):
        """Step status enum values."""
        from src.core.saga import StepStatus

        self.assertEqual(StepStatus.PENDING.value, "PENDING")
        self.assertEqual(StepStatus.RUNNING.value, "RUNNING")
        self.assertEqual(StepStatus.COMPLETED.value, "COMPLETED")
        self.assertEqual(StepStatus.FAILED.value, "FAILED")
        self.assertEqual(StepStatus.COMPENSATED.value, "COMPENSATED")


class TestSagaSingleton(unittest.TestCase):
    """Saga orchestrator singleton tests."""

    def test_get_orchestrator_singleton(self):
        """Get singleton instance."""
        from src.core.saga import _reset_orchestrator, get_orchestrator

        # Reset singleton
        _reset_orchestrator()

        with patch('src.core.saga.get_db'):
            orch1 = get_orchestrator()
            orch2 = get_orchestrator()

            self.assertIs(orch1, orch2)

        # Clean up
        _reset_orchestrator()


if __name__ == '__main__':
    unittest.main()
