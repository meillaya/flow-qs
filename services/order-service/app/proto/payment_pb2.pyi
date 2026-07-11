from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Status(int):
    __slots__ = ()
    STATUS_UNSPECIFIED: _ClassVar[Status]
    APPROVED: _ClassVar[Status]
    DECLINED: _ClassVar[Status]
    REFUNDED: _ClassVar[Status]
STATUS_UNSPECIFIED: Status
APPROVED: Status
DECLINED: Status
REFUNDED: Status

class ChargeRequest(_message.Message):
    __slots__ = ("order_id", "amount_cents", "card_token")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_CENTS_FIELD_NUMBER: _ClassVar[int]
    CARD_TOKEN_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    amount_cents: int
    card_token: str
    def __init__(self, order_id: _Optional[str] = ..., amount_cents: _Optional[int] = ..., card_token: _Optional[str] = ...) -> None: ...

class ChargeResponse(_message.Message):
    __slots__ = ("charge_id", "status")
    CHARGE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    charge_id: str
    status: Status
    def __init__(self, charge_id: _Optional[str] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...

class GetStatusRequest(_message.Message):
    __slots__ = ("charge_id",)
    CHARGE_ID_FIELD_NUMBER: _ClassVar[int]
    charge_id: str
    def __init__(self, charge_id: _Optional[str] = ...) -> None: ...

class GetStatusResponse(_message.Message):
    __slots__ = ("charge_id", "order_id", "amount_cents", "status")
    CHARGE_ID_FIELD_NUMBER: _ClassVar[int]
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_CENTS_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    charge_id: str
    order_id: str
    amount_cents: int
    status: Status
    def __init__(self, charge_id: _Optional[str] = ..., order_id: _Optional[str] = ..., amount_cents: _Optional[int] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...

class RefundRequest(_message.Message):
    __slots__ = ("charge_id",)
    CHARGE_ID_FIELD_NUMBER: _ClassVar[int]
    charge_id: str
    def __init__(self, charge_id: _Optional[str] = ...) -> None: ...

class RefundResponse(_message.Message):
    __slots__ = ("charge_id", "status")
    CHARGE_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    charge_id: str
    status: Status
    def __init__(self, charge_id: _Optional[str] = ..., status: _Optional[_Union[Status, str]] = ...) -> None: ...
