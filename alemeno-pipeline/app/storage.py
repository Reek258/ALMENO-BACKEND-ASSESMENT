import os
import uuid

from app.config import settings


def save_upload(file_bytes: bytes, original_filename: str) -> str:
    """Persist an uploaded CSV to the shared uploads volume and return its path.

    The API and worker run as separate containers, so the file must land on a
    volume both containers mount (see uploads_data in docker-compose.yml) --
    passing raw bytes through Redis would work too, but writing to disk keeps
    job payloads small and lets us re-process a file without re-uploading it.
    """
    os.makedirs(settings.upload_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{os.path.basename(original_filename)}"
    path = os.path.join(settings.upload_dir, safe_name)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path
