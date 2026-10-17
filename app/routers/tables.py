from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.table import RestaurantTable
from app.schemas.table import TableResponse

router = APIRouter()


@router.get("/number/{table_number}", response_model=TableResponse)
def get_table_by_number(table_number: str, db: Session = Depends(get_db)):
    """Look a table up by the number printed on it (and encoded in its QR code)."""
    table = db.query(RestaurantTable).filter(RestaurantTable.table_number == table_number.strip()).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return table


@router.get("/{table_id}", response_model=TableResponse)
def get_table(table_id: int, db: Session = Depends(get_db)):
    table = db.query(RestaurantTable).filter(RestaurantTable.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    return table
