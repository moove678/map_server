from main import app, db

# Важно! Это создаст все таблицы, которые описаны в моделях
with app.app_context():
    db.create_all()
