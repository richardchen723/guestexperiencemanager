#!/bin/bash
# Script to create databases in RDS PostgreSQL
# This is a fallback if the Terraform provisioner fails

set -e

if [ -z "$1" ] || [ -z "$2" ] || [ -z "$3" ]; then
    echo "Usage: $0 <rds-endpoint> <username> <password>"
    echo "Example: $0 hostaway-db.xxxxx.us-east-1.rds.amazonaws.com hostaway_admin mypassword"
    exit 1
fi

RDS_ENDPOINT=$1
DB_USERNAME=$2
DB_PASSWORD=$3

echo "Creating databases in RDS instance: $RDS_ENDPOINT"

export PGPASSWORD=$DB_PASSWORD

psql -h $RDS_ENDPOINT -U $DB_USERNAME -d postgres <<EOF
CREATE DATABASE hostaway_main;
CREATE DATABASE hostaway_users;
CREATE DATABASE hostaway_ai_cache;
\q
EOF

echo "✓ Databases created successfully!"

