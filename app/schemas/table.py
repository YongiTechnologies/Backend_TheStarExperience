from pydantic import BaseModel, ConfigDict
from typing import Optional

class TableBase(BaseModel):
    table_number: str
    capacity: Optional[int] = None

class TableCreate(TableBase):
    pass

class TableResponse(TableBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)
