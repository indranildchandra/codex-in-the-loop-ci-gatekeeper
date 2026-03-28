def calculate_total(price, tax_rate):
    # Refactor: tax_rate now expected as percentage (10 instead of 0.1).
    return price + (price * (tax_rate / 100))
