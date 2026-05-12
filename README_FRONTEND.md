# 🛠️ Инструкция для фронтенд разработчиков (Yuviron)

## 📌 Подключение к dev-среде

Архитектурно есть три разные роли:

* **Tailscale** — preferred private access layer для новых устройств.
* **RadminVPN** — legacy compatibility для существующих рабочих мест.
* **CoreDNS on Windows** — optional local DNS endpoint для dev-доменов.

Новые подключения лучше заводить через Tailscale. RadminVPN-инструкция ниже оставлена для совместимости со старой схемой и `radmin_setup.bat`.

### 1. Подключение через Tailscale (рекомендуется)

1. Получите доступ к Tailnet у владельца инфраструктуры.
2. Установите Tailscale на рабочее устройство.
3. Войдите в нужный Tailnet и включите VPN.
4. Проверьте доступ к dev-доменам из раздела ниже.

Если dev-домены не резолвятся через Tailscale, владелец инфраструктуры должен настроить optional CoreDNS on Windows / Split DNS или выдать явный Tailnet IP для проверки.

### 2. Legacy: установка Radmin VPN

* Скачайте и установите **[Radmin VPN](https://www.radmin-vpn.com/)**

### 3. Legacy: подключение к сети

* Откройте Radmin VPN
* Подключитесь к сети:

  * **Логин:** yuviron
  * **Пароль:** yuviron__strong_password_123#!

---

## ⚙️ Настройка окружения

### 4. Legacy: запуск Radmin DNS-скрипта

* Запустите файл:

  ```bash
  radmin_setup.bat
  ```

  Скрипт настраивает alternate DNS только для legacy-интерфейса `Radmin VPN`.

* Дождитесь сообщения в консоли:

  ```
  Alternate DNS set to: 26.240.80.131
  ```

---

### 5. Установка сертификата

1. Откройте файл `rootCA.crt`
2. Нажмите **Install Certificate / Установить сертификат**
3. Выберите:

   * **Local Machine / Локальный компьютер**
4. Выберите хранилище:

   * **Trusted Root Certification Authorities / Доверенные корневые центры сертификации**
5. Завершите установку

---

## 🌐 Доступные домены

После настройки будут доступны следующие сервисы:

* [https://dev.yuviron.com](https://dev.yuviron.com) - dev фронтенд (клиентская часть)
* [https://dev-backoffice.yuviron.com](https://dev-backoffice.yuviron.com) - dev фронтенд (бэкофис)
* [https://dev-admin.yuviron.com](https://dev-admin.yuviron.com) - dev фронтенд (админ панель)

---

## 🔌 Работа с API

Фронтенд-разработчики могут выполнять запросы к API:

* Базовый URL:

  ```
  https://dev-api.yuviron.com/api/
  ```

* Пример запроса:

  ```
  POST https://dev-api.yuviron.com/api/auth/login
  ```

  **Body:**

  ```json
  {
    "login": "your_login",
    "password": "your_password"
  }
  ```

---

## ⚠️ Обработка ошибок API

Рекомендуется предусмотреть обработку ситуаций, когда сервер недоступен:

* `500` - внутренняя ошибка сервера
* `503` - сервис временно недоступен

👉 Рекомендация:

* Показывать пользователю сообщение: "Сервис временно недоступен"
* Добавить retry-логику (например, 1-3 повторных запроса)
* Логировать ошибки для отладки

---

## ✅ Результат

После выполнения всех шагов:

* приватный доступ включен через Tailscale или legacy RadminVPN
* alternate DNS настроен, если используется legacy RadminVPN
* Сертификат установлен
* Доступ к dev-доменам открыт
* API доступен для локальной разработки

---

Если что-то не работает - проверьте:

* Подключение к Tailscale или legacy RadminVPN
* Запуск `radmin_setup.bat`, если используется legacy RadminVPN
* Установку сертификата
