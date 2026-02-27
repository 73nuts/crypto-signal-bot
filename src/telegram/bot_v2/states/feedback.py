"""
Feedback FSM state definitions.
"""
from aiogram.fsm.state import State, StatesGroup


class FeedbackStates(StatesGroup):
    """Feedback flow states."""
    waiting_for_content = State()
    confirming = State()
