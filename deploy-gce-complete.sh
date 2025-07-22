#!/bin/bash

# Suna GCE Complete Deployment Script
# This script deploys Suna to Google Cloud Compute Engine with the entire codebase

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-}"
INSTANCE_NAME="${1:-suna-instance}"
ZONE="europe-west2-a"  # London
MACHINE_TYPE="e2-standard-2"
BOOT_DISK_SIZE="30"  # Increased for codebase
IMAGE_FAMILY="ubuntu-2204-lts"
IMAGE_PROJECT="ubuntu-os-cloud"
NETWORK_TAG="suna-server"
BUCKET_NAME="suna-deployment-$(date +%s)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Suna GCE Complete Deployment Script ===${NC}"

# Check if gcloud is installed
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}Error: gcloud CLI is not installed. Please install it first.${NC}"
    echo "Visit: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# Check if project ID is set
if [ -z "$PROJECT_ID" ]; then
    echo -e "${YELLOW}Enter your Google Cloud Project ID:${NC}"
    read -r PROJECT_ID
fi

# Set the project
echo -e "${GREEN}Setting project to: $PROJECT_ID${NC}"
gcloud config set project "$PROJECT_ID"

# Check if required files exist
if [ ! -f "backend/.env" ] || [ ! -f "frontend/.env.local" ] || [ ! -f "docker-compose.yaml" ]; then
    echo -e "${RED}Error: Required files not found!${NC}"
    echo "Please ensure backend/.env, frontend/.env.local, and docker-compose.yaml exist."
    exit 1
fi

# Enable required APIs
echo -e "${GREEN}Enabling required Google Cloud APIs...${NC}"
gcloud services enable compute.googleapis.com storage-component.googleapis.com

# Create a temporary bucket for deployment files
echo -e "${GREEN}Creating temporary GCS bucket for deployment files...${NC}"
gsutil mb -p "$PROJECT_ID" -c standard -l europe-west2 "gs://$BUCKET_NAME/" || {
    echo -e "${RED}Failed to create bucket. Using existing bucket name...${NC}"
    BUCKET_NAME="suna-deployment-$PROJECT_ID"
    gsutil mb -p "$PROJECT_ID" -c standard -l europe-west2 "gs://$BUCKET_NAME/" 2>/dev/null || true
}

# Create a tarball of the project excluding unnecessary files
echo -e "${GREEN}Creating project archive...${NC}"
tar -czf suna-project.tar.gz \
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
    backend frontend docker-compose.yaml

# Upload project archive and env files to bucket
echo -e "${GREEN}Uploading project files to GCS...${NC}"
gsutil -o GSUtil:parallel_composite_upload_threshold=150M cp suna-project.tar.gz "gs://$BUCKET_NAME/"
gsutil cp backend/.env "gs://$BUCKET_NAME/backend.env"
gsutil cp frontend/.env.local "gs://$BUCKET_NAME/frontend.env.local"

# Clean up local archive
rm -f suna-project.tar.gz

# Create firewall rules
echo -e "${GREEN}Creating firewall rules...${NC}"
gcloud compute firewall-rules create suna-allow-http \
    --allow tcp:80 \
    --source-ranges 0.0.0.0/0 \
    --target-tags "$NETWORK_TAG" \
    --description "Allow HTTP traffic for Suna" \
    2>/dev/null || echo "Firewall rule suna-allow-http already exists"

gcloud compute firewall-rules create suna-allow-https \
    --allow tcp:443 \
    --source-ranges 0.0.0.0/0 \
    --target-tags "$NETWORK_TAG" \
    --description "Allow HTTPS traffic for Suna" \
    2>/dev/null || echo "Firewall rule suna-allow-https already exists"

# Create startup script
cat > startup-script.sh << EOF
#!/bin/bash

# Log all output
exec > >(tee -a /var/log/suna-startup.log)
exec 2>&1

echo "Starting Suna deployment at \$(date)"

# Update system
apt-get update
apt-get upgrade -y

# Install required packages
apt-get install -y curl wget software-properties-common tar

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose
apt-get install -y docker-compose-plugin

# Create app directory
mkdir -p /opt/suna
cd /opt/suna

# Start Docker service
systemctl start docker
systemctl enable docker

# Install Google Cloud SDK (if not present)
if ! command -v gsutil &> /dev/null; then
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
    apt-get install -y apt-transport-https ca-certificates gnupg
    curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key --keyring /usr/share/keyrings/cloud.google.gpg add -
    apt-get update && apt-get install -y google-cloud-sdk
