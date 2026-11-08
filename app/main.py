import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query, Request
import resend
from pydantic import BaseModel, EmailStr, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool
import httpx
import jwt  # PyJWT
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy import or_, inspect, text
from sqlalchemy.orm import Session
from app.database import engine, get_db, Base
from app.models import Subscriber, User, Booking, Route, BookingSegment
from app.schemas import SubscriberCreate, SubscriberResponse, BookingCreate
from datetime import datetime, date as date_type, timedelta, timezone
from typing import List, Tuple, Optional
from . import schemas
from . import models

# Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)


def ensure_booking_columns():
    """create_all не добавляет колонки в уже существующие таблицы. Если в bookings ещё нет новых полей
    (направление рейса и точка посадки) — добавляем их при запуске. Безопасно вызывать повторно."""
    insp = inspect(engine)
    if "bookings" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("bookings")}
    with engine.begin() as conn:
        for name in ("trip_direction", "boarding_time", "boarding_address"):
            if name not in existing:
                conn.execute(text(f"ALTER TABLE bookings ADD COLUMN {name} VARCHAR(255)"))


ensure_booking_columns()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("uralway")

# В продакшене документацию API (/docs, /redoc, /openapi.json) отключаем
IS_PROD = os.getenv("ENV", "").lower() == "production"
app = FastAPI(
    title="УралWay API", version="1.0",
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None if IS_PROD else "/redoc",
    openapi_url=None if IS_PROD else "/openapi.json",
)

# Телефоны администраторов (для писем подписчикам)
ADMIN_PHONES = ["+7 (919) 400-64-14", "+7 (919) 325-15-39"]

resend.api_key = os.getenv("RESEND_API_KEY")

CORS_ORIGIN_REGEX = r"https://([a-z0-9-]+\.)?uralway-pages\.pages\.dev|https://golden-zuccutto-687bfd\.netlify\.app|http://(localhost|127\.0\.0\.1):\d+"

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# БЕЗОПАСНОСТЬ: вход в админку (JWT), лимиты запросов, обработка ошибок
# =====================================================================
# Переменные окружения:
#   ADMIN_PASSWORD  — пароль администратора (обязательно, иначе вход отключён)
#   JWT_SECRET      — секрет для подписи токенов (длинная случайная строка)
#   ENV=production  — отключает /docs, /redoc и /openapi.json
#   TRUSTED_PROXY_HOPS — сколько прокси стоит перед сервером (по умолчанию 1; 0 — сервер доступен напрямую)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=24)
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "1"))

LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")
BOOKING_RATE_LIMIT = os.getenv("BOOKING_RATE_LIMIT", "5/minute")
SUBSCRIBE_RATE_LIMIT = os.getenv("SUBSCRIBE_RATE_LIMIT", "5/minute")
CHECK_BAN_RATE_LIMIT = os.getenv("CHECK_BAN_RATE_LIMIT", "30/minute")

if not JWT_SECRET:
    JWT_SECRET = secrets.token_urlsafe(48)
    logger.warning("JWT_SECRET не задан: создан временный ключ. После перезапуска (и между воркерами) токены "
                   "перестанут работать — задайте JWT_SECRET в переменных окружения.")
if not ADMIN_PASSWORD:
    logger.warning("ADMIN_PASSWORD не задан — вход в админку отключён.")

SERVER_ERROR_MESSAGE = "Внутренняя ошибка сервера. Попробуйте позже."
RATE_LIMIT_MESSAGE = "Слишком много запросов. Подождите минуту и попробуйте снова."


def client_ip(request: Request) -> str:
    """IP клиента за прокси: берём адрес, который дописал ближайший доверенный прокси (с конца X-Forwarded-For).
    Так подделанный клиентом заголовок не позволяет обойти лимиты."""
    xff = request.headers.get("x-forwarded-for")
    if xff and TRUSTED_PROXY_HOPS > 0:
        ips = [p.strip() for p in xff.split(",") if p.strip()]
        if ips:
            return ips[-min(TRUSTED_PROXY_HOPS, len(ips))]
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=client_ip)
app.state.limiter = limiter

bearer_scheme = HTTPBearer(auto_error=False)


