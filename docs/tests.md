# Тесты

Как запускать и поддерживать тесты инфраструктурных скриптов.

[← К README](../README.md)

## Быстрый запуск

```bash
python3 -m unittest discover -s scripts/tests
```

Запуск одного тестового файла:

```bash
python3 -m unittest scripts/tests/test_generate_config.py
```

## Принципы

* Тесты должны работать без настоящих секретов и локальных `env/dev.env` или `env/prod.env`.
* Если тесту нужен env-файл, он создаёт временную fixture в `tempfile.TemporaryDirectory()`.
* Для тестовой генерации runtime-конфига используйте `scripts/generate-config.py --common-env-file ... --env-file ...`.
* Эти override-флаги предназначены только для test/CI fixtures; для реального запуска нужно создать `env/<env>.env` из `env/example.env` и заполнить настоящие значения.

## Генерация конфига в тестах

Тест `scripts/tests/test_generate_config.py` создаёт временные `common.env` и `dev.env`, передаёт их в CLI и проверяет, что в `deploy.env` не попали реальные локальные dev-секреты.

```bash
python3 scripts/generate-config.py \
  --env dev \
  --domain example.com \
  --apps admin,backoffice \
  --output-dir /tmp/yuviron-generated \
  --common-env-file /tmp/common.env \
  --env-file /tmp/dev.env
```

CLI при таком запуске предупреждает, что используется временный/override ENV и для реальной работы нужно создать настоящий `env/dev.env`.
