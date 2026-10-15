from flask import Flask

app = Flask(__name__)


class Cache:
    def get(self, key):
        return lambda f: f


cache = Cache()


@app.route("/items", methods=["GET", "POST"])
def items():
    return "x"


@app.get("/ping")
def ping():
    return "pong"


@app.before_request
def pre():
    pass


@cache.get("/nope")
def nope():
    pass
