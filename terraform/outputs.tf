# EC2 Outputs
output "ec2_instance_public_ip" {
  description = "Public IP address of the EC2 instance"
  value       = try(aws_instance.hostaway_messages.public_ip, null)
}

output "ec2_instance_private_ip" {
  description = "Private IP address of the EC2 instance"
  value       = try(aws_instance.hostaway_messages.private_ip, null)
}

output "ec2_elastic_ip" {
  description = "Elastic IP address (if attached)"
  value       = var.attach_elastic_ip ? try(aws_eip.hostaway_messages[0].public_ip, null) : null
}

output "ec2_ebs_volume_id" {
  description = "EBS volume ID for persistent storage"
  value       = try(aws_ebs_volume.hostaway_data.id, null)
}

output "ec2_ssh_command" {
  description = "SSH command to connect to the EC2 instance"
  value       = var.attach_elastic_ip ? "ssh ubuntu@${try(aws_eip.hostaway_messages[0].public_ip, "EIP")}" : "ssh ubuntu@${try(aws_instance.hostaway_messages.public_ip, "INSTANCE_IP")}"
}

# Lightsail Outputs (deprecated - commented out)
# output "lightsail_instance_public_ip" {
#   description = "Public IP address of the Lightsail instance"
#   value       = try(aws_lightsail_instance.hostaway_messages.public_ip_address, null)
# }

# output "lightsail_instance_private_ip" {
#   description = "Private IP address of the Lightsail instance"
#   value       = try(aws_lightsail_instance.hostaway_messages.private_ip_address, null)
# }

# output "lightsail_static_ip" {
#   description = "Static IP address (if attached)"
#   value       = var.attach_static_ip ? try(aws_lightsail_static_ip.hostaway_messages[0].ip_address, null) : null
# }

# output "lightsail_ssh_command" {
#   description = "SSH command to connect to the instance"
#   value       = "ssh ubuntu@${try(aws_lightsail_instance.hostaway_messages.public_ip_address, "INSTANCE_IP")}"
# }

# Legacy RDS Outputs (deprecated - commented out)
# output "rds_endpoint" {
#   value       = try(aws_db_instance.main.endpoint, null)
#   description = "RDS instance endpoint (deprecated - not used with EC2)"
# }

# output "rds_port" {
#   value       = aws_db_instance.main.port
#   description = "RDS instance port"
# }

# output "database_name" {
#   value       = aws_db_instance.main.db_name
#   description = "Database name"
# }

# output "database_username" {
#   value       = aws_db_instance.main.username
#   description = "Database master username"
#   sensitive   = true
# }

# output "database_secret_arn" {
#   value       = aws_secretsmanager_secret.db_credentials.arn
#   description = "ARN of the secret containing database credentials"
# }

# S3 Outputs (deprecated - commented out)
# output "s3_bucket_name" {
#   value       = aws_s3_bucket.main.id
#   description = "S3 bucket name for file storage"
# }

# output "s3_bucket_arn" {
#   value       = aws_s3_bucket.main.arn
#   description = "S3 bucket ARN"
# }

# output "s3_bucket_region" {
#   value       = aws_s3_bucket.main.region
#   description = "S3 bucket region"
# }

# Cost estimation (updated for EC2 deployment)
output "estimated_monthly_cost" {
  value = {
    ec2 = {
      instance = "~$24.19/month (t4g.medium, 4GB RAM, 2 vCPU, ARM-based)"
      ebs      = "~$5/month (50GB gp3)"
      eip      = "FREE (when attached to running instance)"
      total    = "~$29-35/month (depending on data transfer)"
    }
    note = "Consider Reserved Instances for ~40% savings (~$15/month for compute)"
  }
  description = "Estimated monthly AWS costs for EC2 deployment"
}


