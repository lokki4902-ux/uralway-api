from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

# Загружаем переменные из .env файла
load_dotenv()

# Получаем URL базы данных из .env
DATABASE_URL = os.getenv("DATABASE_URL")

# Создаем движок для подключения к БД
engine = create_engine(DATABASE_URL)

# Создаем сессию для работы с БД
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Базовый класс для моделей
Base = declarative_base()

# Функция для получения сессии БД (используется в API)
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()