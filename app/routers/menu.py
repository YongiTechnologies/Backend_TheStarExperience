from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database.connection import get_db
from app.models.menu import MenuCategory, MenuItem
from app.schemas.menu import MenuCategoryResponse, MenuItemResponse

router = APIRouter()

@router.get("", response_model=List[MenuItemResponse])
def get_menu(db: Session = Depends(get_db)):
    items = db.query(MenuItem).all()
    return items

@router.get("/categories", response_model=List[MenuCategoryResponse])
def get_menu_categories(db: Session = Depends(get_db)):
    categories = db.query(MenuCategory).all()
    return categories
