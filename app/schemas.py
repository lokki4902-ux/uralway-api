import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import date, datetime
from typing import Optional, List, Literal


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


# Один участок заявки: откуда, куда, сколько мест
class BookingSegmentIn(BaseModel):
    from_city: str
    to_city: str
    seats: int = Field(ge=1, le=8)
    price: Optional[int] = Field(None, ge=0)  # стоимость строки (для аналитики), в БД не сохраняется


# Схема для создания бронирования
class BookingCreate(BaseModel):
    phone: str
    name: Optional[str] = Field(None, max_length=100)
    email: Optional[EmailStr] = None
    from_city: str
    to_city: str
    trip_date: date
    trip_time: Optional[str] = None
    trip_address: Optional[str] = None
    trip_direction: Optional[Literal["forward", "return"]] = "forward"
    boarding_time: Optional[str] = None
    boarding_address: Optional[str] = None
    passengers: int = 1
    comment: Optional[str] = Field(None, max_length=2000)
    total_price: Optional[int] = 0
    total_km: Optional[int] = 0
    # согласие на обработку персональных данных (152-ФЗ): без него заявка не принимается
    consent: Optional[bool] = None
    # откуда заявка. Для публичного /api/booking игнорируется; ручные заявки идут через /api/admin/booking
    source: Optional[Literal["web", "manual"]] = "web"
    # Новое: список участков (строки формы). Если не передан — берутся from_city/to_city/passengers
    segments: Optional[List[BookingSegmentIn]] = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if not 10 <= len(digits) <= 15:
            raise ValueError("Некорректный номер телефона")
        return v.strip()

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
