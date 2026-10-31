# uralway-api
markdown
# УралWay — Сервис междугородних пассажирских перевозок

## 1. Общая информация о проекте
**УралWay** — это веб-приложение для бронирования мест на междугородние рейсы (маршрут: Магнитогорск ⇔ Оренбург). 
Проект реализован по архитектуре **Client-Server** и включает в себя: клиентскую часть (сайт), серверную часть (API), базу данных (PostgreSQL) и панель администратора.

**Ссылки на продакшн:**
*   **Фронтенд (Сайт):** https://golden-zuccutto-687bfd.netlify.app/
*   **Бэкенд (API):** https://uralway-api.de1.netrun.io
*   **Swagger (Документация API):** https://uralway-api.de1.netrun.io/docs

## 2. Технологический стек
*   **Frontend:** HTML5, CSS3, Vanilla JavaScript (ES6+). Хостинг: Netlify.
*   **Backend:** Python 3.10, FastAPI, SQLAlchemy, Pydantic. Хостинг: Netrun.
*   **Database:** PostgreSQL 15. (Развернута в Docker-контейнере внутри Netrun).
*   **Дополнительно:** Resend API (для email-рассылок).

## 3. Архитектура и хранение данных
*   **Фронтенд** (Netlify) отправляет HTTP-запросы к **Бэкенду** (Netrun).
*   **Бэкенд** обрабатывает запросы (валидация, проверка на бан) и сохраняет данные в **Базу данных** (PostgreSQL).
*   Доступ к базе данных осуществляется только через API. Прямое подключение через pgAdmin к продакшн-БД закрыто в целях безопасности.

## 4. Структура базы данных
В PostgreSQL созданы следующие таблицы:

### 4.1. `users` (Пользователи)
*   `id` (Integer, PK) — уникальный идентификатор.
*   `phone` (String) — номер телефона (уникальный).
*   `name` (String) — имя пользователя.
*   `email` (String) — email.
*   `is_banned` (Boolean) — флаг блокировки.
*   `ban_reason` (String) — причина блокировки.
*   `created_at` (DateTime) — дата регистрации.

### 4.2. `bookings` (Заявки на бронирование)
*   `id` (Integer, PK) — номер заявки.
*   `user_id` (Integer, FK) — ссылка на таблицу `users`.
*   `route_id` (Integer, FK) — ссылка на таблицу `routes`.
*   `trip_date` (Date) — дата поездки.
*   `trip_time` (String) — время отправления.
*   `trip_address` (String) — адрес отправления.
*   `passengers` (Integer) — количество забронированных мест.
*   `comment` (String) — детали маршрута (откуда-куда).
*   `total_price` (Integer) — итоговая стоимость.
*   `total_km` (Integer) — километраж.
*   `status` (String) — статус (new, confirmed, cancelled, completed).

### 4.3. `subscribers` (Email-подписчики)
*   `id` (Integer, PK) — номер записи.
*   `email` (String) — email (уникальный).
*   `created_at` (DateTime) — дата подписки.

### 4.4. `routes` (Маршруты)
*   `id` (Integer, PK) — номер маршрута.
*   `from_city` (String) — город отправления.
*   `to_city` (String) — город назначения.

## 5. Работа с API (Эндпоинты)

### 5.1. Создание заявки (POST `/api/booking`)
Этот эндпоинт принимает данные из формы на сайте и сохраняет заявку в БД.

**URL:** `https://uralway-api.de1.netrun.io/api/booking`
**Метод:** `POST`
**Заголовки:** `Content-Type: application/json`

**Тело запроса (JSON):**
```json
{
  "name": "Иван",
  "phone": "+7 (999) 111-22-33",
  "email": "ivan@mail.ru",
  "from_city": "Магнитогорск",
  "to_city": "Оренбург",
  "trip_date": "2026-11-01",
  "trip_time": "12:00",
  "trip_address": "г. Магнитогорск, ул. Ленина 1",
  "passengers": 2,
  "comment": "Маршруты: Магнитогорск — Оренбург (436 км, 2 чел.)",
  "total_price": 4600,
  "total_km": 436
}
Примечание: Если пользователь с таким phone находится в бан-листе (is_banned = true), сервер вернет ошибку 403 Forbidden.

Успешный ответ (200 OK):

json
{
  "message": "Бронирование создано!",
  "booking_id": 1
}
Как протестировать:

Перейди в Swagger: https://uralway-api.de1.netrun.io/docs

Найди POST /api/booking.

Нажми Try it out.

Вставь JSON из примера выше и нажми Execute.

В блоке Response ты увидишь результат.

5.2. Остальные эндпоинты:
POST /api/subscribe — подписка на рассылку (принимает {"email": "..."}).

GET /api/bookings — получение списка всех заявок (для админки).

PATCH /api/bookings/{id}?status=... — изменение статуса заявки.

DELETE /api/bookings/{id} — удаление заявки.

GET /api/subscribers — получение списка подписчиков.

POST /api/ban_user/{phone} — бан пользователя (принимает {"reason": "..."}).

POST /api/unban_user/{phone} — разбан.

GET /api/banned_users — список забаненных.

## 🖥️ 6. Панель администратора (admin.html)
Доступна по адресу: https://golden-zuccutto-687bfd.netlify.app/admin.html

*   **Пароль:** admin123 (хранится в script.js).
*   **Функционал:**
    *   Просмотр и управление заявками (подтверждение, отмена, удаление, бан).
    *   Просмотр базы подписчиков.
    *   Управление бан-листом.
    *   Создание анонсов новых рейсов (с функцией ручного указания занятых мест).

## 🚀 7. Локальный запуск (для разработки)
1. Установите Python 3.10+ и PostgreSQL.
2. Клонируйте репозиторий: git clone https://github.com/lokki4902-ux/uralway-api.git
3. Создайте виртуальное окружение: python -m venv venv
4. Активируйте его: venv\Scripts\activate (Windows).
5. Установите зависимости: pip install -r requirements.txt
6. Настройте .env файл с локальной строкой подключения к БД.
7. Запустите сервер: uvicorn app.main:app --reload
