import datetime as _dt
import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import date, datetime
from typing import Optional, List, Literal


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


# ---------- Дополнительные услуги: трансфер и посылки ----------
class ExtraServiceCreate(BaseModel):
    service_type: Literal["transfer", "parcel"]
    client_name: str = Field(min_length=1, max_length=100)
    phone: str
    from_location: str = Field(min_length=1, max_length=255)
    to_location: str = Field(min_length=1, max_length=255)
    delivery_address: Optional[str] = Field(None, max_length=500)   # обязателен для посылки (проверяет эндпоинт)
    service_date: date
    amount: int = Field(ge=0, le=10_000_000)
    comment: Optional[str] = Field(None, max_length=1000)

    @field_validator("client_name", "from_location", "to_location", "delivery_address", "comment", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if not 10 <= len(digits) <= 15:
            raise ValueError("Некорректный номер телефона")
        return v.strip()


class ExtraServiceResponse(BaseModel):
    id: int
    service_type: str
    client_name: str
    phone: str
    from_location: str
    to_location: str
    delivery_address: Optional[str] = None
    service_date: date
    amount: int
    comment: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ---------- Анонсы рейсов ----------
def _check_time(v: str) -> str:
    v = (v or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
        raise ValueError("Время должно быть в формате ЧЧ:ММ (00:00–23:59)")
    return v


class AnnouncementCreate(BaseModel):
    date: _dt.date
    time: str
    address: str = Field(min_length=1, max_length=500)
    driver_id: Literal["1", "2"]
    direction: Literal["forward", "return"] = "forward"
    occupied_seats: int = Field(0, ge=0, le=8)
    cycle_id: Optional[int] = None

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        return _check_time(v)

    @field_validator("address", mode="before")
    @classmethod
    def strip_address(cls, v):
        return v.strip() if isinstance(v, str) else v


class AnnouncementLeg(BaseModel):
    date: _dt.date
    time: str
    address: str = Field(min_length=1, max_length=500)

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        return _check_time(v)

    @field_validator("address", mode="before")
    @classmethod
    def strip_address(cls, v):
        return v.strip() if isinstance(v, str) else v


class AnnouncementCycleCreate(BaseModel):
    """Полный цикл: рейс «туда» и «обратно» с одним водителем."""
    driver_id: Literal["1", "2"]
    forward: AnnouncementLeg
    back: AnnouncementLeg


class AnnouncementResponse(BaseModel):
    id: int
    date: _dt.date
    time: str
    address: str
    driver_id: str
    direction: str
    occupied_seats: int
    cycle_id: Optional[int] = None

    class Config:
        from_attributes = True


# ---------- Отзывы ----------
class ReviewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str
    email: EmailStr
    rating: int = Field(ge=1, le=5)
    text: str = Field(min_length=1, max_length=2000)
    consent: Optional[bool] = None       # согласие на обработку персональных данных (152-ФЗ)

    @field_validator("name", "text", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if not 10 <= len(digits) <= 15:
            raise ValueError("Некорректный номер телефона")
        return v.strip()


class ReviewResponse(BaseModel):
    """Публичные поля отзыва: телефон и email наружу не отдаются."""
    id: int
    name: str
    rating: int
    text: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
