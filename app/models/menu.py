from sqlalchemy import Column, String, Float, Boolean, ForeignKey, Integer, Text
from app.models.base import BaseModel
from sqlalchemy.orm import relationship

class MenuCategory(BaseModel):
    __tablename__ = "menu_categories"
    
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    
    items = relationship("MenuItem", back_populates="category")

class MenuItem(BaseModel):
    __tablename__ = "menu_items"
    
    category_id = Column(Integer, ForeignKey("menu_categories.id"), nullable=False)
    name = Column(String, nullable=False, index=True)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    is_available = Column(Boolean, default=True)
    
    category = relationship("MenuCategory", back_populates="items")
