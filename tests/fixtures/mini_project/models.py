from base import Base as B              # as 별칭으로 가져온 부모


class Child(B):
    def __init__(self):
        super().__init__()

    def run(self):                      # Base.run 오버라이드
        return helper()

    def ping(self):
        return 1


class Other(B):
    def run(self):                      # Base.run 오버라이드
        return 2


def helper():
    return 3
