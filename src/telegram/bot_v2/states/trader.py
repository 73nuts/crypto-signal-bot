"""
Trader Program FSM state definitions.
"""
from aiogram.fsm.state import State, StatesGroup


class TraderStates(StatesGroup):
    """Trader application flow states."""
    waiting_for_uid = State()
    confirming = State()
