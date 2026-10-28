from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # <-- НОВОЕ
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Subscriber, User, Booking, Route
from app.schemas import SubscriberCreate, SubscriberResponse, BookingCreate
from datetime import datetime


Base.metadata.create_all(bind=engine)

#  FastAPI
app = FastAPI(title="УралWay API", version="1.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Тестовый эндпоинт
@app.get("/")
def read_root():
    return {"message": "УралWay API работает!", "timestamp": datetime.now()}


# Эндпоинт для подписки на рассылку
@app.post("/api/subscribe", response_model=SubscriberResponse)
def subscribe(subscriber: SubscriberCreate, db: Session = Depends(get_db)):
    existing = db.query(Subscriber).filter(Subscriber.email == subscriber.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Этот email уже подписан")

    new_subscriber = Subscriber(email=subscriber.email)
    db.add(new_subscriber)
    db.commit()
    db.refresh(new_subscriber)

    return new_subscriber


# Эндпоинт для бронирования поездки
@app.post("/api/booking")
def create_booking(booking: BookingCreate, db: Session = Depends(get_db)):
    # 1. Находим или создаем пользователя
    user = db.query(User).filter(User.phone == booking.phone).first()
    if not user:
        user = User(phone=booking.phone, name=booking.name, email=booking.email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 2. Находим маршрут
    route = db.query(Route).filter(
        Route.from_city == booking.from_city,
        Route.to_city == booking.to_city
    ).first()

    if not route:
        raise HTTPException(status_code=404, detail="Маршрут не найден")

    # 3. Создаем бронирование
    new_booking = Booking(
        user_id=user.id,
        route_id=route.id,
        trip_date=booking.trip_date,
        passengers=booking.passengers,
        comment=booking.comment
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)

    return {"message": "Бронирование создано!", "booking_id": new_booking.id}