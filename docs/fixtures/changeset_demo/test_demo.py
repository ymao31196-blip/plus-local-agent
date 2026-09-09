from math_a import add
from math_b import double


def test_add():
    assert add(2, 3) == 5


def test_double():
    assert double(3) == 6
