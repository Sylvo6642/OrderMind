"""Fake product catalog used by the seed data. sku -> (name, unit price, returnable)."""

PRODUCTS: dict[str, tuple[str, float, bool]] = {
    # Electronics
    "ELEC-EARBUDS-01": ("Wireless Earbuds", 79.99, False),  # hygiene item, not returnable
    "ELEC-KEYB-01": ("Mechanical Keyboard", 64.50, True),
    "ELEC-MOUSE-01": ("Ergonomic Mouse", 29.99, True),
    "ELEC-MONITOR-27": ("27 inch 4K Monitor", 189.00, True),
    "ELEC-HDPHN-01": ("Noise Cancelling Headphones", 149.00, True),
    "ELEC-CABLE-USBC": ("USB-C Cable 2m", 9.99, True),
    "ELEC-CHARGER-65": ("65W GaN Charger", 39.00, True),
    "ELEC-WEBCAM-01": ("1080p Webcam", 54.99, True),
    # Home
    "HOME-MUG-01": ("Ceramic Coffee Mug", 12.50, True),
    "HOME-LAMP-01": ("LED Desk Lamp", 34.50, True),
    "HOME-PILLOW-01": ("Memory Foam Pillow", 45.00, False),  # bedding, not returnable
    "HOME-KETTLE-01": ("Electric Kettle", 42.00, True),
    "HOME-TOWEL-SET": ("Bath Towel Set", 27.99, True),
    "HOME-CANDLE-01": ("Scented Candle", 15.00, True),
    # Clothing
    "CLOTH-TEE-M": ("Cotton T-Shirt (M)", 19.99, True),
    "CLOTH-JACKET-L": ("Rain Jacket (L)", 89.99, True),
    "CLOTH-SOCKS-3": ("Wool Socks 3-Pack", 18.00, False),  # hygiene item, not returnable
    "CLOTH-HOODIE-M": ("Fleece Hoodie (M)", 49.00, True),
    "CLOTH-CAP-01": ("Baseball Cap", 22.00, True),
    # Books
    "BOOK-PYTHON-01": ("Fluent Python", 49.99, True),
    "BOOK-NOVEL-01": ("The Long Way Home (Novel)", 14.99, True),
    "BOOK-COOK-01": ("Weeknight Cooking", 24.95, True),
    # Sport
    "SPORT-YOGA-MAT": ("Yoga Mat", 32.00, True),
    "SPORT-BOTTLE-01": ("Insulated Water Bottle", 24.00, True),
    "SPORT-DUMBBELL-5": ("Dumbbell 5kg", 27.50, True),
    "SPORT-RUNSHOE-42": ("Running Shoes (EU 42)", 119.00, True),
    # Digital and gift
    "GIFT-CARD-50": ("Gift Card $50", 50.00, False),  # never returnable
    "DIGI-EBOOK-01": ("E-book: Home Workouts", 9.99, False),
}


def line_item(sku: str, qty: int = 1) -> dict:
    name, price, returnable = PRODUCTS[sku]
    return {"sku": sku, "name": name, "qty": qty, "price": price, "returnable": returnable}
