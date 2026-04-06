import base64

def prepare_openai_request(messages: list) -> list:
    """
    Превращает историю чата (с указателями на файлы) в формат OpenAI (для Polza.ai).
    Все локальные файлы читаются с диска и кодируются в Base64.
    """
    openai_messages = []

    for m in messages:
        # В формате OpenAI системный промпт — это просто сообщение с role="system"
        if m["role"] == "system":
            openai_messages.append({"role": "system", "content": m["content"]})
            continue

        # В OpenAI роли обычно "user" и "assistant" (вместо "model")
        role = "user" if m["role"] == "user" else "assistant"
        content_parts = []

        if isinstance(m["content"], str):
            openai_messages.append({"role": role, "content": m["content"]})
            continue

        elif isinstance(m["content"], list):
            for item in m["content"]:
                if item["type"] == "text":
                    content_parts.append({"type": "text", "text": item["text"]})

                # --- НОВАЯ ЛОГИКА: Читаем файлы с диска и кодируем в Base64 ---
                elif item["type"] in ["local_image_pointer", "local_pdf_pointer"]:
                    file_path = item["path"]
                    mime_type = item.get("mime", "application/pdf")

                    try:
                        # Читаем байты прямо с диска
                        with open(file_path, "rb") as f:
                            file_bytes = f.read()
                        
                        # Кодируем в Base64
                        base64_data = base64.b64encode(file_bytes).decode('utf-8')

                        # Формируем структуру, которую требует Polza.ai (OpenAI формат)
                        content_parts.append({
                            "type": "image_url", # В OpenAI API это поле используется и для картинок, и для документов
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_data}"
                            }
                        })
                    except FileNotFoundError:
                        print(f"⚠️ Ошибка: Файл {file_path} не найден на диске!")
                        content_parts.append({
                            "type": "text",
                            "text": "[Системное сообщение: Пользователь прикреплял файл, но он больше недоступен на сервере]"
                        })

                # --- СТАРАЯ ЛОГИКА (Если где-то остался готовый URL/Base64) ---
                elif item["type"] in ["image_url", "file_url"]:
                    url_key = item["type"]
                    # Если оно уже в формате data:mime;base64,..., просто передаем
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": item[url_key]["url"]}
                    })

            openai_messages.append({"role": role, "content": content_parts})

    return openai_messages