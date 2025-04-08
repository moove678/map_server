#!/bin/bash

echo "== Удаление SQLite базы =="
rm -f mydb.sqlite

echo "== Удаление папки миграций =="
rm -rf migrations

echo "== Экспорт переменной FLASK_APP =="
export FLASK_APP=main.py

echo "== Инициализация Alembic (Flask-Migrate) =="
flask db init

echo "== Создание начальной миграции =="
flask db migrate -m "Initial migration"

echo "== Применение миграции =="
flask db upgrade

echo "== Готово. Хотите закоммитить изменения в Git? (y/n) =="
read confirm

if [ "$confirm" = "y" ]; then
    git add .
    git commit -m "Recreated DB and migrations"
    git push
    echo "Изменения отправлены на GitHub"
else
    echo "Окей, изменения не отправлены."
fi
