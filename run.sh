#!/bin/bash

echo "========== [1/6] Запуск PostgreSQL =========="
sudo service postgresql start

echo "========== [2/6] Проверка соединения с базой =========="
python3 check_db.py
if [ $? -ne 0 ]; then
  echo "Ошибка подключения к БД. Останавливаемся."
  exit 1
fi

echo "========== [3/6] Применение миграций =========="
flask db upgrade

echo "========== [4/6] Запуск Flask сервера =========="
# Запускаем Flask сервер в фоне
python3 main.py &
FLASK_PID=$!
sleep 3

echo "========== [5/6] Запуск ngrok =========="
# Убиваем старые процессы ngrok на всякий случай
pkill -f ngrok
sleep 1

# Запуск ngrok в фоне
./ngrok http 5000 > /dev/null &
sleep 3

echo "========== [6/6] Получение публичного адреса ngrok =========="
NGROK_URL=$(curl -s http://localhost:4040/api/tunnels | grep -oE 'https://[a-z0-9\-]+\.ngrok[-free]*\.app')
if [ -z "$NGROK_URL" ]; then
  echo "!!!! Не удалось получить публичный адрес ngrok"
  echo ">>> Возможные причины:"
  echo "- ngrok не авторизован (добавь токен)"
  echo "- порт 4040 или 5000 занят"
  echo "- не установлен ./ngrok"
else
  echo ">>> Ngrok публичный адрес: $NGROK_URL"
fi

# Ожидаем завершения Flask сервера
wait $FLASK_PID
