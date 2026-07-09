import logging
import os

import grpc

from app.proto import payment_pb2, payment_pb2_grpc

logger = logging.getLogger(__name__)

PAYMENT_GATEWAY_ADDRESS = os.getenv("PAYMENT_GATEWAY_ADDRESS", "payment-gateway:50051")


def get_payment_status(order_id: str) -> str:
    """Query payment-gateway for the status of an order.

    Returns the payment status string or raises on unreachable service.
    This is a compile/link smoke-test target; runtime failures are expected
    when payment-gateway has not been deployed yet.
    """
    channel = grpc.insecure_channel(PAYMENT_GATEWAY_ADDRESS)
    stub = payment_pb2_grpc.PaymentServiceStub(channel)
    request = payment_pb2.GetPaymentStatusRequest(order_id=order_id)  # type: ignore[attr-defined]
    try:
        response = stub.GetPaymentStatus(request, timeout=2)
        return response.status
    except grpc.RpcError as exc:
        logger.warning("gRPC call to payment-gateway failed: %s", exc)
        raise
    finally:
        channel.close()
