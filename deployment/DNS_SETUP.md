# DNS Setup for HTTPS

## Current Status

- **Domain**: yourcottoncandy.com
- **Current DNS**: Points to 50.87.197.86
- **Required DNS**: Should point to 44.196.144.24 (Lightsail static IP)

## Steps to Update DNS

1. **Log in to your domain registrar** (where you purchased yourcottoncandy.com)

2. **Find DNS Management**:
   - Look for "DNS Management", "DNS Settings", or "Name Servers"
   - This might be in your domain registrar's control panel

3. **Update the A Record**:
   - Find the A record for `yourcottoncandy.com` (or `@`)
   - Change the IP address from `50.87.197.86` to `44.196.144.24`
   - Save the changes

4. **Wait for DNS Propagation**:
   - DNS changes can take 5-30 minutes to propagate
   - You can check with: `dig +short yourcottoncandy.com`
   - It should return `44.196.144.24` when ready

5. **Verify DNS**:
   ```bash
   dig +short yourcottoncandy.com
   # Should return: 44.196.144.24
   ```

## After DNS is Updated

Once the DNS record is updated and propagated, run:

```bash
ssh -i LightsailDefaultKey-us-east-1.pem ubuntu@44.196.144.24
sudo certbot --nginx -d yourcottoncandy.com
```

Or use the automated script:
```bash
ssh -i LightsailDefaultKey-us-east-1.pem ubuntu@44.196.144.24
cd /opt/hostaway-messages/app
sudo ./deployment/setup-https.sh yourcottoncandy.com
```

## Common Domain Registrars

- **GoDaddy**: My Products → DNS → Records → Edit A record
- **Namecheap**: Domain List → Manage → Advanced DNS → A Record
- **Google Domains**: DNS → Custom records → Edit A record
- **Cloudflare**: DNS → Records → Edit A record
- **Route 53**: Hosted zones → yourcottoncandy.com → Edit A record

## Troubleshooting

If DNS doesn't update:
1. Clear your local DNS cache: `sudo dscacheutil -flushcache` (macOS)
2. Check TTL (Time To Live) - some DNS providers have long TTLs
3. Verify the A record is for the root domain (`@` or `yourcottoncandy.com`), not a subdomain

