import os
import threading
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
import resend
from pydantic import BaseModel, EmailStr
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Subscriber, User, Booking, Route, BookingSegment
from app.schemas import SubscriberCreate, SubscriberResponse, BookingCreate
from datetime import datetime, date as date_type
from typing import List, Tuple
from . import schemas
from . import models

# Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="УралWay API", version="1.0")

resend.api_key = os.getenv("RESEND_API_KEY")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BanRequest(BaseModel):
    reason: str

class AnnouncementEmail(BaseModel):
    date: str
    time: str
    address: str

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

# =====================================================================
# СЕГМЕНТНОЕ БРОНИРОВАНИЕ
# =====================================================================

# Города маршрута СТРОГО по порядку. Участок i — это отрезок между
# CITIES_ORDER[i] и CITIES_ORDER[i + 1].
CITIES_ORDER = [
    "Магнитогорск",
    "Сибай",
    "Баймак",
    "Юлдыбаево",
    "Зилаир",
    "Исянгулово",
    "Саракташ",
    "Оренбург",
]
CITY_INDEX = {city: i for i, city in enumerate(CITIES_ORDER)}
SEGMENT_KEYS = [f"{CITIES_ORDER[i]}-{CITIES_ORDER[i + 1]}" for i in range(len(CITIES_ORDER) - 1)]
TOTAL_SEATS = 8
NO_SEATS_MESSAGE = "На выбранном участке маршрута недостаточно свободных мест"

# Защита от гонки «проверил — записал» внутри одного процесса.
# При нескольких воркерах uvicorn/gunicorn нужна блокировка на уровне БД.
_booking_lock = threading.Lock()


def normalize_time(value):
    """'08:00:00' -> '08:00', чтобы время рейса совпадало независимо от формата."""
    if not value:
        return None
    return str(value).strip()[:5]


def segment_indices(from_city: str, to_city: str) -> List[int]:
    """Номера участков, которые проезжает пассажир (направление не важно).
    Город вне CITIES_ORDER (Кугарчи, Аманат, Тазларово) консервативно считается
    занимающим ВЕСЬ маршрут, чтобы не допустить овербукинг."""
    a, b = CITY_INDEX.get(from_city), CITY_INDEX.get(to_city)
    if a is None or b is None:
        return list(range(len(SEGMENT_KEYS)))
    if a == b:
        return []
    lo, hi = sorted((a, b))
    return list(range(lo, hi))


def get_occupancy(trip_date, trip_time, db: Session) -> List[int]:
    """Сколько мест занято на каждом участке рейса. Отменённые заявки места освобождают."""
    occupied = [0] * len(SEGMENT_KEYS)
    bookings = db.query(models.Booking).filter(
        models.Booking.trip_date == trip_date,
        models.Booking.trip_time == normalize_time(trip_time),
    ).all()
    bookings = [b for b in bookings if (b.status or "new") != "cancelled"]
    if not bookings:
        return occupied

    seg_rows = db.query(models.BookingSegment).filter(
        models.BookingSegment.booking_id.in_([b.id for b in bookings])
    ).all()
    by_booking = {}
    for s in seg_rows:
        by_booking.setdefault(s.booking_id, []).append(s)

    for b in bookings:
        segs = by_booking.get(b.id)
        if segs:
            items = [(s.from_city, s.to_city, s.seats) for s in segs]
        else:
            # Старые заявки без сегментов: один маршрут на всех пассажиров
            route = db.query(models.Route).filter(models.Route.id == b.route_id).first()
            items = [(route.from_city, route.to_city, b.passengers or 1)] if route else []
        for from_city, to_city, seats in items:
            for i in segment_indices(from_city, to_city):
                occupied[i] += seats
    return occupied


def check_segments_availability(trip_date, trip_time, requests: List[Tuple[str, str, int]], db: Session) -> bool:
    """Проверка сразу всех строк заявки: если строки пересекаются по участкам, места суммируются."""
    occupied = get_occupancy(trip_date, trip_time, db)
    extra = [0] * len(SEGMENT_KEYS)
    for from_city, to_city, seats in requests:
        for i in segment_indices(from_city, to_city):
            extra[i] += seats
    return all(occupied[i] + extra[i] <= TOTAL_SEATS for i in range(len(SEGMENT_KEYS)))


def check_seat_availability(announcement_date, announcement_time, from_city, to_city, requested_seats, db) -> bool:
    """True — если на КАЖДОМ участке от from_city до to_city свободно не меньше requested_seats."""
    return check_segments_availability(
        announcement_date, announcement_time, [(from_city, to_city, requested_seats)], db
    )


@app.get("/api/availability")
def get_availability(
    trip_date: date_type = Query(..., alias="date"),
    trip_time: str = Query(..., alias="time"),
    db: Session = Depends(get_db),
):
    """Карта занятости рейса: {"Магнитогорск-Сибай": 8, "Сибай-Баймак": 5, ...} (занятые места)."""
    occupied = get_occupancy(trip_date, trip_time, db)
    return {SEGMENT_KEYS[i]: occupied[i] for i in range(len(SEGMENT_KEYS))}


@app.post("/api/booking")
def create_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == booking.phone).first()

    if user and user.is_banned:
        raise HTTPException(status_code=403, detail="Пользователь забанен. Бронирование невозможно.")

    # Участки заявки: из segments, либо (старые клиенты) из from_city/to_city/passengers
    if booking.segments:
        segments = [(s.from_city, s.to_city, s.seats) for s in booking.segments]
    else:
        segments = [(booking.from_city, booking.to_city, booking.passengers)]

    for from_city, to_city, _seats in segments:
        if from_city == to_city:
            raise HTTPException(status_code=400, detail="Пункты отправления и назначения совпадают")

    total_passengers = sum(seats for _f, _t, seats in segments)
    trip_time = normalize_time(getattr(booking, 'trip_time', None))

    with _booking_lock:
        if not check_segments_availability(booking.trip_date, trip_time, segments, db):
            raise HTTPException(status_code=400, detail=NO_SEATS_MESSAGE)

        if not user:
            user = models.User(phone=booking.phone, name=booking.name, email=booking.email)
            db.add(user)
            db.commit()
            db.refresh(user)

        route = db.query(models.Route).filter(
            models.Route.from_city == booking.from_city,
            models.Route.to_city == booking.to_city
        ).first()

        if not route:
            route = models.Route(from_city=booking.from_city, to_city=booking.to_city)
            db.add(route)
            db.commit()
            db.refresh(route)

        new_booking = models.Booking(
            user_id=user.id,
            route_id=route.id,
            trip_date=booking.trip_date,
            passengers=total_passengers,
            comment=booking.comment,
            trip_time=trip_time,
            trip_address=getattr(booking, 'trip_address', None),
            status='new',
            total_price=getattr(booking, 'total_price', 0),
            total_km=getattr(booking, 'total_km', 0)
        )
        db.add(new_booking)
        db.flush()  # получаем new_booking.id

        for from_city, to_city, seats in segments:
            db.add(models.BookingSegment(
                booking_id=new_booking.id, from_city=from_city, to_city=to_city, seats=seats
            ))
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
        db.query(models.BookingSegment).filter(models.BookingSegment.booking_id == booking_id).delete()
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
