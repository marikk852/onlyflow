import hashlib
import os
import random
import shutil
from pathlib import Path


def randomize_file(src_path: str, dest_dir: str) -> str:
    """
    Создаёт копию файла с изменённым хэшем.
    Исходный файл не изменяется.
    """
    src = Path(src_path)
    dest_dir_path = Path(dest_dir)
    dest_dir_path.mkdir(parents=True, exist_ok=True)

    rand_suffix = hashlib.md5(os.urandom(8)).hexdigest()[:6]
    new_name = f"{src.stem}_{rand_suffix}{src.suffix}"
    dest = dest_dir_path / new_name

    shutil.copy2(src, dest)

    ext = src.suffix.lower()
    try:
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            with open(dest, "ab") as f:
                f.write(b"\xFF\xFE" + os.urandom(4))
        elif ext in (".mp4", ".mov", ".avi", ".mkv"):
            with open(dest, "ab") as f:
                f.write(os.urandom(random.randint(4, 16)))
    except Exception:
        pass

    return str(dest)
