"""
Idempotency utilities

Provides function-level idempotency protection to prevent duplicate execution.
"""
import functools
import hashlib
import json
import logging
from typing import Callable

from src.core.database import get_db

logger = logging.getLogger(__name__)


def _generate_idempotency_key(func_name: str, args: tuple, kwargs: dict) -> str:
    """
    Generate an idempotency key from function name and arguments.

    Args:
        func_name: Function name
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        Format: {func_name}:{hash}
    """
    params_str = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
    params_hash = hashlib.sha256(params_str.encode()).hexdigest()[:16]
    return f"{func_name}:{params_hash}"


def idempotent(
    key_func: Callable = None,
    operation: str = None,
    ttl_hours: int = 24
):
    """
    Idempotency decorator

    Ensures identical requests are executed only once; duplicate requests return cached results.

    Args:
        key_func: Function to generate idempotency key from arguments; must have the same signature as the decorated function
        operation: Operation type name
        ttl_hours: Cache TTL in hours

    Example:
        @idempotent(
            key_func=lambda order_id, **_: f"activate:{order_id}",
            operation="activate_membership"
        )
        async def activate_membership(order_id: str, ...):
            ...

    Behavior:
        - First call: executes function and caches result
        - Duplicate call (COMPLETED): returns cached result
        - Duplicate call (PROCESSING): raises RuntimeError
        - Duplicate call (FAILED): allows retry
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate idempotency key
            if key_func:
                key = key_func(*args, **kwargs)
            else:
                key = _generate_idempotency_key(func.__name__, args, kwargs)

            op_type = operation or func.__name__
            db = get_db()

            # Check if already processed
            check_sql = """
                SELECT response, status FROM idempotency_keys
                WHERE idempotency_key = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
            """
            row = db.execute(check_sql, (key,), fetch='one')

            if row:
                status = row.get('status')
                if status == 'COMPLETED':
                    logger.info(f"Idempotency hit: {key}")
                    return json.loads(row['response']) if row.get('response') else None
                elif status == 'PROCESSING':
                    raise RuntimeError(f"Operation already in progress: {key}")
                # FAILED status allows retry; continue execution

            # Mark as processing
            insert_sql = """
                INSERT INTO idempotency_keys (idempotency_key, operation_type, status, expires_at)
                VALUES (%s, %s, 'PROCESSING', DATE_ADD(NOW(), INTERVAL %s HOUR))
                ON DUPLICATE KEY UPDATE status = 'PROCESSING'
            """
            db.execute(insert_sql, (key, op_type, ttl_hours), fetch=None)

            try:
                # Execute function
                result = await func(*args, **kwargs)

                # Save result
                update_sql = """
                    UPDATE idempotency_keys
                    SET status = 'COMPLETED', response = %s
                    WHERE idempotency_key = %s
                """
                db.execute(update_sql, (json.dumps(result, default=str), key), fetch=None)

                return result

            except Exception:
                # Mark as failed
                fail_sql = """
                    UPDATE idempotency_keys
                    SET status = 'FAILED'
                    WHERE idempotency_key = %s
                """
                db.execute(fail_sql, (key,), fetch=None)
                raise

        return wrapper
    return decorator
