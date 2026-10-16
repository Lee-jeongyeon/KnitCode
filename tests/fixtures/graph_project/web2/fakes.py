class MethodView:
    pass


class LocalView(MethodView):
    def get(self):
        return 1


class Plain:
    def get(self):
        return 2

    def post(self):
        return 3
