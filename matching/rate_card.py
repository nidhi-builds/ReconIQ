GST_ON_FEE_RATE = 0.18

_FEE_RATES = {
    "upi": 0.01,
    "card": 0.02,
    "netbanking": 0.015,
    "wallet": 0.018,
    "emi": 0.025,
}


def calculate_settlement_amounts(
    gross_amount: float, payment_method: str
) -> tuple[float, float, float]:
    fee = round(gross_amount * _FEE_RATES[payment_method], 2)
    gst_on_fee = round(fee * GST_ON_FEE_RATE, 2)
    net_amount = round(gross_amount - fee - gst_on_fee, 2)
    return fee, gst_on_fee, net_amount
