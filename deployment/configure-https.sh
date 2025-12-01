#!/bin/bash
# HTTPS Configuration Script
# Waits for DNS propagation and configures HTTPS
# Usage: sudo ./deployment/configure-https.sh yourcottoncandy.com

set -e

if [ -z "$1" ]; then
    echo "Usage: sudo ./configure-https.sh your-domain.com"
    exit 1
fi

DOMAIN=$1
TARGET_IP="44.196.144.24"
MAX_ATTEMPTS=10
WAIT_BETWEEN_ATTEMPTS=60  # 1 minute

echo "========================================="
echo "HTTPS Configuration for $DOMAIN"
echo "========================================="
echo "Target IP: $TARGET_IP"
echo ""

# Update Nginx config with domain
sudo sed -i "s/server_name .*/server_name $DOMAIN;/" /etc/nginx/sites-available/hostaway
sudo nginx -t && sudo systemctl reload nginx

# Try to get certificate with retries
ATTEMPT=1
while [ $ATTEMPT -le $MAX_ATTEMPTS ]; do
    echo "========================================="
    echo "Attempt $ATTEMPT of $MAX_ATTEMPTS"
    echo "========================================="
    
    # Check DNS
    echo "Checking DNS propagation..."
    DNS_IP=$(dig +short $DOMAIN @1.1.1.1 | head -1)
    echo "  Cloudflare DNS (1.1.1.1): $DNS_IP"
    
    if [ "$DNS_IP" = "$TARGET_IP" ]; then
        echo "✓ DNS is correct!"
        
        # Wait a bit more for full propagation
        if [ $ATTEMPT -gt 1 ]; then
            echo "Waiting 30 seconds for DNS to fully propagate..."
            sleep 30
        fi
        
        # Try to get certificate
        echo ""
        echo "Attempting to obtain SSL certificate..."
        
        if sudo certbot --nginx -d $DOMAIN \
            --non-interactive \
            --agree-tos \
            --email admin@$DOMAIN \
            --redirect; then
            
            echo ""
            echo "========================================="
            echo "✓ HTTPS Configuration Complete!"
            echo "========================================="
            echo ""
            echo "Your application is now accessible at:"
            echo "  https://$DOMAIN"
            echo ""
            
            # Test HTTPS
            echo "Testing HTTPS access..."
            sleep 5
            curl -s https://$DOMAIN/health | python3 -m json.tool 2>/dev/null || curl -s https://$DOMAIN/health
            
            echo ""
            echo "Certificate info:"
            sudo certbot certificates
            
            exit 0
        else
            echo ""
            echo "Certificate installation failed."
            echo "This may be due to DNS propagation delays."
        fi
    else
        echo "⚠️  DNS not yet updated. Current: $DNS_IP, Expected: $TARGET_IP"
    fi
    
    if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
        echo ""
        echo "Waiting $WAIT_BETWEEN_ATTEMPTS seconds before next attempt..."
        echo "You can check DNS manually: dig +short $DOMAIN @1.1.1.1"
        sleep $WAIT_BETWEEN_ATTEMPTS
    fi
    
    ATTEMPT=$((ATTEMPT + 1))
done

echo ""
echo "========================================="
echo "HTTPS Configuration Failed"
echo "========================================="
echo ""
echo "After $MAX_ATTEMPTS attempts, DNS may still not be fully propagated."
echo ""
echo "Please verify:"
echo "1. Your DNS A record for $DOMAIN points to $TARGET_IP"
echo "2. DNS has propagated (check with: dig +short $DOMAIN @1.1.1.1)"
echo ""
echo "Once DNS is correct, run manually:"
echo "  sudo certbot --nginx -d $DOMAIN"
echo ""
exit 1

