from flask.views import MethodView
from flask_restful import Resource, Api
from flask_restx import Namespace


class UserAPI(MethodView):
    def get(self):
        return 1

    def post(self):
        return 2

    def helper(self):
        return 3


class SubAPI(UserAPI):
    def delete(self):
        return 4


class Item(Resource):
    def get(self, id):
        return id


ns = Namespace("n")


@ns.route("/ns")
class NsThing(Resource):
    def put(self):
        return 0
