"""
Проверка наличия ассетов (картинок и аудио) для бота.
Запуск из корня проекта: python scripts/check_assets.py
Точный список и описание ассетов: docs/ASSETS_LIST.md
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import config

# Дополнительные файлы игр (конвенция: slug.jpg, slugwin.jpg, sluglose.jpg)
REQUIRED_GAME_FILES = [
    "echo.jpg",
    "random_win.jpg",
    "random_lose.jpg",
    "gamerandom_load.jpg",
    "blackmarket_start.jpg",
    "blackmarket_win.jpg",
    "blackmarket_scam.jpg",
    "fracture.jpg",
    "fracturewin.jpg",
    "fracturelose.jpg",
    "mirrorwin.jpg",
    "mirrorlose.jpg",
    "birzh.jpg",
    "status.jpg",
]


def main():
    images_dir = config.IMAGES_DIR
    print("=== Чекер ассетов Tehnolog Games ===\n")
    print(f"Папка изображений: {images_dir}")
    print(f"Существует: {images_dir.exists()}\n")
    if not images_dir.exists():
        print("Создайте папку assets/images и положите туда файлы.")
        return

    # 1) validate_assets() из config (базовый список)
    missing_config = config.validate_assets()
    if missing_config["images"]:
        print("--- Отсутствуют (из config.validate_assets) ---")
        for f in missing_config["images"]:
            print(f"  --  {f}")
    if missing_config["audio"]:
        print("--- Отсутствует аудио ---")
        for f in missing_config["audio"]:
            print(f"  --  {f}")

    # 2) Доп. файлы игр и интерфейсов
    found, missing = [], []
    for filename in REQUIRED_GAME_FILES:
        path = images_dir / filename
        if path.exists():
            found.append(filename)
        else:
            missing.append(filename)

    print("\n--- Игры и интерфейсы (доп.) ---")
    for f in found:
        print(f"  OK  {f}")
    for f in missing:
        print(f"  --  {f} (нет)")

    total_miss = len(missing_config["images"]) + len(missing_config["audio"]) + len(missing)
    print(f"\nИтого отсутствует: {total_miss} файлов.")
    print("Полный список и описание: docs/ASSETS_LIST.md")


if __name__ == "__main__":
    main()
