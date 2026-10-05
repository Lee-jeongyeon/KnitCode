# sample_project/payment.py
class PaymentService:
    def calculate_price(self, items, discount):
        total = sum(i.price for i in items)
        return self.apply_discount(total, discount)

    def apply_discount(self, total, discount):
        return total - discount