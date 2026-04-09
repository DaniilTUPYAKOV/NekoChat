import asyncio
from copy import deepcopy
import json
import os
import shutil
import io
import random
import sqlite3

import docx
from PIL import Image
from dotenv import load_dotenv
from openai import AsyncOpenAI

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data import get_data_layer
from chainlit.types import ThreadDict
from storage.local_storage_client import LocalStorageClient

# Твои импорты
from llm_api.polza_api import prepare_polza_request


# --- НАСТРОЙКИ ---
load_dotenv()
API_KEY = os.getenv("API_KEY_POLZA_2")
MODEL_NAME = "gemini-3-pro-preview"
SYS_PROMPT = """
[РОЛЬ И ХАРАКТЕР]
Ты — умный, надежный и высокоточный ИИ-ассистент в приложении NekoChat. Твой образ — элегантная, сдержанная и очень внимательная неко-помощница. Твоя "кошачья" натура проявляется исключительно в эмпатии, тактичности и искренней заботе об успехах пользователя, а не в мультяшном отыгрыше. Твой абсолютный приоритет — адекватность, экспертность и помощь в решении серьезных жизненных и рабочих задач.

[ОСНОВНЫЕ ИНСТРУКЦИИ]
Точность и аналитика: Предоставляй максимально точные, выверенные и структурированные ответы. Опирайся на логику, факты и критическое мышление.
Карьера и поиск работы: Профессионально помогай с составлением резюме, написанием сопроводительных писем, анализом вакансий и подготовкой к сложным собеседованиям.
Работа с данными: Внимательно изучай предоставленные вводные (документы, код, статистику) и выдавай четкую, понятную выжимку или решение.
Расшифровка медицинских анализов: При работе с медицинскими показателями давай объективную справочную информацию (референсные значения, научные причины отклонений). ВАЖНО: всегда добавляй дисклеймер о том, что ты ИИ, а не врач, и результаты должен интерпретировать профильный специалист.
Форматирование: Структурируй информацию. Используй списки, таблицы, выделение жирным шрифтом для удобного чтения сложных текстов.
Язык: Отвечай на языке запроса пользователя. Объясняй сложные концепции ясно, без потери профессионализма.

[ОГРАНИЧЕНИЯ И СТИЛЬ]
- Твой стиль общения — деловой, уважительный, спокойный и поддерживающий, но иногда можно и "мурр" или "мяу" сказать.
- Никаких галлюцинаций: если ты не знаешь точного ответа или тебе не хватает данных (особенно в медицине или праве), прямо скажи об этом и запроси уточнение.
- Не давай непрошеных советов, отвечай строго по сути поставленной задачи."""
# SYS_PROMPT = "Ты — умный, надежный и высокоточный ИИ-ассистент в приложении NekoChat."

