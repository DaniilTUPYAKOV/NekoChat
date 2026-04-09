
import os
import aiofiles
import aiohttp
import base64

async def upload_file_to_polza(file_path: str, mime_type: str, filename: str, api_key: str) -> str:
    """Асинхронно загружает файл на сервер Polza.ai и возвращает URL."""
    url = "https://polza.ai/api/v1/storage/upload"
    headers = {"Authorization": f"Bearer {api_key}"}

    print(f"Загружаем файл: {file_path}")

    # 1. Асинхронно читаем содержимое файла в память
    async with aiofiles.open(file_path, "rb") as f:
        file_content = await f.read()

    # 2. Формируем данные для отправки
    data = aiohttp.FormData()
    data.add_field('file', file_content, filename=filename, content_type=mime_type)
    data.add_field('purpose', 'assistants')

    # 3. Асинхронно отправляем запрос
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=data) as response:
            if response.status in (200, 201):
                result = await response.json()
                return result["url"]
            else:
                # Читаем текст ошибки, если статус не успешный
                error_text = await response.text()
                raise Exception(f"Ошибка загрузки файла: {response.status} - {error_text}")

async def prepare_polza_request(messages: list, api_key: str) -> list:
    """
    Превращает историю чата в формат OpenAI.
    Автоматически загружает локальные файлы на сервер провайдера и кэширует URL.
    """
    openai_messages = []

    for m in messages:
        if m["role"] == "system":
            openai_messages.append({"role": "system", "content": m["content"]})
            continue

        role = "user" if m["role"] == "user" else "assistant"

        if isinstance(m["content"], str):
            openai_messages.append({"role": role, "content": m["content"]})
            continue

        content_parts = []
        for item in m["content"]:
            if item["type"] == "text":
                content_parts.append({"type": "text", "text": item["text"]})

            elif item["type"] == "local_image_pointer":
                file_path = item["path"]
                mime_type = item.get("mime", "image/jpeg")
                filename = item.get("filename", os.path.basename(file_path))

                # Проверяем, загружали ли мы уже эту картинку в текущей сессии
                try:
                    # Загружаем файл провайдеру
                    file_url = await upload_file_to_polza(file_path, mime_type, filename, api_key)
                except Exception as e:
                    print(f"Ошибка загрузки файла {filename}: {e}")
                    # Если интернет отвалился, падаем на запасной вариант - Base64
                    with open(file_path, "rb") as f:
                        base64_data = base64.b64encode(f.read()).decode('utf-8')
                    file_url = f"data:{mime_type};base64,{base64_data}"

                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": file_url}
                })
            elif item["type"] =="local_pdf_pointer":
                file_path = item["path"]
                mime_type = item.get("mime", "application/pdf")

                try:
                    with open(file_path, "rb") as f:
                        base64_data = base64.b64encode(f.read()).decode('utf-8')

                    content_parts.append({
                        "type": "image_url", 
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_data}"
                        }
                    })
                except FileNotFoundError:
                    print(f"⚠️ Ошибка: Файл {file_path} не найден на диске!")
                    content_parts.append({
                        "type": "text",
                        "text": "[Системное сообщение: Пользователь прикреплял файл, но он больше недоступен]"
                    })

        openai_messages.append({"role": role, "content": content_parts})

    return openai_messages