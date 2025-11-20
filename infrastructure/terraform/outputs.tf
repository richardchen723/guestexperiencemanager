# Outputs are defined in main.tf
# This file is kept for organization and can be used for additional outputs

output "setup_instructions" {
  description = "Instructions for setting up environment variables"
  sensitive   = true
  value = <<-EOT
    
    ============================================================
    Infrastructure Setup Complete!
    ============================================================
    
    Add these to your .env file:
    
    # Database URLs
    DATABASE_URL=postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_main
    USERS_DATABASE_URL=postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_users
    CACHE_DATABASE_URL=postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.endpoint}:${aws_db_instance.main.port}/hostaway_ai_cache
    
    # AWS S3
    AWS_ACCESS_KEY_ID=${aws_iam_access_key.s3_user.id}
    AWS_SECRET_ACCESS_KEY=${aws_iam_access_key.s3_user.secret}
    AWS_S3_BUCKET_NAME=${aws_s3_bucket.conversations.id}
    AWS_S3_REGION=${aws_s3_bucket.conversations.region}
    CONVERSATIONS_S3_PREFIX=conversations/
    
    ============================================================
    
    Run: terraform output -json connection_info > connection_info.json
    to get all connection details in JSON format.
    
  EOT
}

