import os
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
import resend
from pydantic import BaseModel, EmailStr
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Subscriber, User, Booking, Route
from app.schemas import SubscriberCreate, SubscriberResponse, BookingCreate
from datetime import datetime
from . import schemas
from . import models

# ВНИМАНИЕ: drop_all закомментирован, чтобы база не удалялась при каждом перезапуске!
# Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="УралWay API", version="1.0")

# --- НАСТРОЙКИ ПОЧТЫ (RESEND) ---
resend.api_key = os.getenv("RESEND_API_KEY")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- МОДЕЛИ ДЛЯ ЗАПРОСОВ ---
class BanRequest(BaseModel):
    reason: str

class AnnouncementEmail(BaseModel):
    date: str
    time: str
    address: str


# --- ЭНДПОИНТЫ ---

@app.get("/")
def read_root():
    return {"message": "УралWay API работает!", "timestamp": datetime.now()}

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

@app.post("/api/booking")
def create_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    # 1. Ищем пользователя (один раз)
    user = db.query(models.User).filter(models.User.phone == booking.phone).first()

    # 2. Проверка на бан
    if user and user.is_banned:
        raise HTTPException(status_code=403, detail="Пользователь забанен. Бронирование невозможно.")

    # 3. Если пользователя нет — создаем
    if not user:
        user = models.User(phone=booking.phone, name=booking.name, email=booking.email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # 4. Ищем или создаем маршрут
    route = db.query(models.Route).filter(
        models.Route.from_city == booking.from_city,
        models.Route.to_city == booking.to_city
    ).first()

    if not route:
        route = models.Route(from_city=booking.from_city, to_city=booking.to_city)
        db.add(route)
        db.commit()
        db.refresh(route)

    # 5. Создаем бронирование
    new_booking = models.Booking(
        user_id=user.id,
        route_id=route.id,
        trip_date=booking.trip_date,
        passengers=booking.passengers,
        comment=booking.comment,
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
            "trip_time": getattr(b, 'trip_time', '—'),
            "passengers": b.passengers,
            "comment": b.comment,
            "status": getattr(b, 'status', 'new'),
            "total_price": getattr(b, 'total_price', 0),
            "total_km": getattr(b, 'total_km', 0),
            "is_banned": user.is_banned if user else False
        })
    return result

@app.patch("/api/bookings/{booking_id}")
def update_booking_status(booking_id: int, status: str, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        if hasattr(booking, 'status'):
            booking.status = status
            db.commit()
            return {"message": "Status updated"}
        else:
            return {"error": "В модели Booking нет поля status"}
    return {"error": "Booking not found"}

@app.delete("/api/bookings/{booking_id}")
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        db.delete(booking)
        db.commit()
        return {"message": "Booking deleted"}
    return {"error": "Booking not found"}

@app.get("/api/subscribers")
def get_subscribers(db: Session = Depends(get_db)):
    subs = db.query(models.Subscriber).order_by(models.Subscriber.id.desc()).all()
    result = []
    for s in subs:
        result.append({
            "id": s.id,
            "email": s.email,
            "created_at": str(s.created_at)
        })
    return result

@app.post("/api/ban_user/{phone}")
def ban_user(phone: str, req: BanRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_banned = True
    user.ban_reason = req.reason
    db.commit()
    return {"message": f"Пользователь {phone} забанен"}

@app.post("/api/unban_user/{phone}")
def unban_user(phone: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_banned = False
    user.ban_reason = None
    db.commit()
    return {"message": f"Пользователь {phone} разбанен"}

@app.get("/api/banned_users")
def get_banned_users(db: Session = Depends(get_db)):
    users = db.query(models.User).filter(models.User.is_banned == True).all()
    return [{"id": u.id, "phone": u.phone, "name": u.name, "ban_reason": u.ban_reason} for u in users]

@app.get("/api/check_ban/{phone}")
def check_ban(phone: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if user and user.is_banned:
        return {"is_banned": True}
    return {"is_banned": False}


# --- ЛОГИКА РАССЫЛКИ ПИСЕМ (RESEND) ---

def send_email_to_subscribers(subject: str, body: str, db: Session):
    subscribers = db.query(models.Subscriber).all()
    emails = [sub.email for sub in subscribers]

    if not emails:
        print("Нет подписчиков для рассылки.")
        return

    try:
        for email in emails:
            params = {
                "from": "UralWay <onboarding@resend.dev>",
                "to": [email],
                "subject": subject,
                "text": body,
            }
            resend.Emails.send(params)
        print(f"Успешно отправлено {len(emails)} писем через Resend.")
    except Exception as e:
        print(f"Ошибка отправки через Resend: {e}")

@app.post("/api/notify_subscribers")
def notify_subscribers(ann: AnnouncementEmail, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    subject = "УралWay: Новый рейс!"
    body = f"""
Здравствуйте!

Опубликован новый рейс:
Дата: {ann.date}
Время: {ann.time}
Адрес отправления: {ann.address}

Забронируйте места на нашем сайте!

Если есть вопросы - свяжитесь с нами: +7 (909) 092-09-89
"""
    background_tasks.add_task(send_email_to_subscribers, subject, body, db)
    return {"message": "Рассылка запущена в фоне"}
