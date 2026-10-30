from sqlalchemy import Column, Integer, String, DateTime, Date, Boolean, ForeignKey, Text, DECIMAL
from sqlalchemy.sql import func
from .database import Base  


# Таблица пользователей
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, nullable=False)
    name = Column(String(100))
    email = Column(String(100))
    ban_reason = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_banned = Column(Boolean, default=False)

# Таблица маршрутов
class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True, index=True)
    from_city = Column(String(100), nullable=False)
    to_city = Column(String(100), nullable=False)
    distance_km = Column(Integer)
    base_price = Column(DECIMAL(10, 2))


# Таблица бронирований
class Booking(Base):
    __tablename__ = "bookings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    route_id = Column(Integer, ForeignKey("routes.id"))
    trip_date = Column(Date)
    trip_time = Column(String, nullable=True)
    trip_address = Column(String, nullable=True)
    passengers = Column(Integer, default=1)
    comment = Column(String, nullable=True)
    status = Column(String, default="new")
    total_price = Column(Integer, default=0) 
    total_km = Column(Integer, default=0)            


# Таблица подписчиков
class Subscriber(Base):
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(100), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# Таблица отзывов
class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    booking_id = Column(Integer, ForeignKey("bookings.id"))
    rating = Column(Integer)
    comment = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
