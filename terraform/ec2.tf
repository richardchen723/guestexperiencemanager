# EC2 Instance for Hostaway Messages Application
# This deployment method uses EC2 with EBS volume for persistence

resource "aws_instance" "hostaway_messages" {
  ami           = data.aws_ami.ubuntu_22_04_arm64.id
  instance_type = var.ec2_instance_type

  # Use default VPC and subnet
  subnet_id = data.aws_subnets.default[0].ids[0]

  # Security group
  vpc_security_group_ids = [aws_security_group.ec2_hostaway.id]

  # Key pair (optional)
  key_name = var.ec2_key_pair_name != "" ? var.ec2_key_pair_name : null

  # Root volume configuration
  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
    tags = {
      Name = "${var.ec2_instance_name}-root"
    }
  }

  # User data script for initial setup
  user_data = <<-EOF
    #!/bin/bash
    # Initial setup script that runs on instance creation
    # Full deployment will be done via deployment scripts
    
    # Update system
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get upgrade -y
    
    # Install AWS CLI (for EBS volume management)
    apt-get install -y awscli
    
    # Install filesystem tools
    apt-get install -y xfsprogs e2fsprogs
    
    # Install git (needed for deployment)
    apt-get install -y git
    
    # Create application directory (will be replaced by EBS mount)
    mkdir -p /opt/hostaway-messages
    chown ubuntu:ubuntu /opt/hostaway-messages
    
    # Log user data execution
    echo "User data script completed at $(date)" >> /var/log/user-data.log
  EOF

  # Ensure instance is replaced if AMI changes
  lifecycle {
    create_before_destroy = false
  }

  tags = {
    Name = var.ec2_instance_name
  }
}



