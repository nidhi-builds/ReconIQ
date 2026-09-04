_SETTLEMENT_WINDOWS = {
    "upi": 2,
    "wallet": 3,
    "netbanking": 4,
    "card": 5,
    "emi": 6,
}


def settlement_window_days(payment_method: str) -> int:
    return _SETTLEMENT_WINDOWS[payment_method]
