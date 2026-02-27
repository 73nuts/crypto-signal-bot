"""
Application bootstrap configuration

On application startup:
1. Initialize structured logging
2. Initialize DI container
3. Register all dependencies
4. Validate dependency completeness
"""
import os

from src.core.container import Container, _reset_container, get_container
from src.core.metrics import get_metrics
from src.core.protocols import (
    BroadcasterProtocol,
    CacheProtocol,
    DatabasePoolProtocol,
    MemberServiceProtocol,
    MembershipRepositoryProtocol,
    MessageBusProtocol,
    NotifierProtocol,
    PositionRepositoryProtocol,
    TradingClientProtocol,
)
from src.core.structured_logger import get_logger, setup_structured_logging


def bootstrap_production(container: Container = None) -> Container:
    """
    Production environment bootstrap

    Registers all production dependencies.
    """
    # Initialize structured logging (JSON format)
    setup_structured_logging(level="INFO", json_format=True)
    logger = get_logger(__name__)

    container = container or get_container()

    # Data layer
    from src.core.database import DatabasePool
    container.register(DatabasePoolProtocol, DatabasePool)

    # Message bus
    from src.core.message_bus import get_message_bus
    container.register(MessageBusProtocol, get_message_bus)

    # Register event handlers explicitly (must be after message_bus)
    from src.notifications.handlers import register_all_handlers
    register_all_handlers(get_message_bus())

    # Repository layer
    from src.telegram.repositories.membership_repository import MembershipRepository
    container.register(MembershipRepositoryProtocol, MembershipRepository)
    # Note: PositionRepositoryProtocol requires an adapter, not registered here
    # PositionManager method names don't match the Protocol

    # Service layer
    from src.telegram.services.member_service import MemberService
    container.register(MemberServiceProtocol, MemberService)

    # Trading client
    def create_trading_client():
        from src.trading.binance_trading_client import BinanceTradingClient

        use_testnet = os.getenv('USE_TESTNET', 'true').lower() == 'true'

        if use_testnet:
            return BinanceTradingClient(
                api_key=os.getenv('BINANCE_TESTNET_API_KEY', ''),
                api_secret=os.getenv('BINANCE_TESTNET_API_SECRET', ''),
                testnet=True
            )
        else:
            return BinanceTradingClient(
                api_key=os.getenv('BINANCE_API_KEY', ''),
                api_secret=os.getenv('BINANCE_API_SECRET', ''),
                testnet=False
            )

    container.register(TradingClientProtocol, create_trading_client)

    # Cache service (Redis)
    from src.core.cache import CacheBackend, get_cache
    cache_manager = get_cache()
    cache_manager.setup(CacheBackend.REDIS)
    container.register_instance(CacheProtocol, cache_manager)

    logger.info("Production dependencies registered")
    return container


def bootstrap_test(container: Container = None) -> Container:
    """
    Test environment bootstrap

    Registers mock dependencies.
    """
    # Initialize structured logging (colored console)
    setup_structured_logging(level="DEBUG", json_format=False, enable_colors=False)
    logger = get_logger(__name__)

    # Reset metrics
    get_metrics().reset()

    container = container or get_container()

    # Mock database
    from tests.mocks.mock_database import MockDatabasePool
    container.register(DatabasePoolProtocol, MockDatabasePool)

    # Mock Repository
    from tests.mocks.mock_repositories import MockMembershipRepository, MockPositionRepository
    container.register(MembershipRepositoryProtocol, MockMembershipRepository)
    container.register(PositionRepositoryProtocol, MockPositionRepository)

    # Mock trading client
    from tests.mocks.mock_trading_client import MockTradingClient
    container.register(TradingClientProtocol, MockTradingClient)

    # Mock message bus
    from tests.mocks.mock_message_bus import MockMessageBus
    container.register(MessageBusProtocol, MockMessageBus)

    # Mock services
    from tests.mocks.mock_services import MockMemberService
    container.register(MemberServiceProtocol, MockMemberService)

    # Mock notifiers
    from tests.mocks.mock_notifier import MockBroadcaster, MockNotifier
    container.register(NotifierProtocol, MockNotifier)
    container.register(BroadcasterProtocol, MockBroadcaster)

    # Cache service (in-memory)
    from src.core.cache import CacheBackend, _reset_cache, get_cache
    _reset_cache()  # Reset cache for test environment
    cache_manager = get_cache()
    cache_manager.setup(CacheBackend.MEMORY)
    container.register_instance(CacheProtocol, cache_manager)

    logger.info("Test dependencies registered")
    return container


def bootstrap(env: str = None) -> Container:
    """
    Generic bootstrap entry point

    Args:
        env: Environment name ('production', 'test').
            If not provided, reads from ENVIRONMENT env var.
    """
    container = get_container()
    container.reset()

    env = env or os.getenv('ENVIRONMENT', 'production')

    if env == 'test':
        bootstrap_test(container)
    else:
        bootstrap_production(container)

    logger = get_logger(__name__)
    logger.info("Bootstrap complete", environment=env)
    return container


def reset_bootstrap() -> None:
    """
    Reset bootstrap state (for testing only).
    """
    _reset_container()
