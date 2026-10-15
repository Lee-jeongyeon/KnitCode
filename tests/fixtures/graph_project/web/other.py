from .app import app


@app.route("/other")
def other():
    return 1
