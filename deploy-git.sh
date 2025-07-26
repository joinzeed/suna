#!/bin/bash

# Suna Git-Based Deploy Script
# Commits local changes, pushes to GitHub, and pulls on VM

set -e

INSTANCE_NAME="suna-instance"
ZONE="europe-west2-a"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Git-Based Deployment ===${NC}"

# Check if there are uncommitted changes
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo -e "${YELLOW}Committing local changes...${NC}"
    
    # Add all changes
    git add .
    
    # Commit with timestamp
    COMMIT_MSG="Deploy: $(date '+%Y-%m-%d %H:%M:%S')"
    if [ ! -z "$1" ]; then
        COMMIT_MSG="Deploy: $1"
    fi
    
    git commit -m "$COMMIT_MSG"
    echo -e "${GREEN}✓ Changes committed${NC}"
else
    echo -e "${YELLOW}No local changes to commit${NC}"
fi

# Push to GitHub
echo -e "${YELLOW}Pushing to GitHub...${NC}"
git push origin main
echo -e "${GREEN}✓ Pushed to GitHub${NC}"

# Pull changes on VM and rebuild
echo -e "${YELLOW}Pulling changes on VM and rebuilding...${NC}"
gcloud compute ssh suna-instance --zone=${ZONE} --command='
    cd /opt/suna
    
    # Pull latest changes
    sudo git pull origin main
    
    # Rebuild containers
    sudo docker compose build --no-cache
    sudo docker compose up -d
    
    echo "✓ Deployment complete!"
'

echo -e "${GREEN}=== Git Deployment Complete! ===${NC}"
echo -e "Frontend: http://34.142.105.100:3000"
echo -e "Backend: http://34.142.105.100:8000"

# Show container status
echo -e "${YELLOW}Container Status:${NC}"
gcloud compute ssh suna-instance --zone=${ZONE} --command='sudo docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"' 