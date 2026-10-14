class Base:
    def __init__(self):
        self.ready = False

    def run(self):                      # 자식이 오버라이드하는 빈 껍데기
        raise NotImplementedError

    def execute(self):
        return self.run()               # 실제로는 자식의 run 이 실행될 수 있다
