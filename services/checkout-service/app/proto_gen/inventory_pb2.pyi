from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class StockRequest(_message.Message):
    __slots__ = ("sku",)
    SKU_FIELD_NUMBER: _ClassVar[int]
    sku: str
    def __init__(self, sku: _Optional[str] = ...) -> None: ...

class StockResponse(_message.Message):
    __slots__ = ("sku", "available", "reserved")
    SKU_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    sku: str
    available: int
    reserved: int
    def __init__(self, sku: _Optional[str] = ..., available: _Optional[int] = ..., reserved: _Optional[int] = ...) -> None: ...

class ReserveRequest(_message.Message):
    __slots__ = ("sku", "quantity")
    SKU_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    sku: str
    quantity: int
    def __init__(self, sku: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class ReserveResponse(_message.Message):
    __slots__ = ("success", "available", "reserved", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    available: int
    reserved: int
    message: str
    def __init__(self, success: _Optional[bool] = ..., available: _Optional[int] = ..., reserved: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...

class ReleaseRequest(_message.Message):
    __slots__ = ("sku", "quantity")
    SKU_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    sku: str
    quantity: int
    def __init__(self, sku: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class ReleaseResponse(_message.Message):
    __slots__ = ("success", "available", "reserved", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    RESERVED_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    available: int
    reserved: int
    message: str
    def __init__(self, success: _Optional[bool] = ..., available: _Optional[int] = ..., reserved: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...
