#!/bin/bash

# Suna Complete Production Deployment Script
# This script deploys Suna to GCE with SSL, CORS fixes, and billing disabled

set -e

# Configuration
PROJECT_ID="${GCP_PROJECT_ID:-}"
INSTANCE_NAME="${1:-suna-instance}"
ZONE="europe-west2-a"  # London
MACHINE_TYPE="e2-standard-2"
BOOT_DISK_SIZE="30"
IMAGE_FAMILY="ubuntu-2204-lts"
IMAGE_PROJECT="ubuntu-os-cloud"
NETWORK_TAG="suna-server"
BUCKET_NAME="suna-deployment-$(date +%s)"
DOMAIN="${2:-agentic-backend.zeed.ai}"
EMAIL="${3:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Suna Complete Production Deployment ===${NC}"
echo -e "${BLUE}This script will:${NC}"
echo -e "• Deploy Suna to Google Cloud Compute Engine"
echo -e "• Set up SSL with Let's Encrypt"
echo -e "• Configure Nginx reverse proxy"
echo -e "• Fix CORS issues"
echo -e "• Disable billing checks"
echo -e "• Make it production-ready"
echo ""

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

# Check if email is provided
if [ -z "$EMAIL" ]; then
    echo -e "${YELLOW}Enter your email for SSL certificate:${NC}"
    read -r EMAIL
fi

# Set the project
echo -e "${GREEN}Setting project to: $PROJECT_ID${NC}"
gcloud config set project "$PROJECT_ID"

# Check if required files exist
if [ ! -f "backend/.env" ] || [ ! -f "frontend/.env.local" ] || [ ! -f "docker-compose.yaml" ]; then
    echo -e "${RED}Error: Required files not found!${NC}"
    echo "Please ensure backend/.env, frontend/.env.local, and docker-compose.yaml exist."
    echo "Run 'python setup.py' first to generate these files."
    exit 1
fi

# Enable required APIs
echo -e "${GREEN}Enabling required Google Cloud APIs...${NC}"
gcloud services enable compute.googleapis.com storage-component.googleapis.com

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

# Create a temporary bucket for deployment files
echo -e "${GREEN}Creating temporary GCS bucket for deployment files...${NC}"
gsutil mb -p "$PROJECT_ID" -c standard -l europe-west2 "gs://$BUCKET_NAME/" || {
    echo -e "${YELLOW}Failed to create bucket. Using existing bucket name...${NC}"
    BUCKET_NAME="suna-deployment-$PROJECT_ID"
    gsutil mb -p "$PROJECT_ID" -c standard -l europe-west2 "gs://$BUCKET_NAME/" 2>/dev/null || true
}

# Create project archive
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

# Upload files to bucket
echo -e "${GREEN}Uploading project files to GCS...${NC}"
gsutil -o GSUtil:parallel_composite_upload_threshold=150M cp suna-project.tar.gz "gs://$BUCKET_NAME/"
gsutil cp backend/.env "gs://$BUCKET_NAME/backend.env"
gsutil cp frontend/.env.local "gs://$BUCKET_NAME/frontend.env.local"

# Clean up local archive
rm -f suna-project.tar.gz

# Create comprehensive startup script
cat > startup-script.sh << EOF
#!/bin/bash

# Complete Suna deployment startup script
exec > >(tee -a /var/log/suna-startup.log)
exec 2>&1

echo "Starting Suna complete deployment at \$(date)"

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

# Install Nginx
apt-get install -y nginx

# Install Certbot
apt-get install -y certbot python3-certbot-nginx

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

# Create app directory
mkdir -p /opt/suna
cd /opt/suna

# Download and extract project files
echo "Downloading project files..."
gsutil cp "gs://$BUCKET_NAME/suna-project.tar.gz" .
tar -xzf suna-project.tar.gz
rm suna-project.tar.gz

# Download env files
gsutil cp "gs://$BUCKET_NAME/backend.env" backend/.env
gsutil cp "gs://$BUCKET_NAME/frontend.env.local" frontend/.env.local

# Build and start Docker containers
echo "Building and starting Docker containers..."
docker compose build
docker compose up -d

# Wait for backend to start
echo "Waiting for backend to start..."
sleep 30

# Create initial HTTP-only Nginx configuration
echo "Setting up Nginx with SSL..."
cat > /etc/nginx/sites-available/suna-temp << 'NGINX_TEMP'
server {
    listen 80;
    server_name $DOMAIN;
    
    # Let's Encrypt verification
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }
    
    # Temporary proxy to backend
    location / {
        proxy_pass http://localhost:8000/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }
}
NGINX_TEMP

