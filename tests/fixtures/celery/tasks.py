from celery import Celery, shared_task


def make_app():
    app = Celery("demo")
    app.config_from_object({})
    return app


@shared_task
def add(x, y):
    return x + y
