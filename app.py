
import asyncio
import json
import shutil

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data import get_data_layer
from chainlit.types import ThreadDict
import base64
import os
from typing import Union, Dict, Any
from chainlit.data.storage_clients.base import BaseStorageClient
import sqlite3
import io
from PIL import Image
import random
import docx
from dotenv import load_dotenv
from chainlit.server import app
from fastapi.responses import FileResponse

# --- ИМПОРТЫ GOOGLE SDK ---
from google import genai
from google.genai import types


# --- НАСТРОЙКИ API ---
load_dotenv()
# Настройки API
API_KEY = os.getenv("API_KEY")
# API_KEY = "тщ"
BASE_URL = "https://api.proxyapi.ru/google"
MODEL_NAME = "gemini-3-pro-preview"

# Получаем абсолютный путь к папке проекта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Создаем абсолютный путь к папке с файлами
STORAGE_PATH = os.path.join(BASE_DIR, "user_attachments")
CONTEXT_PATH = os.path.join(BASE_DIR, "context")
os.makedirs(STORAGE_PATH, exist_ok=True)
os.makedirs(CONTEXT_PATH, exist_ok=True)

# Инициализируем клиент Google (как в документации провайдера)
gemini_client = genai.Client(
    api_key=API_KEY,
    http_options={"base_url": BASE_URL},
)


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

# --- Вспомогательный метод для получения пути к файлу ---
async def _get_context_filepath() -> str:
    """Формирует имя файла на основе имени треда и его ID."""
    thread_id = cl.context.session.thread_id
    
    # Если по какой-то причине ID нет (например, отключена БД), используем ID сессии
    if not thread_id:
        thread_id = cl.context.session.id
        
    thread_name = "Новый_чат"
    
    # Получаем имя из Data Layer
    data_layer =get_data_layer()
    if data_layer and cl.context.session.thread_id:
        thread_data = await data_layer.get_thread(cl.context.session.thread_id)
        if thread_data and thread_data.get("name"):
            thread_name = thread_data.get("name")
            
    # Очищаем имя от запрещенных для файловой системы символов
    safe_thread_name = "".join(c for c in thread_name if c.isalnum() or c in (' ', '-', '_')).strip()
    
    # Формируем итоговый путь: Папка / ИмяТреда_ID.json
    filename = f"{safe_thread_name}_{thread_id}.json"
    return os.path.join(CONTEXT_PATH, filename)


# --- 1. Метод сохранения контекста ---
async def save_context(context_data: list):
    """Сохраняет переданный контекст (список сообщений) в JSON файл."""
    filepath = await _get_context_filepath()
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=4)
        print(f"Контекст успешно сохранен в {filepath}")
    except Exception as e:
        print(f"Ошибка при сохранении контекста: {e}")


# --- 2. Метод загрузки контекста ---
async def load_context() -> list:
    """Загружает контекст из файла. Если файла нет, возвращает пустой список."""
    filepath = await _get_context_filepath()
    
    if not os.path.exists(filepath):
        print(f"Файл контекста не найден: {filepath}. Начинаем с чистого листа.")
        return []
        
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            context_data = json.load(f)
        print(f"Контекст успешно загружен из {filepath}")
        return context_data
    except Exception as e:
        print(f"Ошибка при загрузке контекста: {e}")
        return []



# --- 1. ИНИЦИАЛИЗАЦИЯ БАЗЫ ИЗ ФАЙЛА СХЕМЫ ---


