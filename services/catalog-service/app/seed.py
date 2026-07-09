"""Seed sample products into the catalog database."""

from app import db

SAMPLE_PRODUCTS = [
    {
        "id": "p001",
        "name": "Wireless Laptop Mouse",
        "description": "Ergonomic wireless mouse",
        "price_cents": 2999,
        "sku": "MOU-WRL-001",
    },
    {
        "id": "p002",
        "name": "USB-C Laptop Hub",
        "description": "7-in-1 USB-C hub",
        "price_cents": 4999,
        "sku": "HUB-USBC-001",
    },
    {
        "id": "p003",
        "name": "Mechanical Keyboard",
        "description": "RGB mechanical keyboard",
        "price_cents": 12999,
        "sku": "KEY-MEC-001",
    },
    {
        "id": "p004",
        "name": "Noise Cancelling Headphones",
        "description": "Over-ear wireless headphones",
        "price_cents": 24999,
        "sku": "HED-NC-001",
    },
    {
        "id": "p005",
        "name": "27-inch 4K Monitor",
        "description": "IPS 4K monitor",
        "price_cents": 34999,
        "sku": "MON-4K-001",
    },
    {
        "id": "p006",
        "name": "Laptop Stand Aluminum",
        "description": "Adjustable aluminum stand",
        "price_cents": 3999,
        "sku": "STD-ALU-001",
    },
    {
        "id": "p007",
        "name": "Webcam 1080p",
        "description": "Full HD webcam",
        "price_cents": 5999,
        "sku": "CAM-1080-001",
    },
    {
        "id": "p008",
        "name": "Gaming Laptop",
        "description": "High-performance gaming laptop",
        "price_cents": 149999,
        "sku": "LAP-GAM-001",
    },
    {
        "id": "p009",
        "name": "Portable SSD 1TB",
        "description": "External NVMe SSD",
        "price_cents": 8999,
        "sku": "SSD-1TB-001",
    },
    {
        "id": "p010",
        "name": "Bluetooth Speaker",
        "description": "Waterproof Bluetooth speaker",
        "price_cents": 7999,
        "sku": "SPK-BT-001",
    },
    {
        "id": "p011",
        "name": "Smartwatch Pro",
        "description": "Fitness smartwatch",
        "price_cents": 19999,
        "sku": "WAT-PRO-001",
    },
    {
        "id": "p012",
        "name": "Wireless Earbuds",
        "description": "True wireless earbuds",
        "price_cents": 9999,
        "sku": "EAR-TW-001",
    },
    {
        "id": "p013",
        "name": "USB Microphone",
        "description": "Condenser USB microphone",
        "price_cents": 6999,
        "sku": "MIC-USB-001",
    },
    {
        "id": "p014",
        "name": "Desk Lamp LED",
        "description": "Dimmable LED desk lamp",
        "price_cents": 3499,
        "sku": "LMP-LED-001",
    },
    {
        "id": "p015",
        "name": "Ergonomic Office Chair",
        "description": "Adjustable office chair",
        "price_cents": 29999,
        "sku": "CHR-ERG-001",
    },
    {
        "id": "p016",
        "name": "HDMI Cable 2m",
        "description": "Braided HDMI 2.1 cable",
        "price_cents": 1499,
        "sku": "CBL-HDMI-001",
    },
    {
        "id": "p017",
        "name": "Power Strip Surge",
        "description": "Surge protector with USB",
        "price_cents": 2499,
        "sku": "PWR-SRG-001",
    },
    {
        "id": "p018",
        "name": "Tablet 10-inch",
        "description": "Android tablet",
        "price_cents": 22999,
        "sku": "TAB-10-001",
    },
    {
        "id": "p019",
        "name": "Phone Charger 65W",
        "description": "GaN 65W charger",
        "price_cents": 3499,
        "sku": "CHG-65W-001",
    },
    {
        "id": "p020",
        "name": "Laptop Sleeve 15 inch",
        "description": "Protective laptop sleeve",
        "price_cents": 1999,
        "sku": "SLV-15-001",
    },
]


def seed_if_empty() -> int:
    """Seed the database if no products exist. Returns number of seeded rows."""
    db.init_db()
    if db.count_products() == 0:
        for product in SAMPLE_PRODUCTS:
            db.create_product(product)
        return len(SAMPLE_PRODUCTS)
    return 0
