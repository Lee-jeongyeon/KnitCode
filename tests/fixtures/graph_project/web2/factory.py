from flask import Flask
from flask_restful import Api, Resource

from .views import UserAPI, Item
from . import other


def create_app():
    app = Flask(__name__)
    api = Api(app)

    @app.route("/f", methods=["POST"])
    def f():
        return "f"

    @app.get("/g")
    def g():
        return "g"

    app.add_url_rule("/users", view_func=UserAPI.as_view("users"))
    app.add_url_rule("/legacy", "legacy", other.legacy, methods=["GET", "POST"])
    api.add_resource(Item, "/items/<id>", "/items2")
    return app


def not_flask():
    app = Something()

    @app.route("/n")
    def n():
        return "n"


def rebound():
    app = wrap(1)
    app = Flask(__name__)

    @app.route("/r")
    def r():
        return "r"
