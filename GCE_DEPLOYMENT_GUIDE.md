# Suna GCE Deployment Guide

This guide will help you deploy Suna to Google Cloud Compute Engine using Docker Compose.

## Prerequisites

1. **Google Cloud Account**: You need an active Google Cloud account with billing enabled
2. **gcloud CLI**: Install the Google Cloud SDK on your local machine
3. **Completed Setup**: Run `python setup.py` to generate `.env` and `frontend/.env.local` files

## Quick Deployment

### Option 1: Using the Improved Script (Recommended)

The improved script uses Google Cloud Storage to transfer files, which is more reliable for larger configurations.

```bash
# Make the script executable
chmod +x deploy-gce-improved.sh

# Run the deployment
./deploy-gce-improved.sh
```

### Option 2: Using the Basic Script

The basic script uses instance metadata, which has size limitations but is simpler.

```bash
# Make the script executable
chmod +x deploy-to-gce.sh

# Run the deployment
./deploy-to-gce.sh
```

## What the Scripts Do

1. **Validate Prerequisites**: Check for gcloud CLI and required files
2. **Configure GCP**: Enable necessary APIs and set up the project
3. **Create Firewall Rules**: Allow HTTP (80) and HTTPS (443) traffic
4. **Transfer Files**: Upload your configuration files securely
5. **Create VM Instance**: 
   - Location: London (europe-west2-a)
   - Machine Type: e2-standard-2 (2 vCPU, 8GB RAM)
   - Disk: 20GB SSD
   - OS: Ubuntu 22.04 LTS
6. **Install Docker**: Set up Docker and Docker Compose
7. **Deploy Suna**: Pull images and start all containers
8. **Configure Auto-start**: Ensure Suna starts on VM reboot

## Instance Specifications

- **Machine Type**: e2-standard-2
  - 2 vCPUs
  - 8 GB memory
  - Suitable for moderate workloads
  
- **Disk**: 20 GB standard persistent disk
  - Sufficient for Docker images and application data
  - Can be increased if needed

- **Location**: europe-west2-a (London)
  - Low latency for European users
  - Good availability

## Cost Estimation

Based on Google Cloud pricing (as of 2024):

- **VM (e2-standard-2)**: ~$49/month (running 24/7)
- **Disk (20GB)**: ~$0.80/month
- **Network**: Variable based on usage
- **Total**: ~$50-60/month

To reduce costs:
- Stop the instance when not in use
- Use committed use discounts for long-term deployments
- Consider using e2-micro for testing (free tier eligible)

## Post-Deployment Steps

### 1. Access Your Application

Once deployment is complete, access Suna at:
```
http://YOUR_EXTERNAL_IP
```

### 2. Configure Domain (Optional)

To use a custom domain:

```bash
# Reserve a static IP
gcloud compute addresses create suna-ip --region=europe-west2

# Attach to instance
gcloud compute instances delete-access-config suna-instance --zone=europe-west2-a
gcloud compute instances add-access-config suna-instance --zone=europe-west2-a --address=STATIC_IP
```

### 3. Enable HTTPS (Recommended)

For production use, set up HTTPS with a reverse proxy:

```bash
# SSH into the instance
gcloud compute ssh suna-instance --zone=europe-west2-a

# Install Nginx and Certbot
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx

# Configure Nginx (create /etc/nginx/sites-available/suna)
# Then run Certbot
sudo certbot --nginx -d your-domain.com
```

### 4. Set Up Backups

Create regular snapshots of your disk:

```bash
# Create a snapshot
gcloud compute disks snapshot suna-instance --zone=europe-west2-a --snapshot-names=suna-backup-$(date +%Y%m%d)

# Schedule automatic snapshots
gcloud compute resource-policies create snapshot-schedule suna-backup-schedule \
    --region=europe-west2 \
    --max-retention-days=7 \
    --on-source-disk-delete=keep-auto-snapshots \
    --daily-schedule \
    --start-time=03:00
```

## Monitoring and Maintenance

### Check Application Status

```bash
# View all containers
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker ps'

# View logs
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker compose -f /opt/suna/docker-compose.yaml logs -f'
```

### Update Application

```bash
# SSH into instance
gcloud compute ssh suna-instance --zone=europe-west2-a

# Navigate to app directory
cd /opt/suna

# Pull latest images
sudo docker compose pull

# Restart containers
sudo docker compose down && sudo docker compose up -d
```

### Monitor Resources

```bash
# Check disk usage
gcloud compute ssh suna-instance --zone=europe-west2-a --command='df -h'

# Check memory usage
gcloud compute ssh suna-instance --zone=europe-west2-a --command='free -h'

# Check Docker stats
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker stats --no-stream'
```

## Troubleshooting

### Container Not Starting

1. Check logs:
   ```bash
   gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo journalctl -u suna -f'
   ```

2. Check startup script output:
   ```bash
   gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo cat /var/log/suna-startup.log'
   ```

### Cannot Access Application

1. Verify firewall rules:
   ```bash
   gcloud compute firewall-rules list --filter="name:suna-allow-http OR name:suna-allow-https"
   ```

2. Check if containers are running:
   ```bash
   gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker ps'
   ```

### Out of Disk Space

1. Clean up Docker:
   ```bash
   gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker system prune -a'
   ```

2. Resize disk if needed:
   ```bash
   # Stop instance first
   gcloud compute instances stop suna-instance --zone=europe-west2-a
   
   # Resize disk
   gcloud compute disks resize suna-instance --size=30GB --zone=europe-west2-a
   
   # Start instance
   gcloud compute instances start suna-instance --zone=europe-west2-a
   ```

## Cleanup

To completely remove the deployment:

```bash
# Delete the instance (this will also delete the boot disk)
gcloud compute instances delete suna-instance --zone=europe-west2-a

# Delete firewall rules
gcloud compute firewall-rules delete suna-allow-http suna-allow-https

# Delete any reserved IPs
gcloud compute addresses delete suna-ip --region=europe-west2
```

## Security Recommendations

1. **Use HTTPS**: Always use HTTPS in production
2. **Firewall**: Restrict SSH access to your IP only
3. **Updates**: Regularly update the OS and Docker images
4. **Secrets**: Never commit `.env` files to version control
5. **Backups**: Set up automated backups
6. **Monitoring**: Use Google Cloud Monitoring for alerts

## Support

For issues or questions:
- Check the [Suna documentation](https://github.com/kortix-ai/suna)
- Join the [Discord community](https://discord.gg/Py6pCBUUPw)
- Open an issue on [GitHub](https://github.com/kortix-ai/suna/issues) 