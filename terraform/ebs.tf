# EBS Volume for Persistent Storage

resource "aws_ebs_volume" "hostaway_data" {
  availability_zone = aws_instance.hostaway_messages.availability_zone
  size              = var.ebs_volume_size
  type              = var.ebs_volume_type
  encrypted         = true

  tags = {
    Name = "${var.ec2_instance_name}-data"
  }
}

# Attach EBS volume to EC2 instance
resource "aws_volume_attachment" "hostaway_data" {
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.hostaway_data.id
  instance_id = aws_instance.hostaway_messages.id

  # Prevent volume from being deleted when instance is terminated
  skip_destroy = false
}





