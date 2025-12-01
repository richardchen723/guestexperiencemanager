# Lightsail Outputs
output "lightsail_instance_public_ip" {
  description = "Public IP address of the Lightsail instance"
  value       = try(aws_lightsail_instance.hostaway_messages.public_ip_address, null)
}

output "lightsail_instance_private_ip" {
  description = "Private IP address of the Lightsail instance"
  value       = try(aws_lightsail_instance.hostaway_messages.private_ip_address, null)
}

output "lightsail_static_ip" {
  description = "Static IP address (if attached)"
  value       = var.attach_static_ip ? try(aws_lightsail_static_ip.hostaway_messages[0].ip_address, null) : null
}

output "lightsail_ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh ubuntu@${try(aws_lightsail_instance.hostaway_messages.public_ip_address, "INSTANCE_IP")}"
}

# Legacy RDS Outputs (deprecated - for reference only)
output "rds_endpoint" {
  value       = try(aws_db_instance.main.endpoint, null)
  description = "RDS instance endpoint (deprecated - not used with Lightsail)"
}

output "rds_port" {
  value       = aws_db_instance.main.port
  description = "RDS instance port"
}

output "database_name" {
  value       = aws_db_instance.main.db_name
  description = "Database name"
}

output "database_username" {
  value       = aws_db_instance.main.username
  description = "Database master username"
  sensitive   = true
}

output "database_secret_arn" {
  value       = aws_secretsmanager_secret.db_credentials.arn
  description = "ARN of the secret containing database credentials"
}

output "s3_bucket_name" {
  value       = aws_s3_bucket.main.id
  description = "S3 bucket name for file storage"
}

output "s3_bucket_arn" {
  value       = aws_s3_bucket.main.arn
  description = "S3 bucket ARN"
}

output "s3_bucket_region" {
  value       = aws_s3_bucket.main.region
  description = "S3 bucket region"
}

# Cost estimation (approximate)
output "estimated_monthly_cost" {
  value = {
    rds = {
      instance = "FREE for 12 months, then ~$15/month (db.t4g.micro, single-AZ, Free Tier compatible). Upgrade to db.t4g.medium (~$50/month) after upgrading AWS account for better performance."
      storage  = "~$2/month (20GB gp3)"
      backups  = "~$0.50/month (1 day retention, increase after free tier expires)"
      total    = "~$53/month"
    }
    s3 = {
      storage_standard    = "~$0.023/GB/month"
      storage_ia          = "~$0.0125/GB/month (after 90 days)"
      storage_glacier     = "~$0.004/GB/month (after 180 days)"
      intelligent_tiering = "~$0.0025/1000 objects/month"
      requests            = "~$0.005/1000 requests"
      note                = "Costs vary based on actual usage"
    }
    total_estimate = "~$53-60/month (RDS + minimal S3 usage)"
  }
  description = "Estimated monthly AWS costs"
}


