class MockGateway:
    def verify(self, order_id: str) -> bool:
        return bool(order_id)
