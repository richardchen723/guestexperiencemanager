# Elastic IP for Static Public IP Address

resource "aws_eip" "hostaway_messages" {
  count  = var.attach_elastic_ip ? 1 : 0
  domain = "vpc"

  tags = {
    Name = "${var.ec2_instance_name}-eip"
  }

  # Ensure Elastic IP is released if instance is destroyed
  lifecycle {
    create_before_destroy = true
  }
}

# Associate Elastic IP with EC2 instance
resource "aws_eip_association" "hostaway_messages" {
  count         = var.attach_elastic_ip ? 1 : 0
  instance_id   = aws_instance.hostaway_messages.id
  allocation_id = aws_eip.hostaway_messages[0].id
}








