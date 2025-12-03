# DEPRECATED: Lightsail resources are no longer used.
# The application now uses EC2 with EBS volume for persistence.
# Commented out to allow terraform to destroy existing Lightsail resources.

# resource "aws_lightsail_instance" "hostaway_messages" {
#   name              = var.lightsail_instance_name
#   availability_zone = var.lightsail_availability_zone
#   blueprint_id      = "ubuntu_22_04"
#   bundle_id         = var.lightsail_bundle_id

#   user_data = <<-EOF
#     #!/bin/bash
#     # Initial setup script that runs on instance creation
#     # Full deployment will be done via deployment scripts
#     
#     # Update system
#     apt-get update
#     apt-get upgrade -y
#     
#     # Install git (needed for deployment)
#     apt-get install -y git
#     
#     # Create application directory
#     mkdir -p /opt/hostaway-messages
#     chown ubuntu:ubuntu /opt/hostaway-messages
#   EOF

#   tags = {
#     Name        = var.lightsail_instance_name
#     Environment = var.environment
#     Project     = "hostaway-messages"
#   }

#   # Ensure static IP is properly handled during instance recreation
#   lifecycle {
#     create_before_destroy = false
#   }
# }

# # Static IP for the Lightsail instance
# resource "aws_lightsail_static_ip" "hostaway_messages" {
#   count = var.attach_static_ip ? 1 : 0
#   name  = "${var.lightsail_instance_name}-static-ip"
# }

# resource "aws_lightsail_static_ip_attachment" "hostaway_messages" {
#   count          = var.attach_static_ip ? 1 : 0
#   static_ip_name = aws_lightsail_static_ip.hostaway_messages[0].name
#   instance_name  = aws_lightsail_instance.hostaway_messages.name

#   # Ensure static IP is detached before instance is destroyed and reattached after creation
#   lifecycle {
#     create_before_destroy = false
#   }
# }

