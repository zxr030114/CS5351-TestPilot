def withdraw(balance, amount):
    if amount < 0:
        raise ValueError("Amount must be non-negative")
    if amount > balance:
        raise ValueError("Insufficient balance")
    return round(balance - amount,1)


def deposit(balance, amount):
    return balance + amount