"""
Проверка наличия ассетов (картинок) для игр.
Запуск из корня проекта: python scripts/check_assets.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import config

# Файлы в assets/images для новых/обновлённых игр (конвенция: slug.jpg, slugwin.jpg, sluglose.jpg, имя.jpg)
REQUIRED_FILES = [
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
    "mirror.jpg",
    "mirror_box.jpg",
    "mirrorwin.jpg",
    "mirrorlose.jpg",
]

def main():
    images_dir = config.IMAGES_DIR
    print(f"Папка изображений: {images_dir}")
    print(f"Существует: {images_dir.exists()}\n")
    if not images_dir.exists():
        print("Создайте папку assets/images и положите туда файлы.")
        return

    found, missing = [], []
    for filename in REQUIRED_FILES:
        path = images_dir / filename
        if path.exists():
            found.append(filename)
        else:
            missing.append(filename)

    print("--- Найдено ---")
    for f in found:
        print(f"  OK  {f}")
    print("\n--- Отсутствует ---")
    for f in missing:
        print(f"  --  {f}")
    print(f"\nИтого: {len(found)} есть, {len(missing)} нет.")

if __name__ == "__main__":
    main()
