from pydantic import BaseModel, ConfigDict
from typing import Optional, List

class MenuItemBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    is_available: bool = True
    category_id: int

class MenuItemCreate(MenuItemBase):
    pass

class MenuItemResponse(MenuItemBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)

class MenuCategoryBase(BaseModel):
    name: str
    description: Optional[str] = None

class MenuCategoryCreate(MenuCategoryBase):
    pass

class MenuCategoryResponse(MenuCategoryBase):
    id: int
    items: List[MenuItemResponse] = []
    
    model_config = ConfigDict(from_attributes=True)
