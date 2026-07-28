from app.payment.gateway import PaymentGateway


def verify_payment(order_id: str) -> bool:
    gateway = PaymentGateway()
    return gateway.charge(order_id)
