"""Link local car_lot_pilot/check_cars with a shared token, without printing it."""
import argparse
import os
from pathlib import Path
import re
import secrets
import tempfile

from dotenv import dotenv_values


def append_values(path, values):
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    for key, value in values.items():
        pattern = re.compile(r"^" + re.escape(key) + r"\s*=.*$", re.MULTILINE)
        line = key + "=" + value
        text = pattern.sub(lambda _: line, text) if pattern.search(text) else text.rstrip("\n") + "\n" + line + "\n"
    descriptor, name = tempfile.mkstemp(prefix=".env-checks-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(name, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def configure(main_dir, checks_dir):
    main_dir, checks_dir = Path(main_dir).resolve(), Path(checks_dir).resolve()
    if not (checks_dir/"app/api/integrations/car-lot-pilot/route.ts").is_file():
        raise ValueError("Обновите check_cars: не найден защищённый интеграционный endpoint")
    main_env, checks_env = main_dir/".env", checks_dir/".env.local"
    if main_env.is_symlink() or checks_env.is_symlink():
        raise ValueError("Файлы конфигурации не должны быть символическими ссылками")
    main = dotenv_values(main_env) if main_env.exists() else {}
    checks = dotenv_values(checks_env) if checks_env.exists() else {}
    first, second = main.get("CHECK_CARS_INTEGRATION_TOKEN") or "", checks.get("CHECK_CARS_INTEGRATION_TOKEN") or ""
    if first and second and first != second:
        raise ValueError("Существующие интеграционные токены различаются; значения не изменены")
    token = first or second or secrets.token_urlsafe(32)
    if len(token) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
        raise ValueError("Существующий токен должен содержать минимум 32 символа A-Z/a-z/0-9/_/-; значения не изменены")
    main_values = {"CHECK_CARS_INTEGRATION_TOKEN":token}
    for key, value in {"CHECK_CARS_BASE_URL":"http://127.0.0.1:3000", "CHECK_CARS_ALLOW_PAID":"0", "CHECK_CARS_DAILY_LIMIT":"10"}.items():
        if not main.get(key):
            main_values[key] = value
    append_values(checks_env, {"CHECK_CARS_INTEGRATION_TOKEN":token})
    append_values(main_env, main_values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_cars_dir", type=Path, help="Path to your local check_cars checkout")
    args = parser.parse_args()
    try:
        configure(Path(__file__).resolve().parents[1], args.check_cars_dir)
    except (ValueError, OSError):
        parser.error("Не удалось безопасно настроить связку. Проверьте путь, права и совпадение существующих интеграционных токенов")
    print("Связка настроена. Токен не выводится. Почта, ключи провайдеров и их настройки не изменялись.")
    print("На Windows защитите .env и .env.local правами своей учётной записи. Перезапустите оба сервиса.")


if __name__ == "__main__":
    main()
