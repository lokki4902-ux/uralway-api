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
    trip_time: Optional[str] = None
    trip_address: Optional[str] = None
    passengers: int = 1
    comment: Optional[str] = None
    total_price: Optional[int] = 0
    total_km: Optional[int] = 0
    
# Схема для ОТВЕТА при получении заявки (для админки)
class BookingResponse(BaseModel):
    id: int
    phone: str
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    from_city: str
    to_city: str
    trip_date: date
    passengers: int
    comment: Optional[str] = None
    status: str
    # Если у тебя в базе есть эти поля, они тоже нужны:
    total_price: Optional[int] = None
    total_km: Optional[int] = None
    created_at: datetime
    is_banned: bool = False

    class Config:
        from_attributes = True
