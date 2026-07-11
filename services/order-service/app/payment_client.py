"""Typed gRPC client for the payment gateway's canonical GetStatus RPC."""

import logging
import os

import grpc

from app.proto import payment_pb2, payment_pb2_grpc

logger = logging.getLogger(__name__)

PAYMENT_GATEWAY_ADDRESS = os.getenv("PAYMENT_GATEWAY_ADDRESS", "payment-gateway:50052")


def get_payment_status(charge_id: str, *, address: str | None = None) -> str:
    """Return the gateway status for a payment charge ID."""
    with grpc.insecure_channel(address or PAYMENT_GATEWAY_ADDRESS) as channel:
        stub = payment_pb2_grpc.PaymentServiceStub(channel)
        request = payment_pb2.GetStatusRequest(charge_id=charge_id)
        try:
            response = stub.GetStatus(request, timeout=2)
        except grpc.RpcError as exc:
            logger.warning("gRPC call to payment-gateway failed: %s", exc)
            raise
    status_names = {
        payment_pb2.STATUS_UNSPECIFIED: "unspecified",
        payment_pb2.APPROVED: "approved",
        payment_pb2.DECLINED: "declined",
        payment_pb2.REFUNDED: "refunded",
    }
    return status_names[response.status]
