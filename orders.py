from pricing import calculate_total

class OrderService:
    def __init__(self):
        self.orders = []

    def create_order(self, price):
        total = calculate_total(price, 0.1) # Post refactored change in pricing.py this should be calculate_total(price, 10)
        order = {
            "price": price,
            "total": total
        }
        self.orders.append(order)
        return order