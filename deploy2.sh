#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER=root
REMOTE_HOST="${REMOTE_HOST:-YOUR_SERVER_IP}"
REMOTE_DIR=/opt/landingbot2

echo "==> Создание директории на сервере..."
ssh ${REMOTE_USER}@${REMOTE_HOST} "mkdir -p ${REMOTE_DIR}"

echo "==> Копирование файлов на ${REMOTE_HOST}..."
scp bot.py db.py scheduler.py keyboards.py texts.py config.py requirements.txt \
    landingbot2.service \
    ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/

echo "==> Копирование .env для второго бота..."
scp .env2 ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/.env

echo "==> Установка зависимостей и перезапуск сервиса..."
ssh ${REMOTE_USER}@${REMOTE_HOST} bash << 'ENDSSH'
set -euo pipefail
cd /opt/landingbot2

# Создать venv если нет
if [ ! -d venv ]; then
  python3 -m venv venv
fi

venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt

# Установить systemd-юнит
cp landingbot2.service /etc/systemd/system/landingbot2.service
systemctl daemon-reload
systemctl enable landingbot2
systemctl restart landingbot2

echo ""
echo "==> Готово. Статус:"
systemctl status landingbot2 --no-pager -l
ENDSSH
