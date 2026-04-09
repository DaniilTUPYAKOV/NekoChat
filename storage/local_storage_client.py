
import os
from typing import Union, Dict, Any
from chainlit.data.storage_clients.base import BaseStorageClient

class LocalStorageClient(BaseStorageClient):
    def __init__(self, base_path: str = "./user_attachments", base_url: str = "http://localhost:8000"):
        self.base_path = base_path
        self.base_url = base_url  # Добавили базовый URL
        os.makedirs(self.base_path, exist_ok=True)

    async def upload_file(
        self,
        object_key: str,
        data: Union[bytes, str],
        mime: str = "application/octet-stream",
        overwrite: bool = True,
        content_disposition: str | None = None,
    ) -> Dict[str, Any]:

        # Нормализуем слеши
        safe_object_key = object_key.replace("\\", "/")
        file_path = os.path.join(self.base_path, *safe_object_key.split("/"))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Формируем ПОЛНЫЙ АБСОЛЮТНЫЙ URL
        web_url = f"{self.base_url}/user_attachments/{safe_object_key}"

        if not overwrite and os.path.exists(file_path):
            return {"object_key": safe_object_key, "url": web_url}

        mode = "wb" if isinstance(data, bytes) else "w"
        with open(file_path, mode) as f:
            f.write(data)

        return {"object_key": safe_object_key, "url": web_url}

    async def delete_file(self, object_key: str) -> bool:
        safe_object_key = object_key.replace("\\", "/")
        file_path = os.path.join(self.base_path, *safe_object_key.split("/"))
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                return True
        except Exception as e:
            print(f"Ошибка при удалении: {e}")
        return False

    async def get_read_url(self, object_key: str) -> str:
        safe_object_key = object_key.replace("\\", "/")
        # Возвращаем ПОЛНЫЙ АБСОЛЮТНЫЙ URL
        return f"{self.base_url}/user_attachments/{safe_object_key}"

    async def close(self) -> None:
        # Для локальных файлов закрывать соединения не нужно,
        # но метод должен быть реализован из-за @abstractmethod
        pass