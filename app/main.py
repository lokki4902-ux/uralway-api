from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Subscriber, User, Booking, Route
from app.schemas import SubscriberCreate, SubscriberResponse, BookingCreate
from datetime import datetime
from . import schemas
from . import models

# ВРЕМЕННО: Удаляем старую таблицу и создаем новую
Base.metadata.create_all(bind=engine)

# ВОТ ЭТУ СТРОКУ ТЫ СЛУЧАЙНО УДАЛИЛ - ЕЁ НУЖНО ВЕРНУТЬ:
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
def create_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
     # ПРОВЕРКА НА БАН
    user = db.query(models.User).filter(models.User.phone == booking.phone).first()
    if user and user.is_banned:
        raise HTTPException(status_code=403, detail="Пользователь забанен. Бронирование невозможно.")
        
    # 1. Ищем или создаем пользователя
    user = db.query(models.User).filter(models.User.phone == booking.phone).first()
    if not user:
        user = models.User(phone=booking.phone, name=booking.name, email=booking.email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 2. Ищем или создаем маршрут
    route = db.query(models.Route).filter(
        models.Route.from_city == booking.from_city,
        models.Route.to_city == booking.to_city
    ).first()

    if not route:
        route = models.Route(from_city=booking.from_city, to_city=booking.to_city)
        db.add(route)
        db.commit()
        db.refresh(route)

    # 3. Создаем бронирование
    new_booking = models.Booking(
        user_id=user.id,
        route_id=route.id,
        trip_date=booking.trip_date,
        passengers=booking.passengers,
        comment=booking.comment,
        # Безопасно добавляем новые поля, если они пришли
        trip_time=getattr(booking, 'trip_time', None),
        trip_address=getattr(booking, 'trip_address', None),
        status='new',
        total_price=getattr(booking, 'total_price', 0),
        total_km=getattr(booking, 'total_km', 0)
    )
    db.add(new_booking)
    db.commit()
    db.refresh(new_booking)

    return {"message": "Бронирование создано!", "booking_id": new_booking.id}
    
    # main.py
# ... (твои существующие импорты и маршруты) ...

# 1. Получить все заявки
@app.get("/api/bookings")
def get_bookings(db: Session = Depends(get_db)):
    bookings = db.query(models.Booking).order_by(models.Booking.id.desc()).all()
    result = []
    for b in bookings:
        user = db.query(models.User).filter(models.User.id == b.user_id).first()
        route = db.query(models.Route).filter(models.Route.id == b.route_id).first()
        result.append({
            "id": b.id,
            "phone": user.phone if user else "—",
            "name": user.name if user else "—",
            "email": user.email if user else "—",
            "from_city": route.from_city if route else "—",
            "to_city": route.to_city if route else "—",
            "trip_date": str(b.trip_date),
            "passengers": b.passengers,
            "comment": b.comment,
            "status": getattr(b, 'status', 'new'), 
            "total_price": getattr(b, 'total_price', 0),
            "total_km": getattr(b, 'total_km', 0)
        })
    return result

# 2. Изменить статус заявки
@app.patch("/api/bookings/{booking_id}")
def update_booking_status(booking_id: int, status: str, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        booking.status = status
        db.commit()
        return {"message": "Status updated"}
    return {"error": "Booking not found"}

# 3. Удалить заявку
@app.delete("/api/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        db.delete(booking)
        db.commit()
        return {"message": "Booking deleted"}
    return {"error": "Booking not found"}

# 4. Получить всех подписчиков
@app.get("/api/subscribers", response_model=list[schemas.SubscriberResponse])
def get_subscribers(db: Session = Depends(get_db)):
    return db.query(models.Subscriber).order_by(models.Subscriber.id.desc()).all()

@app.post("/api/ban_user/{phone}")
def ban_user(phone: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_banned = True
    db.commit()
    return {"message": f"Пользователь {phone} забанен"}
