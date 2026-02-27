"""
Saga orchestration engine

Implements the Saga pattern for distributed transactions:
1. Flow definition and registration
2. Step execution and compensation
3. State persistence
4. Timeout handling
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.core.database import get_db

logger = logging.getLogger(__name__)


class SagaStatus(str, Enum):
    """Saga instance status"""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPENSATING = "COMPENSATING"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


class StepStatus(str, Enum):
    """Saga step status"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


@dataclass
class SagaStep:
    """Saga step definition"""

    name: str
    forward: Callable[[Dict], Awaitable[Any]]
    compensate: Optional[Callable[[Dict], Awaitable[None]]] = None
    timeout: int = 30  # timeout in seconds
    retries: int = 3   # retry count


@dataclass
class SagaDefinition:
    """Saga flow definition"""

    saga_type: str
    steps: List[SagaStep] = field(default_factory=list)
    timeout: int = 300  # overall timeout in seconds

    def add_step(
        self,
        name: str,
        forward: Callable,
        compensate: Callable = None,
        timeout: int = 30,
        retries: int = 3,
    ) -> "SagaDefinition":
        """Add a step (chainable)"""
        self.steps.append(
            SagaStep(
                name=name,
                forward=forward,
                compensate=compensate,
                timeout=timeout,
                retries=retries,
            )
        )
        return self


