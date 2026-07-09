"""HTTP + gRPC clients for downstream services used by checkout-service.

Each client is a small async adapter around a single downstream service.
The orchestrator only knows about the Protocol types - tests substitute fakes.
"""
from .cart_client import CartClient, HttpxCartClient
from .inventory_grpc_client import GrpcInventoryClient, InventoryClient
from .order_http_client import HttpxOrderClient, OrderClient
from .payment_grpc_client import GrpcPaymentClient, PaymentClient, PaymentDeclined

__all__ = [
    "CartClient",
    "HttpxCartClient",
    "GrpcInventoryClient",
    "InventoryClient",
    "HttpxOrderClient",
    "OrderClient",
    "GrpcPaymentClient",
    "PaymentClient",
    "PaymentDeclined",
]
