import asyncio
from copy import deepcopy
from datetime import datetime
import json
import os
import shutil
import random
import sqlite3

import aiofiles
import docx
from PIL import Image
from dotenv import load_dotenv
from openai import AsyncOpenAI

import chainlit as cl
from chainlit.data.sql_alchemy import SQLAlchemyDataLayer
from chainlit.data import get_data_layer
from chainlit.types import ThreadDict
import yaml
from storage.local_storage_client import LocalStorageClient

# Твои импорты
from llm_api.polza_api import prepare_polza_request


# --- НАСТРОЙКИ ---
load_dotenv()
# API_KEY = os.getenv("API_KEY_POLZA_3")
API_KEY = "dloivnzslkbdzsdfrbgdesrfbsewbrfarfdbbbb"
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
STORAGE_PATH = os.path.join(BASE_DIR, "public", "user_attachments")
CONTEXT_PATH = os.path.join(BASE_DIR, "context")
os.makedirs(STORAGE_PATH, exist_ok=True)
os.makedirs(CONTEXT_PATH, exist_ok=True)


WEB_SEARCH_ENGINES = {
    "Auto (Выберет провайдер, но может выбрать и Exa)": "auto",
    "Native (not for Gemini)": "native",
    "Exa (2 р за поиск)": "exa",
}

PDF_PARSING_ENGINES = {
    # "Auto (Выберет провайдер)": "auto",
    "PDF Text (Извлечение текста из PDF)": "pdf-text",
    "Native (Встроенная обработка провайдера)": "native",
    "OCR через Mistral (для сканов и изображений)": "mistral-ocr",
}

REASONING_EFFORT = {
    "Рассуждения отключены": "none",
    "Минимальные рассуждения": "minimal",
    "Сниженные рассуждения": "low",
    "Сбалансированный режим (по умолчанию)": "medium",
    "Детальные рассуждения": "high",
    "Максимально детальные рассуждения": "xhigh",
}

REASONING_SUMMARY = {
    "Автоматически": "auto",
    "Краткое резюме": "concise",
    "Подробное резюме": "detailed",
}


# --- ИНИЦИАЛИЗАЦИЯ БАЗЫ (Для UI и Авторизации) ---


