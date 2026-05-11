from __future__ import annotations

class OrderIntentNotFoundError(LookupError):
    pass

class OrderIntentStateError(RuntimeError):
    def __init__(self, current_status: str) -> None:
        super().__init__(f"Order intent is in status '{current_status}'")
        self.current_status = current_status

class SignalNotFoundError(LookupError):
    pass

class OrderIntentPreviewError(RuntimeError):
    pass

class BrokerOrderNotFoundError(LookupError):
    pass

NON_CANCELABLE_ORDER_STATUSES = {
    "canceled",
    "expired",
    "filled",
    "rejected",
}
