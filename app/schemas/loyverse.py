from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LoyverseStatusResponse(BaseModel):
    mode: str
    connected: bool
    store_id: Optional[str] = None
    store_name: Optional[str] = None
    # Payment method ("momo"/"card") -> the Loyverse payment type name it's recorded under.
    payment_types: dict[str, str]
    menu_items: int
    linked_menu_items: int
    simulated_outage: bool
    error: Optional[str] = None


class SimulatedOutageUpdate(BaseModel):
    enabled: bool


class LoyverseReceiptResponse(BaseModel):
    """A receipt as Loyverse returns it."""
    receipt_number: str
    order: Optional[str] = None
    receipt_type: str
    total_money: float
    note: Optional[str] = None
    line_items: list[dict]
    payments: list[dict]
    created_at: Optional[datetime] = None


class LoyverseSyncResponse(BaseModel):
    order_id: int
    loyverse_receipt_number: Optional[str] = None
    loyverse_sync_status: Optional[str] = None
    loyverse_sync_error: Optional[str] = None


class LoyverseLinkReport(BaseModel):
    linked: list[str]
    already_linked: list[str]
    not_in_loyverse: list[str]
    needs_manual_link: list[dict]
    price_differences: list[dict]
    not_on_menu: list[str]


class LoyverseVariantResponse(BaseModel):
    variant_id: str
    name: str
    price: Optional[float] = None
    linked_menu_item_id: Optional[int] = None


class MenuItemLinkUpdate(BaseModel):
    # None unlinks the menu item.
    variant_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


class MenuItemLinkResponse(BaseModel):
    menu_item_id: int
    name: str
    loyverse_variant_id: Optional[str] = None
