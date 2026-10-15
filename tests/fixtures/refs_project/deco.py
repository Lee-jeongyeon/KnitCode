import functools


def log_calls(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper


def tagged(tag):
    def decorate(cls):
        return cls
    return decorate
