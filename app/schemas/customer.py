from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class CustomerBase(BaseModel):
    name: str
    phone_number: str

class CustomerCreate(CustomerBase):
    pass

class CustomerResponse(CustomerBase):
    id: int
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
