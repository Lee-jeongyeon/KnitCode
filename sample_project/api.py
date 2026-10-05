# sample_project/api.py
import service

def place_order(items):
    return service.create_order(items, 0)