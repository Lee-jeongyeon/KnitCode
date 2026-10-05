class PaymentService:
    def calculate_price(self, items, discount):
        total = sum(item.price for item in items)
        return apply_discount(total, discount)

def apply_discount(total, discount):
    return total - discount

class OrderService:
    def create_order(self, items, discount):
        service = PaymentService()
        price = service.calculate_price(items, discount)
        return price