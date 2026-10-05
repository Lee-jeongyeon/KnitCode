# sample_project/service.py
from payment import PaymentService

def create_order(items, discount):
    svc = PaymentService()
    return svc.calculate_price(items, discount)