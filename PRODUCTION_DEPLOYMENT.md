# Suna Production Deployment

This guide shows you how to deploy Suna to production in one command with all fixes included.

## Prerequisites

1. **Google Cloud account** with billing enabled
2. **gcloud CLI** installed and authenticated
3. **Domain name** (for SSL certificate)
4. **Completed setup**: Run `python setup.py` to generate `.env` files

## One-Command Deployment

```bash
# Make the script executable
chmod +x deploy-production-complete.sh

# Deploy with your domain and email
./deploy-production-complete.sh suna-instance your-subdomain.yourdomain.com your-email@example.com
```

### Parameters:
- `suna-instance` - Instance name (optional, defaults to "suna-instance")
- `your-subdomain.yourdomain.com` - Your domain for SSL (required)
- `your-email@example.com` - Email for Let's Encrypt SSL (required)

## What This Script Does

✅ **Complete GCE Setup**
- Creates VM instance in London (europe-west2-a)
- Configures firewall rules for HTTP/HTTPS
- Sets up automatic startup/restart

✅ **SSL & Security**
- Installs Let's Encrypt SSL certificate
- Configures Nginx reverse proxy
- Sets up automatic certificate renewal
- Adds security headers

✅ **CORS Fix**
- Properly configured CORS headers
- No Cloudflare conflicts
- Backend handles CORS correctly

✅ **Billing Disabled**
- All users get unlimited access
- No subscription checks
- All models available

✅ **Production Ready**
- Auto-restart on reboot
- Log rotation
- Health monitoring
- Error handling

## Post-Deployment Steps

### 1. Set Up DNS (Required)

Add this A record in your DNS provider (e.g., Cloudflare):

```
Type: A
Name: your-subdomain
Value: [IP from script output]
Proxy: Disabled (gray cloud in Cloudflare)
```

### 2. Monitor Deployment

```bash
# Watch deployment progress
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo tail -f /var/log/suna-startup.log'
```

### 3. Test When Ready

- **Frontend**: `https://your-subdomain.yourdomain.com`
- **API**: `https://your-subdomain.yourdomain.com/api/health`

## Cost Estimate

- **VM (e2-standard-2)**: ~$50/month
- **Disk (30GB)**: ~$1/month
- **SSL Certificate**: Free (Let's Encrypt)
- **Total**: ~$51/month

## Troubleshooting

### SSL Certificate Issues
```bash
# Check Let's Encrypt logs
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo tail -f /var/log/letsencrypt/letsencrypt.log'
```

### Application Issues
```bash
# Check container status
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker ps'

# Check application logs
gcloud compute ssh suna-instance --zone=europe-west2-a --command='sudo docker compose -f /opt/suna/docker-compose.yaml logs -f'
```

### Update Application
```bash
# Use the existing update script
./deploy-updates.sh backend  # or frontend, or all
```

## Clean Up

To completely remove the deployment:

```bash
# Delete the instance
gcloud compute instances delete suna-instance --zone=europe-west2-a

# Delete firewall rules
gcloud compute firewall-rules delete suna-allow-http suna-allow-https
```

## Features Included

- 🔐 **End-to-end HTTPS** with automatic certificate renewal
- 🌐 **CORS properly configured** for your domain
- 💰 **Billing disabled** - unlimited access for all users
- 🔄 **Auto-restart** on VM reboot
- 📊 **Log rotation** and monitoring
- 🛡️ **Security headers** and best practices
- ⚡ **Production-ready** configuration

This script consolidates all the fixes we implemented into one seamless deployment process. 