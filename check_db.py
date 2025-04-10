import os
import psycopg2
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
load_dotenv()

# Чтение параметров подключения из .env
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Проверка соединения
try:
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    conn.close()
    print("Успешное подключение к базе данных.")
    exit(0)
except Exception as e:
    print("Ошибка подключения к базе данных:")
    print(e)
    exit(1)
