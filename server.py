import os
import shutil
import uuid
import threading
import queue
import subprocess
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

###############################################################################
# НАСТРОЙКИ
###############################################################################
# Главная папка, где всё храним, УЧТИ, что user = name заменяй на нужное имя
PROJECTS_ROOT = "/home/name/PythonAPKProjects"

# Подпапка для сборки
BUILD_SUBDIR = "buildarea"

# Очередь задач на сборку
build_queue = queue.Queue()

# Информация о сборках: build_id -> { ... }
builds = {}


###############################################################################
# ФУНКЦИЯ-РАБОТНИК: БЕРЁТ ИЗ ОЧЕРЕДИ BUILD_ID И СТРОИТ
###############################################################################
def build_worker():
    while True:
        build_id = build_queue.get()
        if build_id is None:
            continue

        info = builds.get(build_id)
        if not info:
            continue

        info["status"] = "building"
        project_id = info["project_id"]

        # Папка проекта
        project_dir = os.path.join(PROJECTS_ROOT, project_id)

        # Папка сборки
        build_dir = os.path.join(project_dir, BUILD_SUBDIR)

        # Файл лога
        log_file = info["log_path"]

        try:
            # Переходим в директорию сборки (где лежат main.py, buildozer.spec и т.д.)
            os.chdir(build_dir)

            # (1) buildozer android clean
            cmd_clean = ["buildozer", "android", "clean"]
            p_clean = subprocess.run(cmd_clean, capture_output=True, text=True)
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write("=== CLEAN PHASE ===\n")
                lf.write(p_clean.stdout)
                lf.write(p_clean.stderr)

            if p_clean.returncode != 0:
                info["status"] = "fail"
                build_queue.task_done()
                continue

            # (2) buildozer android debug
            cmd_build = ["buildozer", "android", "debug"]
            p_build = subprocess.run(cmd_build, capture_output=True, text=True)
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write("\n=== BUILD PHASE ===\n")
                lf.write(p_build.stdout)
                lf.write(p_build.stderr)

            if p_build.returncode != 0:
                info["status"] = "fail"
                build_queue.task_done()
                continue

            # Ищем APK в bin/
            bin_path = os.path.join(build_dir, "bin")
            apk_files = [f for f in os.listdir(bin_path) if f.endswith(".apk")]
            if not apk_files:
                info["status"] = "fail"
                build_queue.task_done()
                continue

            # Берём последний APK
            apk_name = apk_files[-1]
            apk_full = os.path.join(bin_path, apk_name)

            info["apk_path"] = apk_full
            info["status"] = "success"

        except Exception as e:
            info["status"] = "fail"
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(f"\n=== EXCEPTION ===\n{str(e)}\n")

        finally:
            build_queue.task_done()


# Запуск рабочего потока
worker_thread = threading.Thread(target=build_worker, daemon=True)
worker_thread.start()

###############################################################################
# ENDPOINTЫ
###############################################################################

@app.route("/")
def index():
    return "Сервер очереди сборок работает в /home/name/PythonAPKProjects!"

@app.route("/submit", methods=["POST"])
def submit_project():
    """
    Принимает JSON:
      {
        "project_id": "имя_проекта",
        "files": [
          {
            "filename": "main.py",
            "code": "... (текст файла)"
          },
          ...
        ]
      }
    Сохраняет файлы в /home/name/PythonAPKProjects/<project_id>/src/
    """
    data = request.json
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    project_id = data.get("project_id")
    files = data.get("files")
    if not project_id or not files:
        return jsonify({"error": "project_id и files обязательны"}), 400

    project_dir = os.path.join(PROJECTS_ROOT, project_id)
    src_dir = os.path.join(project_dir, "src")

    os.makedirs(src_dir, exist_ok=True)

    for item in files:
        fn = item.get("filename")
        code = item.get("code", "")
        if not fn:
            continue
        file_path = os.path.join(src_dir, fn)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(code)

    return jsonify({"message": "OK", "project_id": project_id})

@app.route("/build/<project_id>", methods=["POST"])
def enqueue_build(project_id):
    """
    Добавляем задачу сборки проекта в очередь.
    1) Копируем src -> buildarea
    2) Создаём запись в builds{}
    3) Отдаём build_id
    """
    project_dir = os.path.join(PROJECTS_ROOT, project_id)
    src_dir = os.path.join(project_dir, "src")
    build_dir = os.path.join(project_dir, BUILD_SUBDIR)

    if not os.path.exists(src_dir):
        return jsonify({"error": f"Проект {project_id} не найден"}), 404

    # Удаляем старую папку buildarea, если была
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir, exist_ok=True)

    # Копируем все файлы из src/
    for item in os.listdir(src_dir):
        sp = os.path.join(src_dir, item)
        dp = os.path.join(build_dir, item)
        if os.path.isfile(sp):
            shutil.copy2(sp, dp)
        else:
            if os.path.isdir(sp):
                shutil.copytree(sp, dp)

    # Создаём уникальный build_id
    build_id = str(uuid.uuid4())

    # Файл для логов
    log_file = os.path.join(project_dir, f"build_{build_id}.log")

    builds[build_id] = {
        "status": "pending",
        "log_path": log_file,
        "apk_path": None,
        "project_id": project_id
    }

    build_queue.put(build_id)
    return jsonify({"build_id": build_id})

@app.route("/status/<build_id>", methods=["GET"])
def get_status(build_id):
    info = builds.get(build_id)
    if not info:
        return jsonify({"error": "Build not found"}), 404
    return jsonify({"build_id": build_id, "status": info["status"]})

@app.route("/logs/<build_id>", methods=["GET"])
def get_logs(build_id):
    info = builds.get(build_id)
    if not info:
        return jsonify({"error": "Build not found"}), 404

    log_file = info["log_path"]
    if not os.path.exists(log_file):
        return jsonify({"logs": ""})

    with open(log_file, "r", encoding="utf-8") as lf:
        content = lf.read()
    return jsonify({"logs": content})

@app.route("/download/<build_id>", methods=["GET"])
def download_apk(build_id):
    info = builds.get(build_id)
    if not info:
        return jsonify({"error": "Build not found"}), 404

    if info["status"] != "success":
        return jsonify({"error": "Сборка не завершена успешно"}), 400

    apk_path = info["apk_path"]
    if not apk_path or not os.path.exists(apk_path):
        return jsonify({"error": "APK not found"}), 404

    # Отдаём APK
    apk_dir = os.path.dirname(apk_path)
    apk_name = os.path.basename(apk_path)
    return send_from_directory(apk_dir, apk_name, as_attachment=True)

if __name__ == "__main__":
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    app.run(host="0.0.0.0", port=8001)