fi

# Download and extract project files
echo "Downloading project files..."
gsutil cp "gs://$BUCKET_NAME/suna-project.tar.gz" .
tar -xzf suna-project.tar.gz
rm suna-project.tar.gz

# Download env files
gsutil cp "gs://$BUCKET_NAME/backend.env" backend/.env
gsutil cp "gs://$BUCKET_NAME/frontend.env.local" frontend/.env.local

# Verify files
echo "Verifying project structure..."
ls -la
ls -la backend/
ls -la frontend/

# Build Docker images first (in case the registry images are not accessible)
echo "Building Docker images..."
docker compose build

# Start containers
echo "Starting Docker containers..."
docker compose up -d

# Wait for containers to start
sleep 20

# Show container status
docker compose ps

# Setup auto-restart service
cat > /etc/systemd/system/suna.service << 'SYSD'
[Unit]
Description=Suna Application
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/suna
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SYSD

systemctl daemon-reload
systemctl enable suna.service

# Clean up - delete the deployment files from GCS
echo "Cleaning up deployment files..."
gsutil -m rm -r "gs://$BUCKET_NAME/" || true

# Setup log rotation
cat > /etc/logrotate.d/suna << 'LOGR'
/var/log/suna-*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
LOGR

# Log completion
echo "Suna deployment completed successfully at \$(date)!" | tee /var/log/suna-deploy.log

# Show final status
echo "=== Docker Container Status ==="
docker ps
echo "=== Deployment Complete ==="
EOF

# Create the instance
echo -e "${GREEN}Creating GCE instance...${NC}"
echo -e "${YELLOW}Instance details:${NC}"
echo "  Name: $INSTANCE_NAME"
echo "  Zone: $ZONE (London)"
echo "  Machine Type: $MACHINE_TYPE"
echo "  Disk Size: ${BOOT_DISK_SIZE}GB"

gcloud compute instances create "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --network-interface=network-tier=PREMIUM,subnet=default \
    --maintenance-policy=MIGRATE \
    --provisioning-model=STANDARD \
    --tags="$NETWORK_TAG" \
    --create-disk=auto-delete=yes,boot=yes,device-name="$INSTANCE_NAME",image-family="$IMAGE_FAMILY",image-project="$IMAGE_PROJECT",mode=rw,size="$BOOT_DISK_SIZE",type=pd-standard \
    --scopes=https://www.googleapis.com/auth/devstorage.read_write,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/trace.append \
    --metadata startup-script="$(cat startup-script.sh)",bucket-name="$BUCKET_NAME"

# Wait for instance to be ready
echo -e "${GREEN}Waiting for instance to initialize...${NC}"
sleep 30

# Get instance IP
EXTERNAL_IP=$(gcloud compute instances describe "$INSTANCE_NAME" \
    --zone="$ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo -e "${GREEN}=== Deployment Information ===${NC}"
echo -e "Instance Name: ${YELLOW}$INSTANCE_NAME${NC}"
echo -e "External IP: ${YELLOW}$EXTERNAL_IP${NC}"
echo -e "Zone: ${YELLOW}$ZONE${NC}"
echo ""
echo -e "${GREEN}The instance is being configured. This may take 10-15 minutes.${NC}"
echo -e "${YELLOW}Note: Building Docker images will take additional time.${NC}"
echo ""
echo -e "Monitor deployment progress:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo tail -f /var/log/suna-startup.log'${NC}"
echo ""
echo -e "Check instance startup logs:"
echo -e "  ${YELLOW}gcloud compute instances get-serial-port-output $INSTANCE_NAME --zone=$ZONE${NC}"
echo ""
echo -e "Once ready, access Suna at:"
echo -e "  ${YELLOW}http://$EXTERNAL_IP:3000${NC} (Frontend)"
echo -e "  ${YELLOW}http://$EXTERNAL_IP:8000${NC} (Backend API)"
echo ""
echo -e "${GREEN}=== Useful Commands ===${NC}"
echo -e "SSH into instance:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE${NC}"
echo ""
echo -e "Check Docker containers:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo docker ps'${NC}"
echo ""
echo -e "View application logs:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo docker compose -f /opt/suna/docker-compose.yaml logs -f'${NC}"
echo ""
echo -e "Restart application:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo systemctl restart suna'${NC}"

# Clean up local startup script
rm -f startup-script.sh

echo -e "${GREEN}Deployment script completed!${NC}"
echo -e "${YELLOW}Note: The temporary GCS bucket will be automatically deleted after deployment.${NC}" 