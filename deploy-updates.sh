#!/bin/bash

# Suna Development Deploy Script
# Syncs local changes to GCE VM and rebuilds containers

set -e

INSTANCE_NAME="suna-instance"
ZONE="europe-west2-a"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Syncing Local Changes to GCE VM ===${NC}"

# Function to sync specific directory
sync_directory() {
    local dir=$1
    local desc=$2
    
    echo -e "${YELLOW}Syncing $desc...${NC}"
    
    # Create archive excluding unnecessary files
    tar -czf ${dir}-update.tar.gz \
        --exclude='.git' \
        --exclude='node_modules' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='.env' \
        --exclude='.env.local' \
        --exclude='venv' \
        --exclude='.venv' \
        --exclude='dist' \
        --exclude='build' \
        --exclude='.next' \
        ${dir}/
    
    # Upload to VM
    gcloud compute scp ${dir}-update.tar.gz suna-instance:/tmp/ --zone=${ZONE}
    
    # Extract on VM
    gcloud compute ssh suna-instance --zone=${ZONE} --command="
        cd /opt/suna
        sudo tar -xzf /tmp/${dir}-update.tar.gz
        sudo chown -R root:root ${dir}/
        rm /tmp/${dir}-update.tar.gz
    "
    
    # Clean up local archive
    rm ${dir}-update.tar.gz
    
    echo -e "${GREEN}✓ $desc synced${NC}"
}

# Check what to sync
SYNC_BACKEND=false
SYNC_FRONTEND=false

if [ "$1" == "backend" ] || [ "$1" == "all" ] || [ "$1" == "" ]; then
    SYNC_BACKEND=true
fi

if [ "$1" == "frontend" ] || [ "$1" == "all" ] || [ "$1" == "" ]; then
    SYNC_FRONTEND=true
fi

# Sync directories
if [ "$SYNC_BACKEND" == "true" ]; then
    sync_directory "backend" "Backend code"
    # Also sync docker-compose.yaml when backend changes
    echo -e "${YELLOW}Syncing docker-compose.yaml...${NC}"
    gcloud compute scp docker-compose.yaml suna-instance:/tmp/docker-compose.yaml --zone=${ZONE}
    gcloud compute ssh suna-instance --zone=${ZONE} --command="
        sudo mv /tmp/docker-compose.yaml /opt/suna/docker-compose.yaml
        sudo chown root:root /opt/suna/docker-compose.yaml
    "
    echo -e "${GREEN}✓ docker-compose.yaml synced${NC}"
fi

if [ "$SYNC_FRONTEND" == "true" ]; then
    sync_directory "frontend" "Frontend code"
fi

# Rebuild and restart containers
echo -e "${YELLOW}Rebuilding and restarting containers...${NC}"

REBUILD_CMD="cd /opt/suna && "

if [ "$SYNC_BACKEND" == "true" ]; then
    REBUILD_CMD+="sudo docker compose build backend worker --no-cache && "
fi

if [ "$SYNC_FRONTEND" == "true" ]; then
    REBUILD_CMD+="sudo docker compose build frontend --no-cache && "
fi

REBUILD_CMD+="sudo docker compose up -d"

gcloud compute ssh suna-instance --zone=${ZONE} --command="$REBUILD_CMD"

# Reload Nginx to ensure proper connection to updated containers
echo -e "${YELLOW}Reloading Nginx...${NC}"
gcloud compute ssh suna-instance --zone=${ZONE} --command="sudo nginx -t && sudo systemctl reload nginx"
echo -e "${GREEN}✓ Nginx reloaded${NC}"

echo -e "${GREEN}=== Deployment Complete! ===${NC}"
echo -e "Frontend: http://34.142.105.100:3000"
echo -e "Backend: http://34.142.105.100:8000"

# Show container status
echo -e "${YELLOW}Container Status:${NC}"
gcloud compute ssh suna-instance --zone=${ZONE} --command='sudo docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"' 