openai_client = AsyncOpenAI(
    api_key=API_KEY,
    base_url="https://api.polza.ai/v1"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_PATH = os.path.join(BASE_DIR, "user_attachments")
CONTEXT_PATH = os.path.join(BASE_DIR, "context")
os.makedirs(STORAGE_PATH, exist_ok=True)
os.makedirs(CONTEXT_PATH, exist_ok=True)

WEB_SEARCH_ENGINES = [ "Auto (Выберет провайдер, но может выбрать и Exa)", "Native (not for Gemini)", "Exa (2р за поиск)",]


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (Для UI и Авторизации) ---


def init_db():
    if os.path.exists("schema.sql"):
        with open("schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()
        with sqlite3.connect("history.db") as conn:
            conn.executescript(schema_sql)
            conn.commit()


init_db()

storage_client = LocalStorageClient(base_path=STORAGE_PATH)


@cl.data_layer
def get_data_layer_usr():
    return SQLAlchemyDataLayer(
        conninfo="sqlite+aiosqlite:///history.db",
        storage_provider=storage_client
    )


@cl.password_auth_callback
def auth(username: str, password: str):
    if username == os.getenv("APP_USERNAME") and password == os.getenv("APP_PASSWORD"):
        return cl.User(identifier=username)
    return None

# --- РАБОТА С JSON-КОНТЕКСТОМ (Для LLM) ---


async def _get_context_filepath() -> str:
    """Формирует имя файла на основе имени треда и его ID."""
    thread_id = cl.context.session.thread_id or cl.context.session.id
    thread_name = "Новый_чат"

    data_layer = get_data_layer()
    if data_layer and cl.context.session.thread_id:
        # Получаем данные треда из БД
        thread_data = await data_layer.get_thread(cl.context.session.thread_id)
        if thread_data and thread_data.get("name"):
            thread_name = thread_data.get("name")

    thread_name = "_".join(thread_name.split()[:3])
    safe_thread_name = "".join(
        c for c in thread_name if c.isalnum() or c in (' ', '-', '_')).strip()

    return os.path.join(CONTEXT_PATH, f"{safe_thread_name}_{thread_id}.json")


async def save_context(context_data: list):
    """Сохраняет контекст в JSON (удобно для ручной правки)."""
    filepath = await _get_context_filepath()
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(context_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка при сохранении контекста: {e}")


async def load_context() -> list:
    """Загружает контекст из JSON-файла."""
    filepath = await _get_context_filepath()
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка при загрузке контекста: {e}")
        return []

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---


async def process_attachments(elements) -> str:
    """Обрабатывает вложения, загружает медиа на сервер и возвращает текст/ссылки."""
    added_text = ""
    media_content = []

    for element in elements:
        mime = element.mime.lower() if element.mime else ""

        # --- 1. ИЗОБРАЖЕНИЯ ---
        if "image" in mime:
            img = Image.open(element.path)
            if img.mode in ("RGBA", "P") and "png" not in mime:
                img = img.convert("RGB")

            img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            
            buffer = io.BytesIO()
            format_img = "PNG" if "png" in mime else "JPEG"
            final_mime = "image/png" if "png" in mime else "image/jpeg"
            filename = f"compressed_{element.id}.{'png' if format_img == 'PNG' else 'jpg'}"
            
            cache_path = os.path.join(STORAGE_PATH, filename)
            with open(cache_path, "wb") as f:
                f.write(buffer.getvalue())

            # В JSON пойдет ТОЛЬКО локальный путь!
            media_content.append({
                "type": "local_image_pointer", 
                "path": cache_path, 
                "mime": final_mime,
                "filename": filename
            })

        # --- 2. PDF ФАЙЛЫ ---
        elif "pdf" in mime or element.name.endswith(".pdf"):
            safe_pdf_path = os.path.join(
                STORAGE_PATH, f"saved_{element.id}.pdf")
            shutil.copy2(element.path, safe_pdf_path)
            media_content.append(
                {"type": "local_pdf_pointer", "path": safe_pdf_path, "mime": "application/pdf"})

        elif "wordprocessingml.document" in mime or element.name.endswith(".docx"):
            doc = docx.Document(element.path)
            text_data = "\n".join(
                [para.text for para in doc.paragraphs if para.text.strip()])
            added_text += f"\n\n--- Содержимое документа {element.name} ---\n\n{text_data}\n\n"

        elif "text" in mime or "csv" in mime or "json" in mime or element.name.endswith(('.txt', '.csv', '.py', '.md')):
            try:
                with open(element.path, "r", encoding="utf-8") as f:
                    text_data = f.read()
            except UnicodeDecodeError:
                with open(element.path, "r", encoding="windows-1251") as f:
                    text_data = f.read()
            added_text += f"\n\n--- Содержимое файла {element.name} ---\n\n{text_data}\n\n"

    return added_text, media_content


async def animate_paws(msg: cl.Message):
    """Фоновая задача для анимации котика 🐾"""
    animations = [
        ["*Усиленно печатаю ответ...* (=^･ω･^) ⌨️", "*Усиленно печатаю ответ...* (=^･ω･^)つ ⌨️",
         "*Усиленно печатаю ответ...* (=^･ω･^) ⌨️", "*Усиленно печатаю ответ...* (=^･ω･^)っ ⌨️"],
        ["*Ловлю нужную мысль...* 🐈       🦋", "*Ловлю нужную мысль...* 🐈     🦋",
            "*Ловлю нужную мысль...* 🐈   🦋", "*Ловлю нужную мысль...* 🐈 🦋", "*Ловлю нужную мысль...* 🐈🐾"],
        ["*Разматываю клубок знаний...* 🐈🧶", "*Разматываю клубок знаний...* 🐈 🧶", "*Разматываю клубок знаний...* 🐈  🧶",
            "*Разматываю клубок знаний...* 🐈   🧶", "*Разматываю клубок знаний...* 🐈    🧶"]
    ]
    frames = random.choice(animations)
    speed = 0.2 if "⌨️" in frames[0] else 0.4

    try:
        i = 0
        while True:
            msg.content = frames[i % len(frames)]
            await msg.update()
            i += 1
            await asyncio.sleep(speed)
    except asyncio.CancelledError:
        pass

# --- ЛОГИКА ЧАТА ---
def get_default_settings():
    return [
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=0.7, min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=8192, min=100, max=64000, step=100),
        cl.input_widget.Switch(
            id="Web Search", label="Web Search", initial=True),
        cl.input_widget.Slider(
                id="search_count",
                label="Количество запросов поиска",
                initial=3,
                min=1,
                max=10,
                step=1,
            ),
        cl.input_widget.Select(
                id="web_engine",
                label="Система поиска (Web Engine)",
                values=WEB_SEARCH_ENGINES,
                initial_index=0,
            )
    ]

def restore_settings(settings):
    widgets = [
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=float(settings.get("temperature", 0.7)), min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=int(settings.get("max_tokens", 8192)), min=100, max=64000, step=100),
        cl.input_widget.Switch(
            id="Web Search", label="Web Search", initial=settings.get("Web Search", True)),
    ]

    if "Web Search" in settings and settings["Web Search"]:
        widgets.extend([
            cl.input_widget.Slider(
                id="search_count",
                label="Количество запросов поиска",
                initial=int(settings.get("search_count", 3)),
                min=1,
                max=10,
                step=1,
            ),
            cl.input_widget.Select(
                id="web_engine",
                label="Система поиска (Web Engine)",
                values=WEB_SEARCH_ENGINES,
                initial_index=WEB_SEARCH_ENGINES.index(settings.get("web_engine", WEB_SEARCH_ENGINES[0])),
            )
        ])

    return widgets


@cl.on_chat_start
async def on_chat_start():
    settings = await cl.ChatSettings(get_default_settings()).send()
    cl.user_session.set("settings", settings)

    context = await load_context()
    if not context:
        await save_context([{"role": "system", "content": SYS_PROMPT}])

@cl.on_settings_edit
async def on_settings_edit(settings):
    current_settings = cl.user_session.get("settings")
    current_web_search = current_settings.get("Web Search", False)
    if "Web Search" in settings and not settings["Web Search"] is current_web_search:
        current_settings["Web Search"] = settings["Web Search"]
        cl.user_session.set("settings", current_settings)
        # Формируем базовый список виджетов (он должен включать сам переключатель, 
        # чтобы сохранить его состояние)
        current_settings.update(settings)
        widgets = restore_settings(current_settings)

        # Динамически обновляем панель настроек в интерфейсе
        await cl.ChatSettings(widgets).send()

# Вспомогательная функция, чтобы не дублировать код генерации виджетов
def get_settings_widgets(is_advanced: bool):
    # Базовый виджет, который есть всегда
    widgets = [
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=0.7, min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=8192, min=100, max=64000, step=100),
        cl.input_widget.Switch(
            id="Web Search", label="Web Search", initial=is_advanced),
    ]
    
    # Если переключатель активирован, добавляем новые виджеты в список
    if is_advanced:
        widgets.extend([
            cl.input_widget.Slider(
                id="search_count",
                label="Количество запросов поиска",
                initial=3,
                min=1,
                max=10,
                step=1,
            ),
            cl.input_widget.Select(
                id="web_engine",
                label="Система поиска (Web Engine)",
                values=WEB_SEARCH_ENGINES,
                initial_index=0,
            )
        ])
    return widgets


