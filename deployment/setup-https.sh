#!/bin/bash
# Script to set up HTTPS with Let's Encrypt
# Usage: sudo ./deployment/setup-https.sh your-domain.com

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo ./setup-https.sh your-domain.com"
    exit 1
fi

DOMAIN=$1

echo "========================================="
echo "Setting up HTTPS for $DOMAIN"
echo "========================================="

# Update Nginx configuration with domain name
echo "Updating Nginx configuration with domain name..."
sudo sed -i "s/server_name .*/server_name $DOMAIN;/" /etc/nginx/sites-available/hostaway

# Test Nginx configuration
echo "Testing Nginx configuration..."
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx

# Obtain SSL certificate
echo ""
echo "Obtaining SSL certificate from Let's Encrypt..."
echo "This will prompt for your email address and agreement to terms."
echo ""

sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN --redirect

# Test certificate renewal
echo ""
echo "Testing certificate renewal..."
sudo certbot renew --dry-run

echo ""
echo "========================================="
echo "HTTPS setup complete!"
echo "========================================="
echo ""
echo "Your application is now accessible at:"
echo "  https://$DOMAIN"
echo ""
echo "HTTP will automatically redirect to HTTPS."
echo ""
echo "Certificate will auto-renew. Check status with:"
echo "  sudo certbot certificates"