def init_db():
    with open("schema.sql", "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn = sqlite3.connect("chainlit_history.db")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


init_db()

os.makedirs(STORAGE_PATH, exist_ok=True)  # Гарантируем, что папка есть
storage_client = LocalStorageClient(base_path=STORAGE_PATH)

# 3. Применяем настройки
cl.data._data_layer = SQLAlchemyDataLayer(
    conninfo="sqlite+aiosqlite:///chainlit_history.db", storage_provider=storage_client)

@app.get("/api/my_files/{filename}")
async def serve_my_file(filename: str):
    file_path = os.path.join(STORAGE_PATH, filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    return FileResponse(status_code=404)


# --- 2. АВТОРИЗАЦИЯ ---
@cl.password_auth_callback
def auth(username: str, password: str):
    valid_username = os.getenv("APP_USERNAME")
    valid_password = os.getenv("APP_PASSWORD")
    if username == valid_username and password == valid_password:
        return cl.User(identifier=username)
    return None

# --- 3. ЛОГИКА ЧАТА ---


async def animate_paws(msg: cl.Message):
    """Фоновая задача для случайной анимации ожидания"""

    # Сценарий 1: Котик-программист (быстро печатает)
    coder_cat = [
        "*Усиленно печатаю ответ...* (=^･ω･^) ⌨️",
        "*Усиленно печатаю ответ...* (=^･ω･^)つ ⌨️",
        "*Усиленно печатаю ответ...* (=^･ω･^) ⌨️",
        "*Усиленно печатаю ответ...* (=^･ω･^)っ ⌨️"
    ]

    # Сценарий 2: Охота на бабочку (мысль)
    butterfly_cat = [
        "*Ловлю нужную мысль...* 🐈       🦋",
        "*Ловлю нужную мысль...* 🐈     🦋",
        "*Ловлю нужную мысль...* 🐈   🦋",
        "*Ловлю нужную мысль...* 🐈 🦋",
        "*Ловлю нужную мысль...* 🐈🐾"
    ]

    # Сценарий 3: Разматываем клубок
    yarn_cat = [
        "*Разматываю клубок знаний...* 🐈🧶",
        "*Разматываю клубок знаний...* 🐈 🧶",
        "*Разматываю клубок знаний...* 🐈  🧶",
        "*Разматываю клубок знаний...* 🐈   🧶",
        "*Разматываю клубок знаний...* 🐈    🧶"
    ]

    # Сценарий 4: Спящий котик
    sleeping_cat = [
        "*Мур-мур, обращаюсь к серверу...* 🐈",
        "*Мур-мур, обращаюсь к серверу...* 🐈 z",
        "*Мур-мур, обращаюсь к серверу...* 🐈 zZ",
        "*Мур-мур, обращаюсь к серверу...* 🐈 zZz"
    ]

    # Сценарий 5: Кот в коробке
    box_cat = [
        "*Ищу ответ в базе данных...* 📦",
        "*Ищу ответ в базе данных...* 📦👀",
        "*Ищу ответ в базе данных...* 📦🐈",
        "*Ищу ответ в базе данных...* 📦👀"
    ]

    # Собираем все сценарии в один список
    all_animations = [coder_cat, butterfly_cat,
                      yarn_cat, sleeping_cat, box_cat]

    # Случайно выбираем один сценарий для текущего сообщения
    selected_frames = random.choice(all_animations)

    # Если выпал котик-программист, делаем анимацию быстрее
    speed = 0.2 if selected_frames == coder_cat else 0.4

    i = 0
    try:
        while True:
            msg.content = selected_frames[i % len(selected_frames)]
            await msg.update()
            i += 1
            await asyncio.sleep(speed)
    except asyncio.CancelledError:
        pass


@cl.on_chat_start
async def on_chat_start():

    cl.user_session.set("messages", [
                        {"role": "system", "content": "Ты полезный ИИ-ассистент Neko Chat."}])

    settings = await cl.ChatSettings([
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=0.7, min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=8192, min=100, max=64000, step=100),
        cl.input_widget.Switch(
            id="Web Search", label="Web Search", initial=True),
    ]).send()
    cl.user_session.set("settings", settings)


@cl.on_settings_update
async def setup_agent(settings):
    cl.user_session.set("settings", settings)


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    messages = [
        {"role": "system", "content": "Ты полезный ИИ-ассистент Neko Chat 🐾."}]
    for step in thread["steps"]:
        if step["type"] == "user_message":
            messages.append({"role": "user", "content": step["output"]})
        elif step["type"] == "assistant_message":
            messages.append({"role": "assistant", "content": step["output"]})
    cl.user_session.set("messages", messages)

    # 5. Восстанавливаем настройки интерфейса
    settings = await cl.ChatSettings([
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=0.7, min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=8192, min=100, max=64000, step=100),
        cl.input_widget.Switch(
            id="Web Search", label="Web Search", initial=True),
    ]).send()
    cl.user_session.set("settings", settings)


