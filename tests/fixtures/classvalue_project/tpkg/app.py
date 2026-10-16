class Service:
    def go(self):
        return 1


class App:
    def __init__(self):
        self.service = Service()

    def run(self):
        return 2
