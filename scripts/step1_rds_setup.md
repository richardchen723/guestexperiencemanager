# Step 1: AWS RDS PostgreSQL Setup

## Quick Checklist

Follow these steps in order:

### 1.1 Create RDS Instance

1. **Go to AWS RDS Console**
   - URL: https://console.aws.amazon.com/rds/
   - Sign in to your AWS account

2. **Click "Create database"**

3. **Configure Database:**
   - **Engine type**: PostgreSQL
   - **Version**: PostgreSQL 15.x or 16.x (recommended)
   - **Template**: 
     - For testing: "Free tier"
     - For production: "Production"
   - **DB instance identifier**: `hostaway-db` (or your choice)
   - **Master username**: `hostaway_admin` (or your choice)
   - **Master password**: 
     - Generate a strong password (save it securely!)
     - Minimum 8 characters, mix of letters, numbers, symbols
   - **DB instance class**: 
     - Free tier: `db.t3.micro` or `db.t4g.micro`
     - Production: `db.t3.small` or larger
   - **Storage**: 
     - Type: General Purpose SSD (gp3)
     - Allocated storage: 20 GB (minimum)
   - **Storage autoscaling**: Enable (recommended)

4. **Connectivity:**
   - **VPC**: Use default VPC (or create new)
   - **Subnet group**: Default
   - **Public access**: **YES** (required for Vercel)
   - **VPC security group**: Create new
     - Name: `hostaway-rds-sg`
   - **Availability Zone**: No preference
   - **Database port**: 5432 (default)

5. **Database authentication**: Password authentication

6. **Additional configuration**:
   - **Initial database name**: Leave empty (we'll create databases manually)
   - **DB parameter group**: Default
   - **Backup**: Enable automated backups (recommended)
   - **Backup retention**: 7 days (or your preference)
   - **Encryption**: Enable encryption (recommended)

7. **Click "Create database"**
   - Wait 5-10 minutes for instance to be available

### 1.2 Configure Security Group

1. **Go to EC2 Console** → Security Groups
   - URL: https://console.aws.amazon.com/ec2/v2/home#SecurityGroups

2. **Find your security group**: `hostaway-rds-sg` (or the one attached to your RDS instance)

3. **Click "Edit inbound rules"**

4. **Add rule:**
   - **Type**: PostgreSQL
   - **Protocol**: TCP
   - **Port**: 5432
   - **Source**: `0.0.0.0/0` (for now - we'll restrict later)
   - **Description**: "Allow Vercel and local connections"

5. **Save rules**

### 1.3 Get Connection Information

1. **Go back to RDS Console**
2. **Click on your database instance**
3. **Copy these values:**
   - **Endpoint**: e.g., `hostaway-db.xxxxx.us-east-1.rds.amazonaws.com`
   - **Port**: 5432
   - **Master username**: (the one you set)
   - **Master password**: (the one you set)

### 1.4 Create Databases

You need to connect to PostgreSQL and create 3 databases. You can use:

**Option A: AWS RDS Query Editor (Easiest)**
1. In RDS Console, click on your database
2. Click "Query Editor" tab
3. Connect using your master username and password
4. Run these commands:
   ```sql
   CREATE DATABASE hostaway_main;
   CREATE DATABASE hostaway_users;
   CREATE DATABASE hostaway_ai_cache;
   ```

**Option B: psql Command Line**
```bash
# Install PostgreSQL client if needed
# macOS: brew install postgresql
# Or use Docker: docker run -it postgres psql -h your-endpoint -U your-username -d postgres

psql -h your-endpoint -U your-username -d postgres
# Enter password when prompted
# Then run:
CREATE DATABASE hostaway_main;
CREATE DATABASE hostaway_users;
CREATE DATABASE hostaway_ai_cache;
\q
```

**Option C: pgAdmin or DBeaver**
- Use a GUI tool to connect and run the CREATE DATABASE commands

### 1.5 Create Connection Strings

Format: `postgresql://username:password@host:port/database`

Example:
```
postgresql://hostaway_admin:YourPassword123@hostaway-db.xxxxx.us-east-1.rds.amazonaws.com:5432/hostaway_main
```

Create 3 connection strings:
1. Main database: `postgresql://username:password@endpoint:5432/hostaway_main`
2. Users database: `postgresql://username:password@endpoint:5432/hostaway_users`
3. Cache database: `postgresql://username:password@endpoint:5432/hostaway_ai_cache`

**Save these connection strings - you'll need them for Step 3!**

## Next Steps

Once RDS is set up:
1. ✅ RDS instance created and available
2. ✅ Security group configured
3. ✅ 3 databases created
4. ✅ Connection strings ready

**Proceed to Step 2: S3 Bucket Setup**

