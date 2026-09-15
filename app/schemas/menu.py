from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List

class MenuItemBase(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    is_available: bool = True
    category_id: int

class MenuItemCreate(MenuItemBase):
    pass

class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    is_available: Optional[bool] = None
    category_id: Optional[int] = Field(default=None, ge=1)

class MenuItemAvailabilityUpdate(BaseModel):
    is_available: bool

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