def verify_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)):
    """Зависимость для защищённых эндпоинтов: требует заголовок Authorization: Bearer <token>."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Требуется авторизация", headers={"WWW-Authenticate": "Bearer"})
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Сессия истекла. Войдите снова.", headers={"WWW-Authenticate": "Bearer"})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Недействительный токен", headers={"WWW-Authenticate": "Bearer"})


def _cors_headers(request: Request) -> dict:
    """Необработанные ошибки (500) формируются снаружи CORS-middleware — добавляем заголовки вручную,
    иначе браузер покажет CORS-ошибку вместо понятного сообщения."""
    origin = request.headers.get("origin")
    if origin and re.fullmatch(CORS_ORIGIN_REGEX, origin):
        return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Credentials": "true", "Vary": "Origin"}
    return {}


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    logger.warning("Превышен лимит запросов: %s %s с IP %s", request.method, request.url.path, client_ip(request))
    return JSONResponse(status_code=429, content={"error": RATE_LIMIT_MESSAGE, "detail": RATE_LIMIT_MESSAGE},
                        headers={"Retry-After": "60"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Подробности (с traceback) — только в логи, клиенту — аккуратный JSON
    logger.exception("Необработанная ошибка: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": SERVER_ERROR_MESSAGE}, headers=_cors_headers(request))


class LoginRequest(BaseModel):
    password: str = Field(max_length=200)


@app.get("/api/health")
def health():
    """Для мониторинга: сервер жив."""
    return {"status": "ok"}


@app.post("/api/login")
@limiter.limit(LOGIN_RATE_LIMIT)
def login(request: Request, data: LoginRequest):
    """Вход администратора: пароль из ADMIN_PASSWORD -> JWT на 24 часа."""
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Вход в админку не настроен на сервере")
    if not hmac.compare_digest(data.password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")):
        logger.warning("Неудачная попытка входа в админку с IP %s", client_ip(request))
        raise HTTPException(status_code=401, detail="Неверный пароль")
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": "admin", "iat": now, "exp": now + TOKEN_TTL}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer", "expires_in": int(TOKEN_TTL.total_seconds())}


@app.get("/api/me", dependencies=[Depends(verify_token)])
def me():
    """Проверка токена (фронтенд вызывает при открытии админки)."""
    return {"role": "admin"}


# =====================================================================
# ЯНДЕКС.МЕТРИКА: серверная отправка офлайн-заявок (Measurement Protocol)
# =====================================================================
# Заявки, которые админ вносит вручную (клиент позвонил), веб-счётчик не видит. Мы создаём в Метрике
# «виртуальный визит»: просмотр страницы -> цель -> покупка (электронная коммерция).
# Документация: https://yandex.com/dev/metrika/ru/data-import/measurement-upload
#
# Настройка (один раз): в счётчике «Настройка → Загрузка данных → Measurement Protocol» включите опцию
# и скопируйте секретный токен в переменную окружения METRIKA_MP_TOKEN. Без токена данные не принимаются.
# В Метрике создайте цель типа «JavaScript-событие» с идентификатором offline_booking_success.
METRIKA_COUNTER_ID = os.getenv("METRIKA_COUNTER_ID", "114068100")
METRIKA_MP_TOKEN = os.getenv("METRIKA_MP_TOKEN")
METRIKA_COLLECT_URL = "https://mc.yandex.ru/collect"
SITE_URL = os.getenv("SITE_URL", "https://uralway-pages.pages.dev").rstrip("/")
OFFLINE_PAGE_URL = f"{SITE_URL}/offline-booking"      # виртуальная страница офлайн-визита
OFFLINE_GOAL_ID = "offline_booking_success"           # идентификатор JS-цели в Метрике
DIRECTION_LABELS = {"forward": "Туда", "return": "Обратно"}


def _new_client_id() -> str:
    """ClientID офлайн-клиента. У звонящего нет браузерной cookie, поэтому создаём новый идентификатор
    в том же числовом формате, что и _ym_uid (10 цифр unix-времени + 9 случайных цифр)."""
    return f"{int(time.time())}{secrets.randbelow(10 ** 9):09d}"


def _money(value: float) -> str:
    value = round(float(value), 2)
    return str(int(value)) if value == int(value) else f"{value:.2f}"


def build_offline_metrika_hits(*, booking_id: int, total_price: int, direction: str, segments, cid: str, now: int) -> list:
    """Три запроса одного визита (без токена): pageview -> цель -> покупка.
    Время хитов (et) идёт по возрастанию и не уходит в будущее."""
    seats = sum(s.seats for s in segments)
    route_text = "; ".join(f"{s.from_city}-{s.to_city}" for s in segments)
    base = {"tid": METRIKA_COUNTER_ID, "cid": cid, "dl": OFFLINE_PAGE_URL}

    # Параметры визита: сумма, маршрут, количество мест (вместо «ep» из ТЗ — в протоколе это поле params)
    visit_params = json.dumps({
        "order_id": str(booking_id), "source": "manual", "order_price": total_price,
        "route": route_text, "seats": seats, "direction": direction,
    }, ensure_ascii=False)

    # Товары покупки: по строке на участок (если известна цена участка), иначе одним товаром на всю заявку
    if segments and all(s.price is not None for s in segments):
        products = [(f"{s.from_city}-{s.to_city}", f"{s.from_city} — {s.to_city}", s.price / s.seats, s.seats) for s in segments]
    else:
        products = [(route_text or "booking", route_text or "Заявка", total_price / max(seats, 1), max(seats, 1))]
    purchase = {**base, "t": "event", "et": now, "pa": "purchase", "ti": str(booking_id), "tr": total_price, "cu": "RUB"}
    for i, (pid, name, price, qty) in enumerate(products, start=1):
        purchase.update({f"pr{i}id": pid, f"pr{i}nm": name, f"pr{i}br": "УралWay",
                         f"pr{i}ca": DIRECTION_LABELS.get(direction, direction),
                         f"pr{i}pr": _money(price), f"pr{i}qt": qty})

    return [
        {**base, "t": "pageview", "et": now - 2, "dr": f"{SITE_URL}/", "dt": "Офлайн-заявка"},
        {**base, "t": "event", "et": now - 1, "ea": OFFLINE_GOAL_ID, "ev": total_price, "cu": "RUB", "params": visit_params},
        purchase,
    ]


async def send_offline_booking_to_metrika(*, booking_id: int, total_price: int, direction: str, segments) -> bool:
    """Отправляет офлайн-заявку в Метрику. Никогда не выбрасывает исключений: сбой аналитики
    не должен ломать создание заявки. Возвращает True, если все запросы приняты."""
    if not METRIKA_MP_TOKEN:
        print("Ошибка отправки в Метрику")
        logger.warning("METRIKA_MP_TOKEN не задан — офлайн-заявка %s в Метрику не отправлена", booking_id)
        return False
    try:
        hits = build_offline_metrika_hits(
            booking_id=booking_id, total_price=int(total_price or 0), direction=direction,
            segments=segments, cid=_new_client_id(), now=int(time.time()),
        )
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            for hit in hits:
                response = await client.post(METRIKA_COLLECT_URL, params={**hit, "ms": METRIKA_MP_TOKEN})
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}")
        logger.info("Офлайн-заявка %s отправлена в Метрику", booking_id)
        return True
    except Exception as exc:
        print("Ошибка отправки в Метрику")
        # URL запроса содержит секретный токен — в лог пишем только тип ошибки
        logger.warning("Ошибка отправки в Метрику (заявка %s): %s", booking_id, type(exc).__name__)
        return False


class BanRequest(BaseModel):
    reason: str = Field(max_length=255)

class AnnouncementEmail(BaseModel):
    date: str
    time: str
    address: str
    # Необязательно: обратный рейс того же цикла
    return_date: Optional[str] = None
    return_time: Optional[str] = None
    return_address: Optional[str] = None

@app.get("/")
def read_root():
    return {"message": "УралWay API работает!", "timestamp": datetime.now()}

@app.post("/api/subscribe", response_model=SubscriberResponse)
@limiter.limit(SUBSCRIBE_RATE_LIMIT)
def subscribe(request: Request, subscriber: SubscriberCreate, db: Session = Depends(get_db)):
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


def get_occupancy(trip_date, trip_time, db: Session, direction: str = "forward") -> List[int]:
    """Сколько мест занято на каждом участке рейса. Отменённые заявки места освобождают.
    Рейс = дата + время + направление: «туда» и «обратно» считаются независимо."""
    occupied = [0] * len(SEGMENT_KEYS)
    if direction == "forward":
        # старые заявки без направления относятся к прямому рейсу
        direction_cond = or_(models.Booking.trip_direction == "forward", models.Booking.trip_direction.is_(None))
    else:
        direction_cond = models.Booking.trip_direction == direction
    bookings = db.query(models.Booking).filter(
        models.Booking.trip_date == trip_date,
        models.Booking.trip_time == normalize_time(trip_time),
        direction_cond,
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


def check_segments_availability(trip_date, trip_time, requests: List[Tuple[str, str, int]], db: Session, direction: str = "forward") -> bool:
    """Проверка сразу всех строк заявки: если строки пересекаются по участкам, места суммируются."""
    occupied = get_occupancy(trip_date, trip_time, db, direction)
    extra = [0] * len(SEGMENT_KEYS)
    for from_city, to_city, seats in requests:
        for i in segment_indices(from_city, to_city):
            extra[i] += seats
    return all(occupied[i] + extra[i] <= TOTAL_SEATS for i in range(len(SEGMENT_KEYS)))


def check_seat_availability(announcement_date, announcement_time, from_city, to_city, requested_seats, db, direction: str = "forward") -> bool:
    """True — если на КАЖДОМ участке от from_city до to_city свободно не меньше requested_seats."""
    return check_segments_availability(
        announcement_date, announcement_time, [(from_city, to_city, requested_seats)], db, direction
    )


# =====================================================================
# НАПРАВЛЕНИЯ РЕЙСА
# =====================================================================
# Адреса и время посадки выбирает сам пассажир на сайте (CITY_STOPS в script.js)
# и присылает в полях boarding_time / boarding_address — сервер их просто сохраняет.
#   "forward" — Магнитогорск ➔ Оренбург, "return" — Оренбург ➔ Магнитогорск
DIRECTIONS = ("forward", "return")
DIRECTION_MISMATCH_MESSAGE = "Выбранный участок не соответствует направлению рейса"


def segments_match_direction(segments, direction: str) -> bool:
    """Прямой рейс идёт по возрастанию CITIES_ORDER, обратный — по убыванию.
    Города вне CITIES_ORDER не проверяются."""
    for from_city, to_city, _seats in segments:
        a, b = CITY_INDEX.get(from_city), CITY_INDEX.get(to_city)
        if a is None or b is None:
            continue
        if direction == "forward" and a > b:
            return False
        if direction == "return" and a < b:
            return False
    return True


@app.get("/api/availability")
def get_availability(
    trip_date: date_type = Query(..., alias="date"),
    trip_time: str = Query(..., alias="time"),
    direction: str = Query("forward"),
    db: Session = Depends(get_db),
):
    """Карта занятости рейса: {"Магнитогорск-Сибай": 8, "Сибай-Баймак": 5, ...} (занятые места).
    direction: forward (туда) или return (обратно) — у каждого направления своя шкала."""
    if direction not in DIRECTIONS:
        raise HTTPException(status_code=400, detail="direction должен быть forward или return")
    occupied = get_occupancy(trip_date, trip_time, db, direction)
    return {SEGMENT_KEYS[i]: occupied[i] for i in range(len(SEGMENT_KEYS))}


@app.post("/api/booking")
@limiter.limit(BOOKING_RATE_LIMIT)
def create_booking(request: Request, booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    """Заявка с сайта. Поле source из публичного запроса игнорируется: в аналитику как «офлайн» не попадает."""
    return _process_booking(booking, db, require_consent=True)


@app.post("/api/admin/booking", dependencies=[Depends(verify_token)])
async def create_manual_booking(booking: schemas.BookingCreate, db: Session = Depends(get_db)):
    """Ручная заявка (клиент позвонил). Только для администратора. Создаёт заявку и передаёт её в Метрику.
    Сбой аналитики заявку не отменяет — в ответе поле analytics_sent показывает результат."""
    result = await run_in_threadpool(_process_booking, booking, db, False)
    segments = booking.segments or [schemas.BookingSegmentIn(
        from_city=booking.from_city, to_city=booking.to_city, seats=booking.passengers)]
    analytics_sent = await send_offline_booking_to_metrika(
        booking_id=result["booking_id"], total_price=booking.total_price or 0,
        direction=booking.trip_direction or "forward", segments=segments)
    return {**result, "source": "manual", "analytics_sent": analytics_sent}


def _process_booking(booking: schemas.BookingCreate, db: Session, require_consent: bool):
    """Общая логика создания заявки (проверка бана, мест по участкам, запись в БД)."""
    user = db.query(models.User).filter(models.User.phone == booking.phone).first()

    if user and user.is_banned:
        raise HTTPException(status_code=403, detail="Пользователь забанен. Бронирование невозможно.")

    if require_consent and booking.consent is not True:
        raise HTTPException(status_code=400, detail="Необходимо согласие на обработку персональных данных")

    # Участки заявки: из segments, либо (старые клиенты) из from_city/to_city/passengers
    if booking.segments:
        segments = [(s.from_city, s.to_city, s.seats) for s in booking.segments]
    else:
        segments = [(booking.from_city, booking.to_city, booking.passengers)]

    for from_city, to_city, _seats in segments:
        if from_city == to_city:
            raise HTTPException(status_code=400, detail="Пункты отправления и назначения совпадают")

    direction = booking.trip_direction or "forward"
    if not segments_match_direction(segments, direction):
        raise HTTPException(status_code=400, detail=DIRECTION_MISMATCH_MESSAGE)

    total_passengers = sum(seats for _f, _t, seats in segments)
    trip_time = normalize_time(getattr(booking, 'trip_time', None))

    # Точка посадки приходит с сайта. Время может быть пустым (пока уточняется) — сохраняем как NULL.
    boarding_time = (booking.boarding_time or "").strip() or None
    boarding_address = (booking.boarding_address or "").strip() or None

    with _booking_lock:
        if not check_segments_availability(booking.trip_date, trip_time, segments, db, direction):
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
            trip_direction=direction,
            boarding_time=boarding_time,
            boarding_address=boarding_address,
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


@app.get("/api/bookings", dependencies=[Depends(verify_token)])
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
            "trip_direction": getattr(b, 'trip_direction', None) or "forward",
            "boarding_time": getattr(b, 'boarding_time', None),
            "boarding_address": getattr(b, 'boarding_address', None),
            "passengers": b.passengers,
            "comment": b.comment,
            "status": getattr(b, 'status', 'new'),
            "total_price": getattr(b, 'total_price', 0),
            "total_km": getattr(b, 'total_km', 0),
            "is_banned": user.is_banned if user else False
        })
    return result

@app.patch("/api/bookings/{booking_id}", dependencies=[Depends(verify_token)])
def update_booking_status(booking_id: int, status: str, db: Session = Depends(get_db)):
    if status not in ("new", "confirmed", "cancelled", "completed"):
        raise HTTPException(status_code=400, detail="Недопустимый статус заявки")
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        if hasattr(booking, 'status'):
            booking.status = status
            db.commit()
            return {"message": "Status updated"}
        else:
            return {"error": "В модели Booking нет поля status"}
    return {"error": "Booking not found"}

@app.delete("/api/bookings/{booking_id}", dependencies=[Depends(verify_token)])
def delete_booking(booking_id: int, db: Session = Depends(get_db)):
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if booking:
        db.query(models.BookingSegment).filter(models.BookingSegment.booking_id == booking_id).delete()
        db.delete(booking)
        db.commit()
        return {"message": "Booking deleted"}
    return {"error": "Booking not found"}

@app.get("/api/subscribers", dependencies=[Depends(verify_token)])
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

@app.post("/api/ban_user/{phone}", dependencies=[Depends(verify_token)])
def ban_user(phone: str, req: BanRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_banned = True
    user.ban_reason = req.reason
    db.commit()
    return {"message": f"Пользователь {phone} забанен"}

@app.post("/api/unban_user/{phone}", dependencies=[Depends(verify_token)])
def unban_user(phone: str, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.phone == phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.is_banned = False
    user.ban_reason = None
    db.commit()
    return {"message": f"Пользователь {phone} разбанен"}

@app.get("/api/banned_users", dependencies=[Depends(verify_token)])
def get_banned_users(db: Session = Depends(get_db)):
    users = db.query(models.User).filter(models.User.is_banned == True).all()
    return [{"id": u.id, "phone": u.phone, "name": u.name, "email": u.email, "ban_reason": u.ban_reason} for u in users]

@app.get("/api/check_ban/{phone}")
@limiter.limit(CHECK_BAN_RATE_LIMIT)
def check_ban(request: Request, phone: str, db: Session = Depends(get_db)):
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

@app.post("/api/notify_subscribers", dependencies=[Depends(verify_token)])
def notify_subscribers(ann: AnnouncementEmail, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    subject = "УралWay: Новый рейс!"
    return_block = ""
    if ann.return_date and ann.return_time:
        return_block = f"""
Обратный рейс (Оренбург ➔ Магнитогорск):
Дата: {ann.return_date}
Время: {ann.return_time}
Адрес отправления: {ann.return_address or '—'}
"""
    phones_text = "\n".join(ADMIN_PHONES)
    body = f"""
Здравствуйте!

Опубликован новый рейс:
Дата: {ann.date}
Время: {ann.time}
Адрес отправления: {ann.address}
{return_block}
Забронируйте места на нашем сайте!

Если есть вопросы - свяжитесь с нами:
{phones_text}
"""
    background_tasks.add_task(send_email_to_subscribers, subject, body, db)
    return {"message": "Рассылка запущена в фоне"}