@cl.on_message
async def on_message(message: cl.Message):
    messages = cl.user_session.get("messages")
    settings = cl.user_session.get("settings")
    context = await load_context()

    user_content = []
    prompt_text = message.content

    # --- ОБРАБОТКА ФАЙЛОВ (СЖАТИЕ PILLOW ОСТАВЛЯЕМ ДЛЯ СКОРОСТИ) ---
    if message.elements:
        for element in message.elements:
            mime = element.mime.lower() if element.mime else ""

            # --- 1. ИЗОБРАЖЕНИЯ (Сжатие Pillow) ---
            if "image" in mime:
                img = Image.open(element.path)
                if img.mode in ("RGBA", "P") and "png" not in mime:
                    img = img.convert("RGB")

                max_size = 2048
                if max(img.size) > max_size:
                    ratio = max_size / max(img.size)
                    new_size = (int(img.size[0] * ratio),
                                int(img.size[1] * ratio))
                    img = img.resize(new_size, Image.Resampling.LANCZOS)

                buffer = io.BytesIO()
                if "png" in mime:
                    img.save(buffer, format="PNG")
                    final_mime = "image/png"
                else:
                    img.save(buffer, format="JPEG", quality=95)
                    final_mime = "image/jpeg"

                # ВАЖНО: Мы сжали картинку. Давай сохраним сжатую версию на диск,
                # чтобы не сжимать ее заново при суммаризации!
                compressed_bytes = buffer.getvalue()

                # Создаем уникальное имя для сжатого кэша
                cache_filename = f"compressed_{element.id}.jpg"
                cache_path = os.path.join(STORAGE_PATH, cache_filename)

                with open(cache_path, "wb") as f:
                    f.write(compressed_bytes)
                # В user_content кладем специальный словарь-указатель!
                user_content.append({
                    "type": "local_image_pointer",  # Наш кастомный тип
                    "path": cache_path,            # Путь к сжатому файлу
                    "mime": final_mime
                })

            # --- 2. PDF ФАЙЛЫ (Нативная поддержка Gemini) ---
            elif "pdf" in mime or element.name.endswith(".pdf"):
                safe_pdf_filename = f"saved_{element.id}.pdf"
                safe_pdf_path = os.path.join(STORAGE_PATH, safe_pdf_filename)

                # 2. Копируем файл из временной папки Chainlit в нашу постоянную
                shutil.copy2(element.path, safe_pdf_path)
                # 3. Сохраняем в контекст НАШ ПОСТОЯННЫЙ ПУТЬ
                user_content.append({
                    "type": "local_pdf_pointer",
                    "path": safe_pdf_path,
                    "mime": "application/pdf"
                })

            # --- 3. WORD ДОКУМЕНТЫ (DOCX) ---
            elif "wordprocessingml.document" in mime or element.name.endswith(".docx"):
                # Открываем документ через python-docx
                doc = docx.Document(element.path)
                full_text = []
                for para in doc.paragraphs:
                    if para.text.strip():  # Пропускаем пустые строки
                        full_text.append(para.text)

                text_data = "\n".join(full_text)
                prompt_text += f"\n\n--- Содержимое документа {element.name} ---\n\n{text_data}\n\n"

            # --- 4. CSV, TXT, JSON, PY (Простой текст) ---
            elif "text" in mime or "csv" in mime or "json" in mime or element.name.endswith(('.txt', '.csv', '.py', '.md')):
                try:
                    # Пытаемся прочитать как UTF-8
                    with open(element.path, "r", encoding="utf-8") as f:
                        text_data = f.read()
                except UnicodeDecodeError:
                    # Если кодировка виндовская (часто бывает с CSV из Excel)
                    with open(element.path, "r", encoding="windows-1251") as f:
                        text_data = f.read()

                prompt_text += f"\n\n--- Содержимое файла {element.name} ---\n\n{text_data}\n\n"

    user_content.append({"type": "text", "text": prompt_text})

    final_content = user_content if len(user_content) > 1 else prompt_text
    messages.append({"role": "user", "content": final_content})
    context.append({"role": "user", "content": final_content})

    msg = cl.Message(content="*Шевелю усами, изучаю...* 🐾", author="Assistant")
    await msg.send()

    # 2. ЗАПУСКАЕМ АНИМАЦИЮ В ФОНЕ
    animation_task = asyncio.create_task(animate_paws(msg))

    try:
        google_contents, sys_prompt = prepare_google_request(context)

        active_tools = []
        # Если тумблер в настройках включен, добавляем инструмент поиска
        if settings.get("Web Search"):
            # Используем строгие классы из google.genai.types
            active_tools.append(types.Tool(google_search=types.GoogleSearch()))

        # 2. Настраиваем конфиг
        config = types.GenerateContentConfig(
            temperature=settings["temperature"],
            max_output_tokens=int(settings["max_tokens"]),
            system_instruction=sys_prompt,
            # Передаем инструменты (если список пустой, передаем None)
            tools=active_tools if active_tools else None
        )

        stream = await gemini_client.aio.models.generate_content_stream(
            model=MODEL_NAME,
            contents=google_contents,
            config=config
        )

        is_first_token = True
        async for chunk in stream:
            if chunk.text:
                if is_first_token:
                    # 3. КАК ТОЛЬКО ПРИШЕЛ ОТВЕТ — ОСТАНАВЛИВАЕМ АНИМАЦИЮ!
                    animation_task.cancel()
                    msg.content = ""
                    is_first_token = False
                await msg.stream_token(chunk.text)

        await msg.update()
        messages.append({"role": "assistant", "content": msg.content})
        context.append({"role": "assistant", "content": msg.content})

    except Exception as e:
        # Если произошла ошибка, анимацию тоже нужно остановить
        animation_task.cancel()
        await cl.ErrorMessage(content=f"🚨 Ошибка API: {e}").send()
        messages.pop()
        context.pop()
    finally:
        await save_context(context)
        
