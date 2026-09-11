from sqlalchemy import Column, String
from app.models.base import BaseModel

class User(BaseModel):
    __tablename__ = "users"
    
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    # Could add roles later if needed
