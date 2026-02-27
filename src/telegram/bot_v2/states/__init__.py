"""
FSM state definitions module.

Provides:
- FeedbackStates: feedback flow states
- TraderStates: Trader application flow states

FSM state diagram (Mermaid):

```mermaid
graph TD
    subgraph Feedback
        F_IDLE[Idle] -->|/feedback| F_WAIT[waiting_feedback]
        F_WAIT -->|submit content| F_DONE[done]
        F_WAIT -->|/cancel| F_IDLE
    end

    subgraph Trader
        T_IDLE[Idle] -->|/trader| T_WAIT[waiting_uid]
        T_WAIT -->|submit UID| T_DONE[done]
        T_WAIT -->|/cancel| T_IDLE
    end

    subgraph Subscription
        S_IDLE[Idle] -->|/subscribe| S_SELECT[select plan]
        S_SELECT -->|click plan| S_PAY{awaiting payment}
        S_PAY -->|detected| S_ACTIVE[activated]
        S_PAY -->|timeout| S_IDLE
    end
```
"""
from .feedback import FeedbackStates
from .trader import TraderStates

__all__ = ['FeedbackStates', 'TraderStates']