class SagaOrchestrator:
    """Saga orchestrator"""

    def __init__(self, db=None):
        """
        Args:
            db: Database pool; uses get_db() if not provided
        """
        self._db = db or get_db()
        self._definitions: Dict[str, SagaDefinition] = {}
        self.logger = logging.getLogger(__name__)

    def register(self, definition: SagaDefinition):
        """Register a saga definition"""
        self._definitions[definition.saga_type] = definition
        self.logger.info(
            f"Registered saga: {definition.saga_type}, steps: {len(definition.steps)}"
        )

    async def execute(
        self, saga_type: str, context: Dict[str, Any], idempotency_key: str = None
    ) -> Dict[str, Any]:
        """
        Execute a saga flow.

        Args:
            saga_type: Saga type
            context: Flow context
            idempotency_key: Idempotency key (optional)

        Returns:
            Execution result

        Raises:
            ValueError: saga_type not registered
        """
        definition = self._definitions.get(saga_type)
        if not definition:
            raise ValueError(f"Unregistered saga type: {saga_type}")

        # Idempotency check
        if idempotency_key:
            cached = await self._check_idempotency(idempotency_key)
            if cached:
                self.logger.info(f"Idempotency hit: {idempotency_key}")
                return cached

        # Generate saga ID
        saga_id = str(uuid.uuid4())

        # Create saga instance
        await self._create_saga_instance(saga_id, saga_type, context)

        try:
            # Execute steps
            result = await self._execute_steps(saga_id, definition, context)

            # Mark as completed
            await self._update_saga_status(saga_id, SagaStatus.COMPLETED)

            # Cache idempotency result
            if idempotency_key:
                await self._save_idempotency(idempotency_key, saga_type, result)

            return result

        except Exception as e:
            self.logger.error(f"Saga execution failed: {saga_id}, error={e}")

            # Trigger compensation
            await self._compensate(saga_id, definition, context)

            raise

    async def get_saga_status(self, saga_id: str) -> Optional[Dict]:
        """Query saga execution status"""
        sql = """
            SELECT saga_id, saga_type, status, current_step, context, error_message,
                   started_at, completed_at
            FROM saga_instances
            WHERE saga_id = %s
        """
        row = self._db.execute(sql, (saga_id,), fetch="one")
        if not row:
            return None

        # Get step info
        steps_sql = """
            SELECT step_index, step_name, status, result, error_message
            FROM saga_steps
            WHERE saga_id = %s
            ORDER BY step_index
        """
        steps = self._db.execute(steps_sql, (saga_id,), fetch="all") or []

        return {**row, "steps": steps}

    async def _execute_steps(
        self, saga_id: str, definition: SagaDefinition, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute all steps"""
        results = {}
        # Track completed steps in memory (for compensation)
        context["_completed_steps"] = []
        # Step results dict for subsequent steps to access
        context["step_results"] = results

        for i, step in enumerate(definition.steps):
            self.logger.info(
                f"Executing step: {saga_id}/{step.name} ({i + 1}/{len(definition.steps)})"
            )

            # Update step status
            await self._update_step_status(saga_id, i, step.name, StepStatus.RUNNING)

            try:
                # Execute with retry
                result = await self._execute_with_retry(
                    step.forward, context, retries=step.retries, timeout=step.timeout
                )

                results[step.name] = result
                context[f"step_{i}_result"] = result  # Pass to subsequent steps
                context["_completed_steps"].append(i)  # Track completion

                # Update step status
                await self._update_step_status(
                    saga_id, i, step.name, StepStatus.COMPLETED, result
                )

            except Exception as e:
                await self._update_step_status(
                    saga_id, i, step.name, StepStatus.FAILED, error=str(e)
                )
                raise

        return results

    async def _compensate(
        self, saga_id: str, definition: SagaDefinition, context: Dict[str, Any]
    ):
        """Execute compensation (in reverse order)"""
        await self._update_saga_status(saga_id, SagaStatus.COMPENSATING)

        # Prefer in-memory completed steps, fall back to database
        completed_steps = context.get("_completed_steps", [])
        if not completed_steps:
            completed_steps = await self._get_completed_steps(saga_id)

        # Compensate in reverse order
        for step_index in reversed(completed_steps):
            step = definition.steps[step_index]

            if step.compensate:
                self.logger.info(f"Compensating step: {saga_id}/{step.name}")

                try:
                    await step.compensate(context)
                    await self._update_step_status(
                        saga_id, step_index, step.name, StepStatus.COMPENSATED
                    )
                except Exception as e:
                    self.logger.error(f"Compensation failed: {step.name}, error={e}")
                    # Log but continue compensation

        await self._update_saga_status(saga_id, SagaStatus.COMPENSATED)

    async def _execute_with_retry(
        self, func: Callable, context: Dict[str, Any], retries: int, timeout: int
    ) -> Any:
        """Execute with retry and timeout"""
        last_error = None

        for attempt in range(retries + 1):
            try:
                return await asyncio.wait_for(func(context), timeout=timeout)
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Step timed out: {timeout}s")
                self.logger.warning(f"Step timed out, attempt={attempt + 1}")
            except Exception as e:
                last_error = e
                self.logger.warning(f"Step failed, attempt={attempt + 1}, error={e}")

            if attempt < retries:
                await asyncio.sleep(2**attempt)  # Exponential backoff

        raise last_error

    # ========================================
    # Database operations
    # ========================================

    async def _create_saga_instance(
        self, saga_id: str, saga_type: str, context: Dict[str, Any]
    ):
        """Create a saga instance"""
        sql = """
            INSERT INTO saga_instances (saga_id, saga_type, context)
            VALUES (%s, %s, %s)
        """
        self._db.execute(sql, (saga_id, saga_type, json.dumps(context)), fetch=None)

    async def _update_saga_status(self, saga_id: str, status: SagaStatus):
        """Update saga status"""
        sql = """
            UPDATE saga_instances
            SET status = %s, completed_at = IF(%s IN ('COMPLETED', 'COMPENSATED', 'FAILED'), NOW(6), NULL)
            WHERE saga_id = %s
        """
        self._db.execute(sql, (status.value, status.value, saga_id), fetch=None)

    async def _update_step_status(
        self,
        saga_id: str,
        step_index: int,
        step_name: str,
        status: StepStatus,
        result: Any = None,
        error: str = None,
    ):
        """Update step status"""
        sql = """
            INSERT INTO saga_steps (saga_id, step_index, step_name, status, result, error_message, started_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(6))
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                result = VALUES(result),
                error_message = VALUES(error_message),
                completed_at = IF(VALUES(status) IN ('COMPLETED', 'FAILED', 'COMPENSATED'), NOW(6), NULL)
        """
        self._db.execute(
            sql,
            (
                saga_id,
                step_index,
                step_name,
                status.value,
                json.dumps(result) if result else None,
                error,
            ),
            fetch=None,
        )

    async def _get_completed_steps(self, saga_id: str) -> List[int]:
        """Get indices of completed steps"""
        sql = """
            SELECT step_index FROM saga_steps
            WHERE saga_id = %s AND status = 'COMPLETED'
            ORDER BY step_index
        """
        rows = self._db.execute(sql, (saga_id,), fetch="all") or []
        return [row["step_index"] for row in rows]

    # ========================================
    # Recovery mechanism
    # ========================================

    async def recover_pending_sagas(self, max_age_hours: int = 24) -> List[Dict]:
        """
        Recover incomplete saga instances.

        Called at startup to query RUNNING sagas and attempt to resume them.
        Sagas older than max_age_hours are marked as FAILED.

        Args:
            max_age_hours: Maximum recovery time window (sagas beyond this are abandoned)

        Returns:
            List of recovery results
        """
        # Query sagas that need recovery
        sql = """
            SELECT saga_id, saga_type, context, started_at
            FROM saga_instances
            WHERE status = 'RUNNING'
              AND started_at > DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY started_at ASC
        """
        rows = self._db.execute(sql, (max_age_hours,), fetch="all") or []

        if not rows:
            self.logger.info("No saga instances to recover")
            return []

        self.logger.warning(f"Found {len(rows)} saga instances to recover")

        results = []
        for row in rows:
            saga_id = row["saga_id"]
            saga_type = row["saga_type"]

            try:
                result = await self._recover_single_saga(
                    saga_id=saga_id,
                    saga_type=saga_type,
                    context=json.loads(row["context"]) if row["context"] else {},
                )
                results.append(
                    {
                        "saga_id": saga_id,
                        "saga_type": saga_type,
                        "status": "recovered",
                        "result": result,
                    }
                )
            except Exception as e:
                self.logger.error(f"Saga recovery failed: {saga_id}, error={e}")
                results.append(
                    {
                        "saga_id": saga_id,
                        "saga_type": saga_type,
                        "status": "failed",
                        "error": str(e),
                    }
                )

        # Mark expired RUNNING sagas as FAILED
        await self._expire_old_sagas(max_age_hours)

        return results

    async def _recover_single_saga(
        self, saga_id: str, saga_type: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Recover a single saga instance"""
        definition = self._definitions.get(saga_type)
        if not definition:
            raise ValueError(f"Unregistered saga type: {saga_type}, cannot recover")

        # Get completed steps
        completed_steps = await self._get_completed_steps(saga_id)
        next_step_index = len(completed_steps)

        self.logger.info(
            f"Recovering saga: {saga_id}, type={saga_type}, "
            f"completed_steps={completed_steps}, resuming from step {next_step_index}"
        )

        if next_step_index >= len(definition.steps):
            # All steps already completed; mark as success
            await self._update_saga_status(saga_id, SagaStatus.COMPLETED)
            return {"status": "already_completed"}

        try:
            # Resume from the interruption point
            result = await self._execute_steps_from(
                saga_id=saga_id,
                definition=definition,
                context=context,
                start_index=next_step_index,
            )

            await self._update_saga_status(saga_id, SagaStatus.COMPLETED)
            return result

        except Exception as e:
            self.logger.error(f"Saga recovery execution failed: {saga_id}, error={e}")
            # Trigger compensation
            context["_completed_steps"] = completed_steps + list(
                range(next_step_index, len(definition.steps))
            )
            await self._compensate(saga_id, definition, context)
            raise

    async def _execute_steps_from(
        self,
        saga_id: str,
        definition: SagaDefinition,
        context: Dict[str, Any],
        start_index: int,
    ) -> Dict[str, Any]:
        """Execute steps starting from a given index"""
        results = {}
        context["_completed_steps"] = list(range(start_index))

        # Restore previous step results into context
        for i in range(start_index):
            step_result = await self._get_step_result(saga_id, i)
            if step_result:
                results[definition.steps[i].name] = step_result
                context[f"step_{i}_result"] = step_result
        # Also populate context.step_results with existing results
        context["step_results"] = results

        for i in range(start_index, len(definition.steps)):
            step = definition.steps[i]
            self.logger.info(
                f"[Recovery] Executing step: {saga_id}/{step.name} "
                f"({i + 1}/{len(definition.steps)})"
            )

            await self._update_step_status(saga_id, i, step.name, StepStatus.RUNNING)

            try:
                result = await self._execute_with_retry(
                    step.forward, context, retries=step.retries, timeout=step.timeout
                )

                results[step.name] = result
                context[f"step_{i}_result"] = result
                context["step_results"][step.name] = result
                context["_completed_steps"].append(i)

                await self._update_step_status(
                    saga_id, i, step.name, StepStatus.COMPLETED, result
                )

            except Exception as e:
                await self._update_step_status(
                    saga_id, i, step.name, StepStatus.FAILED, error=str(e)
                )
                raise

        return results

    async def _get_step_result(self, saga_id: str, step_index: int) -> Optional[Dict]:
        """Get the result of a completed step"""
        sql = """
            SELECT result FROM saga_steps
            WHERE saga_id = %s AND step_index = %s AND status = 'COMPLETED'
        """
        row = self._db.execute(sql, (saga_id, step_index), fetch="one")
        if row and row.get("result"):
            return json.loads(row["result"])
        return None

    async def _expire_old_sagas(self, max_age_hours: int):
        """Mark expired RUNNING sagas as FAILED"""
        sql = """
            UPDATE saga_instances
            SET status = 'FAILED',
                error_message = 'Recovery timed out, expired',
                completed_at = NOW(6)
            WHERE status = 'RUNNING'
              AND started_at <= DATE_SUB(NOW(), INTERVAL %s HOUR)
        """
        result = self._db.execute(sql, (max_age_hours,), fetch=None)
        if result:
            self.logger.warning("Expired sagas marked as FAILED")

    async def _check_idempotency(self, key: str) -> Optional[Dict]:
        """Check idempotency key"""
        sql = """
            SELECT response, status FROM idempotency_keys
            WHERE idempotency_key = %s AND status = 'COMPLETED'
              AND (expires_at IS NULL OR expires_at > NOW())
        """
        row = self._db.execute(sql, (key,), fetch="one")
        if row and row.get("response"):
            return json.loads(row["response"])
        return None

    async def _save_idempotency(
        self, key: str, operation_type: str, response: Dict, ttl_hours: int = 24
    ):
        """Save idempotency result"""
        sql = """
            INSERT INTO idempotency_keys (idempotency_key, operation_type, response, status, expires_at)
            VALUES (%s, %s, %s, 'COMPLETED', DATE_ADD(NOW(), INTERVAL %s HOUR))
            ON DUPLICATE KEY UPDATE
                response = VALUES(response),
                status = 'COMPLETED',
                expires_at = VALUES(expires_at)
        """
        self._db.execute(
            sql, (key, operation_type, json.dumps(response), ttl_hours), fetch=None
        )


# ========================================
# Global singleton
# ========================================

_orchestrator: Optional[SagaOrchestrator] = None


def get_orchestrator() -> SagaOrchestrator:
    """Get the saga orchestrator singleton"""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SagaOrchestrator()
    return _orchestrator


def _reset_orchestrator():
    """Reset singleton (for testing only)"""
    global _orchestrator
    _orchestrator = None
