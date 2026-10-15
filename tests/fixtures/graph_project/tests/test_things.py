import pytest


@pytest.fixture
def data():
    return 1


def test_a():
    assert True


class TestX:
    def test_b(self):
        pass

    def helper(self):
        pass


class NotATest:
    def test_c(self):
        pass
