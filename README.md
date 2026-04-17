# NekoChat
<img width="504" height="248" alt="image" src="https://github.com/user-attachments/assets/8e9273eb-458e-4c0c-962e-32605b8d7274" />
Интерактивное приложение‑чатбот на базе Chainlit для работы с LLM через API Polza.

## Prerequisites

Перед началом убедитесь, что у вас установлены:

* Python 3.8 или выше
* pip (менеджер пакетов Python)

## Установка и настройка

### Шаг 1. Клонирование репозитория

```bash
git clone https://github.com/DaniilTUPYAKOV/NekoChat.git
cd NekoChat
```
Шаг 2. Создание и активация виртуального окружения
Windows:

```bash
python -m venv my_chainlit_app
my_chainlit_app\Scripts\activate
```
macOS/Linux:

```bash
python3 -m venv my_chainlit_app
source my_chainlit_app/bin/activate
```
Шаг 3. Установка зависимостей
Установите все необходимые пакеты из файла requirements.txt:

```bash
pip install -r requirements.txt
Шаг 4. Настройка переменных окружения
```
Создайте файл .env в корне проекта.

Добавьте в него следующие переменные и заполните их актуальными значениями:

```env
API_KEY_POLZA=your_actual_polza_api_key
CHAINLIT_AUTH_SECRET=your_secret_auth_string
APP_USERNAME=your_desired_username
APP_PASSWORD=your_desired_password
```
Описание переменных:

API_KEY_POLZA — API‑ключ для доступа к сервису Polza (или другому LLM‑провайдеру).

CHAINLIT_AUTH_SECRET — секретный ключ для аутентификации в Chainlit. Должен быть длинной случайной строкой.

APP_USERNAME и APP_PASSWORD — логин и пароль для доступа к веб‑интерфейсу вашего приложения.

Шаг 5. Запуск приложения
Запустите приложение с помощью команды:

```bash
chainlit run app.py
```
После успешного запуска в консоли появится сообщение со ссылкой (обычно http://localhost:8000). Откройте эту ссылку в браузере.

Использование
Перейдите по адресу http://localhost:8000.

Войдите в систему, используя учётные данные, указанные в переменных APP_USERNAME и APP_PASSWORD.

Начните общение с LLM в интерфейсе Neko, похожем на ChatGPT.

Структура проекта
.
├── app.py              # Основной файл приложения Chainlit
├── requirements.txt    # Список зависимостей Python
└── .env              # Файл с конфиденциальными переменными окружения
