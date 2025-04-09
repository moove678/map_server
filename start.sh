#!/bin/bash
echo "Running migrations..."
python manage.py db upgrade
echo "Starting server..."
gunicorn main:app
