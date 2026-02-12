#!/bin/bash

NETWORK_NAME="turkey-trend-watcher_ttw_network"

sudo docker-compose down --remove-orphans

STUCK_CONTAINERS=$(sudo docker ps -a -q --filter network=$NETWORK_NAME)

if [ ! -z "$STUCK_CONTAINERS" ]; then
sudo docker rm -f $STUCK_CONTAINERS
fi

sudo docker network rm $NETWORK_NAME 2>/dev/null || true