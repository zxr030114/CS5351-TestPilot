import pytest

from payment import deposit, withdraw


def test_normal_withdraw():
    assert withdraw(100, 30) == 70


def test_insufficient_balance():
    with pytest.raises(ValueError, match="Insufficient balance"):
        withdraw(100, 150)


def test_negative_amount():
    with pytest.raises(ValueError, match="non-negative"):
        withdraw(100, -10)


def test_deposit():
    assert deposit(100, 20) == 120