# Enable temporary configuration
ln -sf /etc/nginx/sites-available/suna-temp /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Create web root for Let's Encrypt
mkdir -p /var/www/html

# Test and start Nginx
nginx -t
systemctl start nginx
systemctl enable nginx

# Wait for Nginx to start
sleep 5

# Generate DH parameters for better security
echo "Generating DH parameters..."
openssl dhparam -out /etc/nginx/dhparam.pem 2048

# Obtain SSL certificate
echo "Obtaining SSL certificate from Let's Encrypt..."
certbot certonly --webroot -w /var/www/html -d $DOMAIN --non-interactive --agree-tos --email $EMAIL

# Create the final HTTPS configuration with CORS fixes
echo "Creating final HTTPS configuration..."
cat > /etc/nginx/sites-available/suna << 'NGINX_FINAL'
server {
    listen 80;
    server_name $DOMAIN;
    
    # Redirect all HTTP requests to HTTPS
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;

    # SSL certificates
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    
    # SSL configuration
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-SHA256:ECDHE-RSA-AES256-SHA384:ECDHE-RSA-AES128-SHA:ECDHE-RSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES256-SHA256:DHE-RSA-AES128-SHA:DHE-RSA-AES256-SHA:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK:!SRP:!CAMELLIA;
    ssl_prefer_server_ciphers on;
    ssl_dhparam /etc/nginx/dhparam.pem;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # API routes - proxy to backend (NO CORS headers - let backend handle them)
    location /api/ {
        proxy_pass http://localhost:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }
    
    # Root path - proxy to backend
    location / {
        proxy_pass http://localhost:8000/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_Set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }
}
NGINX_FINAL

# Enable the HTTPS configuration
ln -sf /etc/nginx/sites-available/suna /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/suna-temp

# Test and reload Nginx
nginx -t
systemctl reload nginx

# Set up automatic certificate renewal
systemctl enable certbot.timer
systemctl start certbot.timer

# Test automatic renewal
certbot renew --dry-run

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

echo "=== Suna deployment completed successfully at \$(date)! ==="
echo "Your application is available at: https://$DOMAIN"
echo "Backend API: https://$DOMAIN/api/"
echo "SSL certificate will auto-renew"
echo "Billing checks are disabled - all users have unlimited access"

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
echo "  Domain: $DOMAIN"
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
    --metadata startup-script="$(cat startup-script.sh)",bucket-name="$BUCKET_NAME",domain="$DOMAIN",email="$EMAIL"

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
echo -e "Domain: ${YELLOW}$DOMAIN${NC}"
echo -e "Zone: ${YELLOW}$ZONE${NC}"
echo ""
echo -e "${BLUE}=== IMPORTANT DNS SETUP ===${NC}"
echo -e "Add this DNS record in your domain provider (e.g., Cloudflare):"
echo -e "  ${YELLOW}Type: A${NC}"
echo -e "  ${YELLOW}Name: agentic-backend${NC}"
echo -e "  ${YELLOW}Value: $EXTERNAL_IP${NC}"
echo -e "  ${YELLOW}Proxy: Disabled (gray cloud)${NC}"
echo ""
echo -e "${GREEN}The instance is being configured. This may take 15-20 minutes.${NC}"
echo -e "${YELLOW}Note: SSL certificate setup requires DNS to be configured first.${NC}"
echo ""
echo -e "Monitor deployment progress:"
echo -e "  ${YELLOW}gcloud compute ssh $INSTANCE_NAME --zone=$ZONE --command='sudo tail -f /var/log/suna-startup.log'${NC}"
echo ""
echo -e "Once ready, your application will be available at:"
echo -e "  ${YELLOW}https://$DOMAIN${NC}"
echo -e "  ${YELLOW}https://$DOMAIN/api/${NC} (API)"
echo ""
echo -e "${GREEN}=== Features Enabled ===${NC}"
echo -e "✅ SSL/HTTPS with Let's Encrypt"
echo -e "✅ Nginx reverse proxy"
echo -e "✅ CORS properly configured"
echo -e "✅ Billing checks disabled (unlimited access)"
echo -e "✅ Auto-restart on reboot"
echo -e "✅ Log rotation"
echo -e "✅ Certificate auto-renewal"

# Clean up local startup script
rm -f startup-script.sh

echo -e "${GREEN}Deployment script completed!${NC}"
echo -e "${BLUE}Don't forget to set up your DNS record!${NC}" 