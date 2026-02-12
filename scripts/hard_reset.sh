#!/bin/bash

NETWORK_NAME="turkey-trend-watcher_ttw_network"
CHROMA_VOLUME="turkey-trend-watcher_chroma_data"

echo "Nuclear Reset Starting..."

sudo docker-compose down -v --remove-orphans

STUCK_CONTAINERS=$(sudo docker ps -a -q --filter network=$NETWORK_NAME)
if [ ! -z "$STUCK_CONTAINERS" ]; then
sudo docker rm -f $STUCK_CONTAINERS
fi

sudo docker volume rm $CHROMA_VOLUME 2>/dev/null || true
sudo docker network rm $NETWORK_NAME 2>/dev/null || true

find . -type d -name "pycache" -exec rm -rf {} +

echo "Building fresh containers..."
sudo docker-compose build --no-cache
sudo docker-compose --profile workers up -d

echo "Reset Complete."