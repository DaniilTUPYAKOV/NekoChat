import base64
from google.genai import types

def prepare_google_request(messages: list) -> tuple[list, str]:
    """
    Превращает историю чата (с указателями на файлы) в нативный формат Google GenAI.
    Возвращает кортеж: (список_сообщений_google, системный_промпт)
    """
    google_contents = []
    system_instruction = None

    for m in messages:
        if m["role"] == "system":
            system_instruction = m["content"]
            continue

        role = "user" if m["role"] == "user" else "model"
        parts = []

        if isinstance(m["content"], str):
            parts.append(types.Part.from_text(text=m["content"]))

        elif isinstance(m["content"], list):
            for item in m["content"]:
                if item["type"] == "text":
                    parts.append(types.Part.from_text(text=item["text"]))

                # --- НОВАЯ ЛОГИКА: Читаем файлы прямо с диска по путям ---
                elif item["type"] in ["local_image_pointer", "local_pdf_pointer"]:
                    file_path = item["path"]
                    # Если это PDF, жестко задаем mime, иначе берем из словаря
                    mime_type = item.get("mime", "application/pdf")

                    try:
                        # Читаем байты прямо с диска
                        with open(file_path, "rb") as f:
                            file_bytes = f.read()

                        # Отправляем байты в Google
                        parts.append(types.Part.from_bytes(
                            data=file_bytes,
                            mime_type=mime_type
                        ))
                    except FileNotFoundError:
                        print(
                            f"⚠️ Ошибка: Файл {file_path} не найден на диске!")
                        # Если файл пропал, честно говорим об этом нейронке, чтобы она не галлюцинировала
                        parts.append(types.Part.from_text(
                            text="[Системное сообщение: Пользователь прикреплял файл, но он больше недоступен на сервере]"
                        ))

                # --- СТАРАЯ ЛОГИКА (Оставил для обратной совместимости, если где-то остался Base64) ---
                elif item["type"] in ["image_url", "file_url"]:
                    url_key = item["type"]
                    b64_data = item[url_key]["url"].split(",")[1]
                    mime_type = item[url_key]["url"].split(";")[
                        0].split(":")[1]
                    file_bytes = base64.b64decode(b64_data)

                    parts.append(types.Part.from_bytes(
                        data=file_bytes,
                        mime_type=mime_type
                    ))

        google_contents.append(types.Content(role=role, parts=parts))

    return google_contents, system_instruction