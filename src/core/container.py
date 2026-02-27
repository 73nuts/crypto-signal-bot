"""
Lightweight dependency injection container

Features:
- Singleton and Factory lifecycle support
- Protocol registration
- Factory function support
- Chained registration
- Dependency override (for testing)
"""
import logging
from enum import Enum
from typing import Dict, Type, TypeVar, Any, Optional

logger = logging.getLogger(__name__)

T = TypeVar('T')


class Lifecycle(Enum):
    """Dependency lifecycle"""
    SINGLETON = "singleton"  # Singleton, globally shared
    FACTORY = "factory"      # Factory, creates a new instance each time


class Registration:
    """Dependency registration entry"""

    def __init__(
        self,
        concrete: Any,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
        is_instance: bool = False
    ):
        self.concrete = concrete
        self.lifecycle = lifecycle
        self.is_instance = is_instance
        self.instance: Optional[Any] = None

        # If already an instance, store it directly
        if is_instance:
            self.instance = concrete


class Container:
    """
    Dependency injection container

    Usage:
        container = Container()
        container.register(ServiceProtocol, ConcreteService)
        service = container.resolve(ServiceProtocol)
    """

    def __init__(self):
        self._registrations: Dict[Type, Registration] = {}
        self._overrides: Dict[Type, Any] = {}

    def register(
        self,
        abstract: Type[T],
        concrete: Any,
        lifecycle: Lifecycle = Lifecycle.SINGLETON
    ) -> 'Container':
        """
        Register a dependency.

        Args:
            abstract: Abstract type (Protocol or base class)
            concrete: Concrete implementation (class or factory function)
            lifecycle: Dependency lifecycle

        Returns:
            self, for method chaining
        """
        self._registrations[abstract] = Registration(
            concrete=concrete,
            lifecycle=lifecycle,
            is_instance=False
        )
        return self

    def register_instance(
        self,
        abstract: Type[T],
        instance: T
    ) -> 'Container':
        """
        Register an existing instance.

        Args:
            abstract: Abstract type
            instance: Already-created instance

        Returns:
            self, for method chaining
        """
        self._registrations[abstract] = Registration(
            concrete=instance,
            lifecycle=Lifecycle.SINGLETON,
            is_instance=True
        )
        return self

    def resolve(self, abstract: Type[T]) -> T:
        """
        Resolve a dependency.

        Args:
            abstract: Abstract type to resolve

        Returns:
            Concrete instance

        Raises:
            KeyError: Dependency not registered
        """
        # Check overrides first
        if abstract in self._overrides:
            return self._overrides[abstract]

        # Check registrations
        if abstract not in self._registrations:
            raise KeyError(f"Dependency not registered: {abstract.__name__}")

        registration = self._registrations[abstract]

        # Return instance directly if already created
        if registration.is_instance:
            return registration.instance

        # Singleton: return cached instance
        if registration.lifecycle == Lifecycle.SINGLETON:
            if registration.instance is None:
                registration.instance = self._create_instance(registration.concrete)
            return registration.instance

        # Factory: create a new instance each time
        return self._create_instance(registration.concrete)

    def _create_instance(self, concrete: Any) -> Any:
        """
        Create an instance.

        Supports both classes and factory functions.
        """
        if callable(concrete):
            return concrete()
        raise ValueError(f"Cannot create instance: {concrete}")

    def override(self, abstract: Type[T], instance: T) -> 'Container':
        """
        Override a dependency (mainly for testing).

        Args:
            abstract: Abstract type
            instance: Override instance

        Returns:
            self, for method chaining
        """
        self._overrides[abstract] = instance
        return self

    def validate(self) -> bool:
        """
        Validate that all registered dependencies can be resolved.

        Returns:
            True if all resolvable
        """
        for abstract in self._registrations:
            try:
                self.resolve(abstract)
            except Exception as e:
                logger.error(f"Dependency validation failed: {abstract.__name__} - {e}")
                return False
        return True

    def reset(self) -> None:
        """
        Reset all singleton instances.

        Keeps registrations but clears created instances.
        """
        for registration in self._registrations.values():
            if not registration.is_instance:
                registration.instance = None
        self._overrides.clear()


# ========================================
# Global container
# ========================================

_global_container: Optional[Container] = None


def get_container() -> Container:
    """
    Get the global container singleton.

    Returns:
        Global Container instance
    """
    global _global_container
    if _global_container is None:
        _global_container = Container()
    return _global_container


def _reset_container() -> None:
    """
    Reset the global container (for testing only).
    """
    global _global_container
    _global_container = None


def inject(abstract: Type[T]) -> T:
    """
    Inject a dependency from the global container.

    Args:
        abstract: Abstract type to inject

    Returns:
        Concrete instance
    """
    return get_container().resolve(abstract)


# ========================================
# Decorators
# ========================================

def injectable(cls: Type[T]) -> Type[T]:
    """
    Automatically register a class in the global container.

    Usage:
        @injectable
        class MyService:
            pass

        # Automatically registered in the global container
        service = inject(MyService)
    """
    container = get_container()
    container.register(cls, cls)
    return cls
