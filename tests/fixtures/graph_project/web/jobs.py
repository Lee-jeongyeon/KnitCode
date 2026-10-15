import typer
from celery import Celery, shared_task

capp = Celery("t")
tapp = typer.Typer()


@capp.task
def add():
    return 1


@shared_task
def sub():
    return 2


@tapp.command()
def hello():
    pass
