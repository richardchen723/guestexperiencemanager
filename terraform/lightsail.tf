# Lightsail Instance for Hostaway Messages Application
# This is the primary deployment method - uses local PostgreSQL and filesystem

resource "aws_lightsail_instance" "hostaway_messages" {
  name              = var.lightsail_instance_name
  availability_zone = var.lightsail_availability_zone
  blueprint_id      = "ubuntu_22_04"
  bundle_id         = var.lightsail_bundle_id

  user_data = <<-EOF
    #!/bin/bash
    # Initial setup script that runs on instance creation
    # Full deployment will be done via deployment scripts
    
    # Update system
    apt-get update
    apt-get upgrade -y
    
    # Install git (needed for deployment)
    apt-get install -y git
    
    # Create application directory
    mkdir -p /opt/hostaway-messages
    chown ubuntu:ubuntu /opt/hostaway-messages
  EOF

  tags = {
    Name        = var.lightsail_instance_name
    Environment = var.environment
    Project     = "hostaway-messages"
  }
}

# Static IP for the Lightsail instance
resource "aws_lightsail_static_ip" "hostaway_messages" {
  count = var.attach_static_ip ? 1 : 0
  name  = "${var.lightsail_instance_name}-static-ip"
}

resource "aws_lightsail_static_ip_attachment" "hostaway_messages" {
  count           = var.attach_static_ip ? 1 : 0
  static_ip_name  = aws_lightsail_static_ip.hostaway_messages[0].id
  instance_name   = aws_lightsail_instance.hostaway_messages.id
}

