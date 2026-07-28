from app.order.service import checkout


def run_demo(order_id: str) -> bool:
    return checkout(order_id)
