"""
Order payments.

The server's PAYMENT_PROVIDER picks the provider:
- "demo" records how the guest chose to pay (MoMo network or card) and marks the
  payment complete without moving money; staff see it labelled as a demo payment.
- "paystack" creates a Paystack transaction at /initialize and returns its hosted
  checkout page as `checkout_url`. The guest pays there and is sent back to the
  frontend, which calls /verify. Paystack's webhook confirms the payment too, so it
  is recorded even if the guest never comes back (see app/core/paystack.py).

Once paid, the order is sent to Loyverse as a sales receipt (app/core/loyverse.py).

Guest endpoints require the order's tracking token, because order IDs are
sequential and would otherwise let anyone act on other guests' orders.
"""
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core import paystack
from app.core.config import settings
from app.core.events import emit
from app.core.loyverse import sync_order_receipt
from app.core.order_access import get_order_for_guest, guest_order_token
from app.database.connection import get_db
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import (
    PaymentConfigResponse,
    PaymentInitializeRequest,
    PaymentInitializeResponse,
    PaymentResponse,
    PaymentVerifyRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Paystack's channel names for the methods guests choose.
_METHOD_FOR_CHANNEL = {"mobile_money": "momo", "card": "card"}


def _paystack_failure(exc: paystack.PaystackError) -> HTTPException:
    logger.warning("Paystack call failed: %s", exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="The payment service is unavailable right now. Please try again.",
    )


def _mark_paid(db: Session, order: Order, payment: Payment) -> None:
    payment.status = PaymentStatus.paid
    # Record the sale in Loyverse; a failure leaves the payment paid and retryable.
    sync_order_receipt(db, order)
    emit(db, "order.updated", order_id=order.id, table_number=order.table_number)


def _apply_paystack_result(db: Session, order: Order, payment: Payment, data: dict) -> None:
    """Update the payment from Paystack's view of the transaction. Caller commits."""
    if payment.status == PaymentStatus.paid:
        return
    outcome = data.get("status")
    if outcome == "success":
        expected = paystack.to_minor_units(payment.amount)
        if data.get("amount") != expected or data.get("currency") != settings.PAYSTACK_CURRENCY:
            # Never accept a payment for a different amount than the order's.
            logger.error(
                "Paystack %s paid %s %s, expected %s %s for order %s",
                payment.reference, data.get("amount"), data.get("currency"),
                expected, settings.PAYSTACK_CURRENCY, order.id,
            )
            payment.status = PaymentStatus.failed
            return
        payment.method = _METHOD_FOR_CHANNEL.get(data.get("channel"), payment.method)
        _mark_paid(db, order, payment)
    elif outcome in {"failed", "reversed"}:
        payment.status = PaymentStatus.failed
    # "abandoned", "ongoing", "pending", ...: the guest hasn't finished, stay pending.


@router.get("/config", response_model=PaymentConfigResponse)
def payment_config():
    """Which provider checkout uses, so the frontend can prepare (e.g. preload Paystack)."""
    return PaymentConfigResponse(provider=settings.PAYMENT_PROVIDER)


@router.post("/initialize", response_model=PaymentInitializeResponse)
def initialize_payment(payment_in: PaymentInitializeRequest, db: Session = Depends(get_db)):
    order = get_order_for_guest(db, payment_in.order_id, payment_in.tracking_token)

    if order.status == OrderStatus.cancelled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initialize payment for a cancelled order"
        )

    payment = order.payment
    if payment and payment.status == PaymentStatus.paid:
        # Already paid (e.g. a retry after a lost /verify response): report it, don't charge again.
        return _initialize_response(payment)

    provider = settings.PAYMENT_PROVIDER
    if (payment and provider == "paystack" and payment.provider == "paystack"
            and payment.status == PaymentStatus.pending and payment.reference):
        # The guest may have paid the earlier checkout and come back without it being
        # recorded yet: check before opening a second one.
        try:
            _apply_paystack_result(db, order, payment, paystack.verify(payment.reference))
        except paystack.PaystackError:
            pass  # e.g. never reached Paystack; start a fresh checkout below
        if payment.status == PaymentStatus.paid:
            db.commit()
            db.refresh(payment)
            return _initialize_response(payment)

    reference = f"STAR-ORD{order.id}-{uuid.uuid4().hex[:12].upper()}"
    if payment is None:
        payment = Payment(order_id=order.id)
        db.add(payment)
    payment.amount = order.total_amount
    payment.status = PaymentStatus.pending
    payment.provider = provider
    payment.method = payment_in.method
    payment.network = payment_in.network
    payment.reference = reference

    checkout_url = None
    if provider == "paystack":
        try:
            started = paystack.initialize(
                reference=reference,
                amount=payment.amount,
                email=paystack.guest_email(order.customer.phone_number if order.customer else ""),
                method=payment_in.method,
                order_id=order.id,
                table_number=order.table_number,
                customer_name=order.customer.name if order.customer else None,
                requested_callback=payment_in.callback_url,
            )
        except paystack.PaystackError as exc:
            db.rollback()
            raise _paystack_failure(exc)
        checkout_url = started.get("authorization_url")
        access_code = started.get("access_code")
    else:
        access_code = None

    db.commit()
    db.refresh(payment)
    return _initialize_response(payment, checkout_url, access_code)


