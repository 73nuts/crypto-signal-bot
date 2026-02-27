"""
DI container unit tests

TDD: write tests first, then implement code

Run:
    pytest tests/core_tests/test_container.py -v
"""
import unittest
from typing import Protocol, runtime_checkable


class TestContainerRegistration(unittest.TestCase):
    """Tests for dependency registration"""

    def setUp(self):
        """Reset container before each test"""
        from src.core.container import Container
        self.container = Container()

    def test_register_class(self):
        """Register a class"""
        from src.core.container import Lifecycle

        class MyService:
            pass

        self.container.register(MyService, MyService)
        instance = self.container.resolve(MyService)

        self.assertIsInstance(instance, MyService)

    def test_register_singleton(self):
        """Singleton registration"""
        from src.core.container import Lifecycle

        class MyService:
            pass

        self.container.register(MyService, MyService, Lifecycle.SINGLETON)

        instance1 = self.container.resolve(MyService)
        instance2 = self.container.resolve(MyService)

        self.assertIs(instance1, instance2)

    def test_register_factory(self):
        """Factory registration"""
        from src.core.container import Lifecycle

        class MyService:
            pass

        self.container.register(MyService, MyService, Lifecycle.FACTORY)

        instance1 = self.container.resolve(MyService)
        instance2 = self.container.resolve(MyService)

        self.assertIsNot(instance1, instance2)

    def test_register_instance(self):
        """Register an existing instance"""
        class MyService:
            def __init__(self, value):
                self.value = value

        existing = MyService(42)
        self.container.register_instance(MyService, existing)

        resolved = self.container.resolve(MyService)

        self.assertIs(resolved, existing)
        self.assertEqual(resolved.value, 42)

    def test_register_with_protocol(self):
        """Register using Protocol"""
        @runtime_checkable
        class ServiceProtocol(Protocol):
            def do_something(self) -> str:
                ...

        class ConcreteService:
            def do_something(self) -> str:
                return "done"

        self.container.register(ServiceProtocol, ConcreteService)
        instance = self.container.resolve(ServiceProtocol)

        self.assertIsInstance(instance, ConcreteService)
        self.assertEqual(instance.do_something(), "done")

    def test_register_factory_function(self):
        """Register using a factory function"""
        class MyService:
            def __init__(self, config):
                self.config = config

        def create_service():
            return MyService({"key": "value"})

        self.container.register(MyService, create_service)
        instance = self.container.resolve(MyService)

        self.assertEqual(instance.config, {"key": "value"})


class TestContainerResolution(unittest.TestCase):
    """Tests for dependency resolution"""

    def setUp(self):
        from src.core.container import Container
        self.container = Container()

    def test_resolve_unregistered_raises(self):
        """Resolving unregistered dependency should raise exception"""
        class UnknownService:
            pass

        with self.assertRaises(KeyError):
            self.container.resolve(UnknownService)

    def test_chain_registration(self):
        """Chained registration"""
        class ServiceA:
            pass

        class ServiceB:
            pass

        result = (
            self.container
            .register(ServiceA, ServiceA)
            .register(ServiceB, ServiceB)
        )

        self.assertIs(result, self.container)
        self.assertIsInstance(self.container.resolve(ServiceA), ServiceA)
        self.assertIsInstance(self.container.resolve(ServiceB), ServiceB)


class TestContainerOverride(unittest.TestCase):
    """Tests for dependency override"""

    def setUp(self):
        from src.core.container import Container
        self.container = Container()

    def test_override_existing(self):
        """Override an existing registered dependency"""
        class MyService:
            def __init__(self, value):
                self.value = value

        original = MyService("original")
        override = MyService("override")

        self.container.register_instance(MyService, original)
        self.container.override(MyService, override)

        resolved = self.container.resolve(MyService)
        self.assertEqual(resolved.value, "override")

    def test_override_unregistered(self):
        """Override an unregistered dependency"""
        class MyService:
            pass

        instance = MyService()
        self.container.override(MyService, instance)

        resolved = self.container.resolve(MyService)
        self.assertIs(resolved, instance)


class TestContainerValidation(unittest.TestCase):
    """Tests for dependency validation"""

    def setUp(self):
        from src.core.container import Container
        self.container = Container()

    def test_validate_success(self):
        """Validation succeeds"""
        class ServiceA:
            pass

        class ServiceB:
            pass

        self.container.register(ServiceA, ServiceA)
        self.container.register(ServiceB, ServiceB)

        result = self.container.validate()
        self.assertTrue(result)

    def test_reset_clears_instances(self):
        """reset clears singleton instances"""
        class MyService:
            pass

        self.container.register(MyService, MyService)
        instance1 = self.container.resolve(MyService)

        self.container.reset()

        instance2 = self.container.resolve(MyService)
        self.assertIsNot(instance1, instance2)


class TestGlobalContainer(unittest.TestCase):
    """Tests for global container"""

    def test_get_container_singleton(self):
        """get_container returns singleton"""
        from src.core.container import get_container, _reset_container

        _reset_container()

        container1 = get_container()
        container2 = get_container()

        self.assertIs(container1, container2)

    def test_inject_function(self):
        """inject shortcut function"""
        from src.core.container import get_container, inject, _reset_container

        _reset_container()

        class MyService:
            pass

        container = get_container()
        container.register(MyService, MyService)

        instance = inject(MyService)
        self.assertIsInstance(instance, MyService)


class TestInjectableDecorator(unittest.TestCase):
    """Tests for injectable decorator"""

    def test_injectable_registers_class(self):
        """@injectable auto-registers a class"""
        from src.core.container import injectable, get_container, _reset_container

        _reset_container()

        @injectable
        class AutoRegisteredService:
            pass

        container = get_container()
        instance = container.resolve(AutoRegisteredService)

        self.assertIsInstance(instance, AutoRegisteredService)


if __name__ == '__main__':
    unittest.main()