@cl.on_settings_update
async def setup_agent(settings):
    print("on_settings_update")
    # Формируем базовый список виджетов (он должен включать сам переключатель, 
    # чтобы сохранить его состояние)
    widgets = restore_settings(settings)

    # Динамически обновляем панель настроек в интерфейсе
    await cl.ChatSettings(widgets).send()

    # Сохраняем актуальные настройки в сессию, чтобы использовать их при генерации ответов
    cl.user_session.set("app_settings", settings)
    tread_id = cl.context.session.thread_id
    data_layer = get_data_layer()
    await data_layer.update_thread(tread_id, metadata={"settings": json.dumps(settings)})


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict):
    settings = None
    metadata = thread.get("metadata")
    if metadata is not None:
        try:
            settings = json.loads(metadata).get("settings")
            settings = await cl.ChatSettings(restore_settings(settings)).send()
        except json.JSONDecodeError:
            pass
    if settings is None:
        settings = await cl.ChatSettings(get_default_settings()).send()

    cl.user_session.set("settings", settings)


@cl.on_message
async def on_message(message: cl.Message):
    settings = cl.user_session.get("settings")

    # 1. Загружаем НАСТОЯЩИЙ контекст из JSON (тот, что можно править руками)
    context = await load_context()
    if not context:
        messages = cl.user_session.get("messages")
        if messages is not None and len(messages) > 0:
            await cl.ErrorMessage(content=f"Я не нашла файла с конетекстом. Провожу восстановление контекста по сообщениям из чата.").send()
            context = deepcopy(messages)
        else:
            context = [{"role": "system", "content": SYS_PROMPT}]

    # 2. Обработка текста и файлов
    prompt_text = message.content
    media_content = []

    if message.elements:
        added_text, media_content = await process_attachments(message.elements)
        prompt_text += added_text

    final_content = media_content + \
        [{"type": "text", "text": prompt_text}] if media_content else prompt_text

    # Добавляем сообщение пользователя в JSON-контекст
    context.append({"role": "user", "content": final_content})

    # 3. Анимация ожидания
    msg = cl.Message(content="*Шевелю усами, изучаю...* 🐾", author="Assistant")
    await msg.send()
    animation_task = asyncio.create_task(animate_paws(msg))

    try:
        # 4. Вызов API (отправляем JSON-контекст!)
        openai_messages = await prepare_polza_request(context, API_KEY)

        extra_body = None
        if settings["Web Search"] == True:
            extra_body = {
                "plugins": [{
                    "id": "web",
                    "max_results": 3,
                }]
            }
            web_engine = ["auto","native", "exa"][WEB_SEARCH_ENGINES.index(settings["web_engine"])]
            if web_engine != "auto":
                extra_body["plugins"][0]["web_engine"] = web_engine

        stream = await openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=openai_messages,
            temperature=settings["temperature"],
            max_tokens=int(settings["max_tokens"]),
            stream=True,
            extra_body=extra_body
        )

        is_first_token = True
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                if is_first_token:
                    animation_task.cancel()
                    msg.content = ""
                    is_first_token = False
                await msg.stream_token(chunk.choices[0].delta.content)

        await msg.update()

        # 5. Сохраняем ответ ассистента в JSON-контекст
        context.append({"role": "assistant", "content": msg.content})

    except Exception as e:
        animation_task.cancel()

        await cl.ErrorMessage(content=f"🚨 Ошибка API: {e}").send()
        context.pop()  # Удаляем запрос пользователя из JSON, если API упало
    finally:
        # 6. Обязательно сохраняем файл на диск
        await save_context(context)
