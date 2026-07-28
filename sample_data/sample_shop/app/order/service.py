from app.order.models import Order
from app.payment.service import verify_payment


def load_order(order_id: str) -> Order:
    return Order(order_id, 1200)


def checkout(order_id: str) -> bool:
    order = load_order(order_id)
    if verify_payment(order.order_id):
        return True
    return False


def dynamic_checkout(order_id: str, gateway: object) -> bool:
    method = getattr(gateway, "verify")
    return method(order_id)
