"""
Order payments.

Only a "demo" provider exists today: it records how the guest chose to pay
(MoMo network or card) and marks the payment complete without moving money,
so staff can see it labelled as a demo payment. A real provider
(Paystack/Hubtel) plugs in at initialize (create the charge) and verify
(confirm it with the provider) without changing the API shape.

Every endpoint requires the order's tracking token, because order IDs are
sequential and would otherwise let anyone act on other guests' orders.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.order_access import get_order_for_guest, guest_order_token
from app.database.connection import get_db
from app.models.order import OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import (
    PaymentInitializeRequest,
    PaymentInitializeResponse,
    PaymentResponse,
    PaymentVerifyRequest,
)

router = APIRouter()


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

    reference = f"STAR-ORD{order.id}-{uuid.uuid4().hex[:12].upper()}"
    if payment is None:
        payment = Payment(order_id=order.id)
        db.add(payment)
    payment.amount = order.total_amount
    payment.status = PaymentStatus.pending
    payment.provider = payment_in.provider
    payment.method = payment_in.method
    payment.network = payment_in.network
    payment.reference = reference

    db.commit()
    db.refresh(payment)
    return _initialize_response(payment)


def _initialize_response(payment: Payment) -> PaymentInitializeResponse:
    return PaymentInitializeResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount,
        status=payment.status,
        provider=payment.provider,
        method=payment.method,
        network=payment.network,
        reference=payment.reference,
        # A real provider returns its hosted checkout or MoMo prompt URL here.
        checkout_url=None,
    )


@router.post("/verify", response_model=PaymentResponse)
def verify_payment(verify_in: PaymentVerifyRequest, db: Session = Depends(get_db)):
    order = get_order_for_guest(db, verify_in.order_id, verify_in.tracking_token)
    payment = order.payment
    if not payment or payment.reference != verify_in.reference:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found for this order")

    if payment.status == PaymentStatus.paid:
        return payment

    if payment.provider != "demo":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Verification for provider '{payment.provider}' is not configured",
        )

    payment.status = PaymentStatus.paid
    db.commit()
    db.refresh(payment)
    return payment


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
