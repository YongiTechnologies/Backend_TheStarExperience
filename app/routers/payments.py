import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentStatus
from app.schemas.payment import PaymentInitializeRequest, PaymentInitializeResponse, PaymentResponse

router = APIRouter()

@router.post("/initialize", response_model=PaymentInitializeResponse)
def initialize_payment(payment_in: PaymentInitializeRequest, db: Session = Depends(get_db)):
    # 1. Validate order exists
    order = db.query(Order).filter(Order.id == payment_in.order_id).first()
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with id {payment_in.order_id} not found"
        )

    if order.status == OrderStatus.paid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order has already been paid"
        )

    if order.status == OrderStatus.cancelled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initialize payment for a cancelled order"
        )

    # 2. Generate unique payment reference
    reference = f"STAR-ORD{order.id}-{uuid.uuid4().hex[:8].upper()}"
    provider = payment_in.provider or "paystack"

    # 3. Check for existing payment record for this order
    payment = db.query(Payment).filter(Payment.order_id == order.id).first()
    if payment:
        if payment.status == PaymentStatus.paid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payment for this order has already been completed"
            )
        payment.reference = reference
        payment.provider = provider
        payment.amount = order.total_amount
        payment.status = PaymentStatus.pending
    else:
        payment = Payment(
            order_id=order.id,
            amount=order.total_amount,
            status=PaymentStatus.pending,
            provider=provider,
            reference=reference
        )
        db.add(payment)

    db.commit()
    db.refresh(payment)

    # Placeholder checkout URL suitable for frontend integration
    checkout_url = f"https://checkout.{provider}.com/pay/{reference}"

    return PaymentInitializeResponse(
        payment_id=payment.id,
        order_id=payment.order_id,
        amount=payment.amount,
        status=payment.status,
        provider=payment.provider,
        reference=payment.reference,
        checkout_url=checkout_url
    )

@router.get("/order/{order_id}", response_model=PaymentResponse)
def get_payment_by_order(order_id: int, db: Session = Depends(get_db)):
    payment = db.query(Payment).filter(Payment.order_id == order_id).first()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No payment record found for order id {order_id}"
        )
    return payment