def _initialize_response(
    payment: Payment, checkout_url: Optional[str] = None, access_code: Optional[str] = None
) -> PaymentInitializeResponse:
    return PaymentInitializeResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount,
        status=payment.status,
        provider=payment.provider,
        method=payment.method,
        network=payment.network,
        reference=payment.reference,
        # Paystack's hosted checkout page; send the guest there. None for demo payments.
        checkout_url=checkout_url,
        access_code=access_code,
    )


@router.post("/verify", response_model=PaymentResponse)
def verify_payment(verify_in: PaymentVerifyRequest, db: Session = Depends(get_db)):
    """Confirm a payment. For Paystack, call this when the guest returns from checkout;
    a "pending" answer means they haven't finished paying yet."""
    order = get_order_for_guest(db, verify_in.order_id, verify_in.tracking_token)
    payment = order.payment
    if not payment or payment.reference != verify_in.reference:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found for this order")

    if payment.status == PaymentStatus.paid:
        return payment

    if payment.provider == "demo":
        if settings.PAYMENT_PROVIDER != "demo":
            # Started before Paystack was switched on: no money moved, so pay again.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payment has expired. Please start the payment again.",
            )
        _mark_paid(db, order, payment)
    elif payment.provider == "paystack":
        try:
            _apply_paystack_result(db, order, payment, paystack.verify(payment.reference))
        except paystack.PaystackError as exc:
            raise _paystack_failure(exc)
    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Verification for provider '{payment.provider}' is not configured",
        )

    db.commit()
    db.refresh(payment)
    return payment


@router.post("/paystack/webhook", include_in_schema=False)
async def paystack_webhook(
    request: Request,
    x_paystack_signature: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Paystack tells us about finished payments here, even if the guest closed the page."""
    raw = await request.body()
    if not paystack.signature_is_valid(raw, x_paystack_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    if event.get("event") != "charge.success":
        return {"received": True}
    # Blocking database and Paystack calls run in a worker thread, off the event loop.
    return await run_in_threadpool(_record_charge, db, event.get("data") or {})


def _record_charge(db: Session, data: dict) -> dict:
    reference = data.get("reference")
    payment = db.query(Payment).filter(Payment.reference == reference).first() if reference else None
    if payment is None:
        # Paid on an earlier checkout after the guest had opened a new one.
        order_id = (data.get("metadata") or {}).get("order_id")
        order = db.get(Order, order_id) if isinstance(order_id, int) else None
        payment = order.payment if order else None
        if not payment or payment.provider != "paystack" or payment.status == PaymentStatus.paid:
            logger.warning("Paystack webhook for unknown reference %s", reference)
            return {"received": True}
        payment.reference = reference

    try:
        # Confirm with Paystack rather than trusting the event body alone.
        _apply_paystack_result(db, payment.order, payment, paystack.verify(reference))
    except paystack.PaystackError as exc:
        db.rollback()
        logger.warning("Couldn't confirm Paystack %s: %s", reference, exc)
        # A non-2xx answer makes Paystack retry the webhook later.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Couldn't confirm payment")
    db.commit()
    return {"received": True}


@router.get("/order/{order_id}", response_model=PaymentResponse)
def get_payment_by_order(
    order_id: int,
    token: Optional[str] = Depends(guest_order_token),
    db: Session = Depends(get_db),
):
    order = get_order_for_guest(db, order_id, token)
    if not order.payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No payment record found for order id {order_id}"
        )
    return order.payment