def init_db():
    if os.path.exists("schema.sql"):
        with open("schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()
        with sqlite3.connect("history.db") as conn:
            conn.executescript(schema_sql)
            conn.commit()


init_db()

storage_client = LocalStorageClient(
    base_path=STORAGE_PATH, base_url="http://localhost:8000/public")


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
    """Формирует имя файла на основе времени создания чата и его имени."""
    thread_id = cl.context.session.thread_id
    thread_name = "Новый_чат"

    # По умолчанию берем текущее время (на случай, если тред еще не сохранен в БД)
    created_at = datetime.now()
    need_to_update = True

    data_layer = get_data_layer()
    if data_layer and thread_id:
        # Получаем данные треда из БД
        thread_data = await data_layer.get_thread(cl.context.session.thread_id)

        if thread_data:
            # 1. Достаем имя
            if thread_data.get("name"):
                thread_name = thread_data.get("name")

            # 2. Достаем время создания
            metadata = thread_data.get("metadata", False)
            if metadata:
                try:
                    metadata = json.loads(metadata)
                    created_at_str = metadata.get("created_at", None)
                    if created_at_str:
                        if created_at_str.endswith('Z'):
                            created_at_str = created_at_str[:-1]
                        created_at = datetime.fromisoformat(created_at_str)
                        need_to_update = False
                except json.JSONDecodeError or ValueError:
                    pass
    
    if need_to_update:
        print("created_at is not found")
        await data_layer.update_thread(
            thread_id, metadata={"created_at": created_at.isoformat()}
        )


    # Форматируем время в безопасную строку: Год-Месяц-День_Часы-Минуты-Секунды
    # Пример: 2024-04-14_15-30-00
    time_str = created_at.strftime("%Y-%m-%d_%H-%M-%S")

    # # Очищаем имя треда (берем первые 3 слова, убираем спецсимволы)
    thread_name = "_".join(thread_name.split()[:3])
    safe_thread_name = "".join(
        c for c in thread_name if c.isalnum() or c in (' ', '-', '_')
    ).strip()

    # Формируем итоговое имя файла.
    # Я оставил короткий кусочек thread_id (первые 4 символа) для 100% уникальности,
    # чтобы файлы не перезаписались, если два чата созданы в одну секунду.
    short_id = thread_id[:4] if thread_id else "0000"

    filename = f"{safe_thread_name}_{time_str}_{short_id}.yaml"

    return os.path.join(CONTEXT_PATH, filename)


async def save_context(context_data: list):
    """Асинхронно сохраняет контекст в YAML (идеально для чтения человеком)."""
    filepath = await _get_context_filepath()
    # Замените расширение файла на .yaml в _get_context_filepath(), если выберете этот путь
    try:
        # Превращаем данные в YAML-строку
        yaml_string = yaml.dump(
            context_data,
            allow_unicode=True,       # Сохраняем кириллицу
            default_flow_style=False,  # Разворачиваем списки и словари
            sort_keys=False           # Сохраняем порядок
        )

        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(yaml_string)

    except Exception as e:
        print(f"Ошибка при сохранении контекста: {e}")


async def load_context() -> list:
    """Асинхронно загружает контекст из YAML-файла."""
    filepath = await _get_context_filepath()

    if not os.path.exists(filepath):
        return []

    try:
        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            content = await f.read()
            # Безопасно загружаем YAML
            return yaml.safe_load(content) or []

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

            # Конвертация для JPEG, чтобы избежать ошибки с альфа-каналом
            if img.mode in ("RGBA", "P") and "png" not in mime:
                img = img.convert("RGB")

            # Изменение размера
            img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

            # Определяем форматы и пути
            format_img = "PNG" if "png" in mime else "JPEG"
            final_mime = "image/png" if "png" in mime else "image/jpeg"
            filename = f"compressed_{element.id}.{'png' if format_img == 'PNG' else 'jpg'}"
            cache_path = os.path.join(STORAGE_PATH, filename)

            # СОХРАНЯЕМ ИЗОБРАЖЕНИЕ НАПРЯМУЮ В ФАЙЛ
            img.save(cache_path, format=format_img)

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


def restore_settings(settings):
    widgets = [
        cl.input_widget.Slider(
            id="temperature", label="Temperature", initial=float(settings.get("temperature")), min=0.0, max=2.0, step=0.1),
        cl.input_widget.Slider(
            id="max_tokens", label="Max Tokens", initial=int(settings.get("max_tokens")), min=100, max=64000, step=1),
    ]

    widgets.append(cl.input_widget.Switch(
        id="use_system_prompt",
        label="Использовать системный промпт",
        initial=settings.get("use_system_prompt", False)
    ))

    if "use_system_prompt" in settings and settings["use_system_prompt"]:
        widgets.append(cl.input_widget.TextInput(
            id="system_prompt",
            label="Системный промпт",
            initial=settings.get("system_prompt", SYS_PROMPT)
        ))

    widgets.append(cl.input_widget.Switch(id="use_reasoning",
                   label="Включить размышления", initial=settings.get("use_reasoning", True)))

    if "use_reasoning" in settings and settings["use_reasoning"]:
        widgets.extend([
            cl.input_widget.Select(
                id="reasoning_effort",
                label="Глубина размышлений (effort)",
                values=list(REASONING_EFFORT.keys()),
                initial_index=list(REASONING_EFFORT.keys()).index(
                    settings.get("reasoning_effort"))
            ),
            cl.input_widget.Select(
                id="reasoning_summary",
                label="Детализация резюме (summary)",
                values=list(REASONING_SUMMARY.keys()),
                initial_index=list(REASONING_SUMMARY.keys()).index(
                    settings.get("reasoning_summary")
                )
            ),
            cl.input_widget.Slider(
                id="reasoning_max_tokens",
                label="Лимит токенов мыслей (0 = авто)",
                initial=int(settings.get("reasoning_max_tokens")),
                min=0,
                max=8000,
                step=1
            ),
            cl.input_widget.Switch(
                id="reasoning_exclude",
                label="Скрыть мысли из ответа (exclude)",
                initial=settings.get("reasoning_exclude")
            ),
        ])

    widgets.append(cl.input_widget.Switch(
        id="web_search", label="Включить поиск по интернету", initial=settings.get("web_search")))

    if "web_search" in settings and settings["web_search"]:
        widgets.extend([
            cl.input_widget.Slider(
                id="search_count",
                label="Количество запросов поиска",
                initial=int(settings.get("search_count")),
                min=1,
                max=10,
                step=1,
            ),
            cl.input_widget.Select(
                id="web_engine",
                label="Система поиска (Web Engine)",
                values=list(WEB_SEARCH_ENGINES.keys()),
                initial_index=list(WEB_SEARCH_ENGINES.keys()).index(
                    settings.get("web_engine")),
            )
        ])

    widgets.append(cl.input_widget.Switch(
        id="pdf_parsing", label="Извлечь данные из PDF", initial=settings.get("pdf_parsing")))

    if "pdf_parsing" in settings and settings["pdf_parsing"]:
        widgets.extend([
            cl.input_widget.Select(
                id="pdf_engine",
                label="Система извлечения (PDF Engine)",
                values=list(PDF_PARSING_ENGINES.keys()),
                initial_index=list(PDF_PARSING_ENGINES.keys()).index(
                    settings.get("pdf_engine")
                ),
            )
        ])

    return widgets


def get_default_settings():
    return restore_settings(
        {
            "temperature": 0.7,
            "max_tokens": 8192,
            "use_reasoning": True,
            "reasoning_effort": list(REASONING_EFFORT.keys())[2],
            "reasoning_summary": list(REASONING_SUMMARY.keys())[0],
            "reasoning_max_tokens": 0,
            "reasoning_exclude": False,
            "web_search": True,
            "search_count": 3,
            "web_engine": list(WEB_SEARCH_ENGINES.keys())[0],
            "pdf_parsing": True,
            "pdf_engine": list(PDF_PARSING_ENGINES.keys())[0],
            "use_system_prompt": True,
            "system_prompt": SYS_PROMPT
        }
    )


@cl.on_chat_start
async def on_chat_start():
    settings = await cl.ChatSettings(get_default_settings()).send()
    cl.user_session.set("settings", settings)


@cl.on_settings_edit
async def on_settings_edit(settings):
    current_settings = cl.user_session.get("settings")
    current_web_search = current_settings.get("web_search")
    current_use_reasoning = current_settings.get("use_reasoning")
    need_to_update = False
    if "web_search" in settings and not settings["web_search"] is current_web_search:
        current_settings["web_search"] = settings["web_search"]
        need_to_update = True

    if "use_reasoning" in settings and not settings["use_reasoning"] is current_use_reasoning:
        current_settings["use_reasoning"] = settings["use_reasoning"]
        need_to_update = True

    if "pdf_parsing" in settings and not settings["pdf_parsing"] is current_settings["pdf_parsing"]:
        current_settings["pdf_parsing"] = settings["pdf_parsing"]
        need_to_update = True

    if "use_system_prompt" in settings and not settings["use_system_prompt"] is current_settings["use_system_prompt"]:
        current_settings["use_system_prompt"] = settings["use_system_prompt"]
        need_to_update = True

    if need_to_update:
        cl.user_session.set("settings", current_settings)
        # Формируем базовый список виджетов (он должен включать сам переключатель,
        # чтобы сохранить его состояние)
        current_settings.update(settings)
        widgets = restore_settings(current_settings)

        # Динамически обновляем панель настроек в интерфейсе
        await cl.ChatSettings(widgets).send()


@cl.on_settings_update
async def setup_agent(settings):
    # Формируем базовый список виджетов (он должен включать сам переключатель,
    # чтобы сохранить его состояние)
    widgets = restore_settings(settings)

    # Динамически обновляем панель настроек в интерфейсе
    await cl.ChatSettings(widgets).send()

    # Сохраняем актуальные настройки в сессию, чтобы использовать их при генерации ответов
    print(settings)
    cl.user_session.set("settings", settings)
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

    messages = []

    # Извлекаем шаги (steps) из словаря потока
    for step in thread.get("steps", []):
        step_type = step.get("type")
        content = step.get("output", "")

        # Пропускаем пустые сообщения или технические шаги (например, вызовы функций/tool calls),
        # если вам нужен только чистый текст диалога
        if not content:
            continue

        # Мапим типы шагов Chainlit на стандартные роли LLM
        if step_type == "user_message":
            messages.append({"role": "user", "content": content})
        elif step_type == "assistant_message":
            messages.append({"role": "assistant", "content": content})
        elif step_type == "system_message":
            messages.append({"role": "system", "content": content})

    # Сохраняем восстановленную историю в сессию
    cl.user_session.set("messages", messages)


@cl.on_message
async def on_message(message: cl.Message):
    settings = cl.user_session.get("settings")

    # 1. Загружаем контекст
    context = await load_context()
    if not context:
        messages = cl.user_session.get("messages")
        if messages is not None and len(messages) > 0:
            await cl.ErrorMessage(content="Я не нашла файла с контекстом. Провожу восстановление...").send()
            context = deepcopy(messages)
        else:
            if settings["use_system_prompt"]:
                context = [
                    {"role": "system", "content": settings["system_prompt"]}]
            else:
                context = []
        await save_context(context)

    # 2. Обработка текста и файлов
    prompt_text = message.content
    media_content = []

    if message.elements:
        added_text, media_content = await process_attachments(message.elements)
        prompt_text += added_text

    final_content = media_content + \
        [{"type": "text", "text": prompt_text}] if media_content else prompt_text

    context.append({"role": "user", "content": final_content})

    # 3. Анимация ожидания (теперь используем только один msg)
    msg = cl.Message(content="*Шевелю усами, изучаю...* 🐾", author="Assistant")
    await msg.send()
    animation_task = asyncio.create_task(animate_paws(msg))

    # Флаги состояний
    is_thinking_started = False
    is_answer_started = False
    SEPARATOR = "\n\n---\n\n"  # Разделитель между мыслями и ответом

    try:
        # 4. Вызов API
        openai_messages = await prepare_polza_request(context, API_KEY)

        extra_body = {}

        # --- Подключение плагинов веб-поиска ---
        if settings.get("web_search") == True:
            if not "plugins" in extra_body:
                extra_body["plugins"] = []
            web_search_plugin = {"id": "web"}
            if "search_count" in settings:
                web_search_plugin["max_results"] = int(
                    settings["search_count"])
            web_engine = WEB_SEARCH_ENGINES[settings.get("web_engine")]
            if web_engine != "auto":
                web_search_plugin["web_engine"] = web_engine
            extra_body["plugins"].append(web_search_plugin)

        # --- Подключение размышлений по ФЛАГУ ---
        # Читаем наш новый переключатель (по умолчанию True, если настройки еще не прогрузились)
        use_reasoning = settings.get("use_reasoning", None)

        if use_reasoning:
            reasoning_params = {
                "effort": REASONING_EFFORT[settings.get("reasoning_effort")],
                "summary": REASONING_SUMMARY[settings.get("reasoning_summary")],
                "exclude": settings.get("reasoning_exclude")
            }

            # Добавляем max_tokens только если лимит больше 0
            r_max_tokens = settings.get("reasoning_max_tokens")
            if r_max_tokens > 0:
                reasoning_params["max_tokens"] = int(r_max_tokens)

            extra_body["reasoning"] = reasoning_params

        extract_pdf = settings.get("pdf_parsing")
        if extract_pdf:
            if not "plugins" in extra_body:
                extra_body["plugins"] = []
            engine = PDF_PARSING_ENGINES[settings.get("pdf_engine")]
            extra_body["plugins"].append(
                {"id": "file-parser", "pdf": {"engine": engine}})

        # Вызов API
        stream = await openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=openai_messages,
            temperature=settings.get("temperature", 0.7),
            max_tokens=int(settings.get("max_tokens", 2000)),
            stream=True,
            # Если extra_body пустой, передаем None
            extra_body=extra_body if extra_body else None
        )

        is_first_token = True

        # 5. Обработка стрима в ЕДИНОЕ сообщение с фоном для мыслей
        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta
            content_chunk = getattr(delta, "content", None)

            reasoning_chunk = None
            if hasattr(delta, "model_extra") and delta.model_extra:
                reasoning_chunk = delta.model_extra.get("reasoning")
            if reasoning_chunk is None:
                reasoning_chunk = getattr(delta, "reasoning", None)

            # --- СТРИМИМ РАЗМЫШЛЕНИЯ (В БЛОКЕ ЦИТАТЫ) ---
            if reasoning_chunk:
                if is_first_token:
                    animation_task.cancel()
                    # Начинаем Markdown-цитату
                    msg.content = "> 💭 *Мои мысли:*\n> "
                    is_first_token = False
                    is_thinking_started = True

                # Если модель выдает перенос строки, добавляем символ цитаты
                # чтобы фон не прерывался на новых абзацах
                formatted_chunk = reasoning_chunk.replace('\n', '\n> ')
                await msg.stream_token(formatted_chunk)

            # --- СТРИМИМ ОСНОВНОЙ ОТВЕТ ---
            if content_chunk:
                if is_first_token:
                    animation_task.cancel()
                    msg.content = ""
                    is_first_token = False

                if is_thinking_started and not is_answer_started:
                    # Выходим из цитаты двумя переносами и ставим наш разделитель
                    await msg.stream_token("\n\n---\n\n")
                    is_answer_started = True

                await msg.stream_token(content_chunk)

        await msg.update()

        # 6. Сохраняем ТОЛЬКО финальный ответ в контекст
        final_text = msg.content
        if is_thinking_started and is_answer_started:
            # Отрезаем размышления по разделителю, берем только последнюю часть
            final_text = msg.content.split(SEPARATOR)[-1].strip()

        context.append({"role": "assistant", "content": final_text})

    except Exception as e:
        animation_task.cancel()
        await cl.ErrorMessage(content=f"🚨 Ошибка API: {e}").send()
        if context and context[-1]["role"] == "user":
            context.pop()

        import traceback
        traceback.print_exc()
    finally:
        await save_context(context)
