#!/bin/bash
# Script that waits for DNS to update, then configures HTTPS
# Usage: sudo ./deployment/wait-and-configure-https.sh yourcottoncandy.com

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo ./wait-and-configure-https.sh your-domain.com"
    exit 1
fi

DOMAIN=$1
TARGET_IP="44.196.144.24"
MAX_WAIT=300  # 5 minutes max wait
CHECK_INTERVAL=30  # Check every 30 seconds

echo "========================================="
echo "Waiting for DNS to update for $DOMAIN"
echo "========================================="
echo "Target IP: $TARGET_IP"
echo ""

# Update Nginx configuration with domain name first
echo "Updating Nginx configuration..."
sudo sed -i "s/server_name .*/server_name $DOMAIN;/" /etc/nginx/sites-available/hostaway
sudo nginx -t && sudo systemctl reload nginx

# Wait for DNS to update
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    CURRENT_IP=$(dig +short $DOMAIN | tail -1)
    
    echo "Checking DNS... (elapsed: ${ELAPSED}s)"
    echo "  Domain resolves to: $CURRENT_IP"
    echo "  Target IP: $TARGET_IP"
    
    if [ "$CURRENT_IP" = "$TARGET_IP" ]; then
        echo ""
        echo "✓ DNS is updated! Proceeding with HTTPS configuration..."
        break
    fi
    
    if [ $ELAPSED -eq 0 ]; then
        echo ""
        echo "Please update your DNS A record:"
        echo "  $DOMAIN → $TARGET_IP"
        echo ""
        echo "Waiting for DNS propagation..."
    fi
    
    sleep $CHECK_INTERVAL
    ELAPSED=$((ELAPSED + CHECK_INTERVAL))
done

if [ "$CURRENT_IP" != "$TARGET_IP" ]; then
    echo ""
    echo "⚠️  DNS has not updated after $MAX_WAIT seconds."
    echo "Please update your DNS A record and run this script again."
    exit 1
fi

# Wait a bit more for DNS to fully propagate
echo "Waiting additional 30 seconds for DNS to fully propagate..."
sleep 30

# Obtain SSL certificate
echo ""
echo "========================================="
echo "Obtaining SSL Certificate"
echo "========================================="

sudo certbot --nginx -d $DOMAIN \
    --non-interactive \
    --agree-tos \
    --email admin@$DOMAIN \
    --redirect \
    --quiet

# Test certificate renewal
echo ""
echo "Testing certificate auto-renewal..."
sudo certbot renew --dry-run

echo ""
echo "========================================="
echo "HTTPS Configuration Complete!"
echo "========================================="
echo ""
echo "Your application is now accessible at:"
echo "  https://$DOMAIN"
echo ""
echo "HTTP will automatically redirect to HTTPS."
echo ""
echo "Testing HTTPS..."
sleep 5
curl -s https://$DOMAIN/health | python3 -m json.tool 2>/dev/null || curl -s https://$DOMAIN/health

echo ""
echo "Certificate info:"
sudo certbot certificates

