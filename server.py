import os
import subprocess
import shlex
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

# Абсолютный путь к твоей папке со сборкой (где лежит main.py и buildozer.spec)
BUILD_DIR = "/home/ubuntu/PythonAPKProjects"
# Папка, где появляется готовый APK (по умолчанию bin/)
BIN_DIR = os.path.join(BUILD_DIR, "bin")

@app.route("/")
def index():
    return "Сервер сборки APK запущен и работает!"

@app.route("/upload", methods=["POST"])
def upload():
    """
    Принимает форму (multipart/form-data или application/x-www-form-urlencoded) с полями:
      - filename (например, 'main.py' или 'buildozer.spec')
      - code (текст кода)
    Очищает файл, записывает 'code', запускает сборку buildozer.
    Возвращает JSON с логом сборки и ссылкой на APK.
    """
    filename = request.form.get("filename")
    code = request.form.get("code")

    if not filename or not code:
        return jsonify({"error": "Поля 'filename' и 'code' обязательны"}), 400

    # Путь к нужному файлу в BUILD_DIR
    file_path = os.path.join(BUILD_DIR, filename)

    # Проверяем, что файл лежит в папке BUILD_DIR (простейшая защита)
    if not os.path.abspath(file_path).startswith(os.path.abspath(BUILD_DIR)):
        return jsonify({"error": "Недопустимое имя файла"}), 400

    # Записываем новый код, стирая старое содержимое
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return jsonify({"error": f"Не удалось записать файл: {str(e)}"}), 500

    # Запускаем buildozer
    cmd = ["buildozer", "-v", "android", "debug"]
    try:
        process = subprocess.run(
            cmd,
            cwd=BUILD_DIR,
            capture_output=True,
            text=True,
            check=False  # не кидаем исключение, если сборка завершится с ошибкой
        )
    except Exception as e:
        return jsonify({"error": f"Не удалось запустить buildozer: {str(e)}"}), 500

    # Лог сборки (stdout + stderr)
    build_log = process.stdout + "\n" + process.stderr

    # Имя файла с APK — по умолчанию Buildozer кладёт в bin/<название>-0.1-debug.apk
    # Если название приложения в buildozer.spec = MapApp, он сделает MapApp-0.1-debug.apk и т.п.
    # Ниже просто пример — при необходимости адаптируй:
    apk_name = "MapApp-0.1-debug.apk"

    # Формируем ссылку для скачивания
    download_url = f"{request.host_url}download/{apk_name}"

    # Возвращаем результат
    return jsonify({
        "message": "Сборка завершена",
        "build_log": build_log,
        "apk_url": download_url
    })

@app.route("/download/<path:filename>", methods=["GET"])
def download(filename):
    """
    Позволяет скачать любой файл из папки bin/
    Например, /download/MapApp-0.1-debug.apk
    """
    return send_from_directory(BIN_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    # Запуск Flask-сервера на порту 8000, слушаем все интерфейсы
    app.run(host="0.0.0.0", port=8001, debug=False)
