from pydantic import BaseModel, EmailStr
from datetime import date, datetime
from typing import Optional


# Схема для создания подписчика
class SubscriberCreate(BaseModel):
    email: EmailStr


# Схема ответа для подписчика
class SubscriberResponse(BaseModel):
    id: int
    email: str
    created_at: datetime

    class Config:
        from_attributes = True


# Схема для создания пользователя
class UserCreate(BaseModel):
    phone: str
    name: Optional[str] = None
    email: Optional[EmailStr] = None


# Схема для создания бронирования
class BookingCreate(BaseModel):
    phone: str
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    from_city: str
    to_city: str
    trip_date: date
    passengers: int = 1
    comment: Optional[str] = None