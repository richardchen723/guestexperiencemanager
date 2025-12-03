# AWS Configuration
aws_region  = "us-east-1"
environment = "production"

# EC2 Configuration
# Note: Key pair name in AWS (not the .pem filename)
# If your key pair is "ychen-key.pem", the key pair name is likely "ychen-key"
ec2_key_pair_name = "ychen-key"

# RDS Configuration (deprecated - not used with EC2/Lightsail)
db_instance_class              = "db.t4g.micro"
db_allocated_storage           = 20
db_max_allocated_storage       = 100
db_multi_az                   = false
db_backup_retention            = 1
db_auto_minor_version_upgrade = true
db_performance_insights        = false
db_engine_version              = "15"
db_name                        = "hostaway"
db_username                    = "hostaway_admin"

# S3 Configuration (deprecated - not used with EC2/Lightsail)
s3_bucket_name          = ""
s3_lifecycle_enabled    = true
s3_intelligent_tiering  = true

