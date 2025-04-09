from flask.cli import FlaskGroup

from main import app, db  # Убедись, что у тебя файл называется main.py

cli = FlaskGroup(create_app=lambda: app)

if __name__ == '__main__':
    cli()
