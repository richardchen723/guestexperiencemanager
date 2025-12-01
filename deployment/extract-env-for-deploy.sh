#!/bin/bash
# Safely extract environment variables from .env file for deployment
# This script reads .env and prepares values for deployment without exposing them

set -e

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found"
    exit 1
fi

echo "Extracting environment variables from .env file..."
echo ""

# Read .env file and extract values
while IFS='=' read -r key value || [ -n "$key" ]; do
    # Skip comments and empty lines
    [[ "$key" =~ ^#.*$ ]] && continue
    [[ -z "$key" ]] && continue
    
    # Remove quotes from value
    value=$(echo "$value" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    
    # Export variable
    export "$key=$value"
done < "$ENV_FILE"

echo "Environment variables extracted successfully."
echo ""
echo "Available variables:"
echo "- DATABASE_URL: ${DATABASE_URL:+SET}"
echo "- SECRET_KEY: ${SECRET_KEY:+SET}"
echo "- OPENAI_API_KEY: ${OPENAI_API_KEY:+SET}"
echo "- HOSTAWAY_ACCOUNT_ID: ${HOSTAWAY_ACCOUNT_ID:+SET}"
echo "- HOSTAWAY_API_KEY: ${HOSTAWAY_API_KEY:+SET}"
echo "- GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID:+SET}"
echo "- GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET:+SET}"
echo ""

