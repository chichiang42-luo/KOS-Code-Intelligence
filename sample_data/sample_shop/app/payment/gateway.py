class PaymentGateway:
    def charge(self, order_id: str) -> bool:
        return bool(order_id)
