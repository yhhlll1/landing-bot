#!/usr/bin/env bash
set -euo pipefail

REMOTE_USER=root
REMOTE_HOST="${REMOTE_HOST:-YOUR_SERVER_IP}"
BUILD_DIR=/opt/landingbot
COMPOSE_DIR=/root/landingbot

echo "==> Копирование файлов на ${REMOTE_HOST}..."
scp bot.py db.py scheduler.py keyboards.py texts.py config.py requirements.txt \
    ${REMOTE_USER}@${REMOTE_HOST}:${BUILD_DIR}/

echo "==> Пересборка образа и перезапуск контейнера..."
ssh ${REMOTE_USER}@${REMOTE_HOST} bash << 'ENDSSH'
set -euo pipefail
cd /opt/landingbot
docker build -t landingbot:latest .
cd /root/landingbot
docker-compose up -d --force-recreate

echo ""
echo "==> Готово. Логи:"
sleep 3
docker logs landingbot --tail 10
ENDSSH
