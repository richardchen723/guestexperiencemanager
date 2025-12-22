# EC2-specific variables

variable "ec2_instance_type" {
  description = "EC2 instance type. Default is t4g.medium (4GB RAM, 2 vCPU, ARM-based)"
  type        = string
  default     = "t4g.medium"
  # Options:
  # t4g.micro: 1GB RAM, 2 vCPU (~$0.0084/hour = ~$6.13/month)
  # t4g.small: 2GB RAM, 2 vCPU (~$0.0168/hour = ~$12.26/month)
  # t4g.medium: 4GB RAM, 2 vCPU (~$0.0336/hour = ~$24.19/month) - CURRENT
  # t4g.large: 8GB RAM, 2 vCPU (~$0.0672/hour = ~$49.06/month)
}

variable "ebs_volume_size" {
  description = "Size of the EBS volume in GB for persistent storage"
  type        = number
  default     = 50
}

variable "ebs_volume_type" {
  description = "EBS volume type (gp3 recommended for cost and performance)"
  type        = string
  default     = "gp3"
  # Options:
  # gp3: General Purpose SSD (recommended, ~$0.10/GB-month)
  # gp2: General Purpose SSD (legacy, ~$0.10/GB-month)
  # io1: Provisioned IOPS SSD (expensive, for high IOPS needs)
}

variable "ec2_key_pair_name" {
  description = "Name of the EC2 Key Pair for SSH access (optional, leave empty to use AWS Systems Manager Session Manager)"
  type        = string
  default     = ""
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to access EC2 via SSH (port 22). Leave empty to allow from anywhere (0.0.0.0/0). For security, restrict to your IP."
  type        = string
  default     = "0.0.0.0/0"
  # Example: "203.0.113.0/24" for a specific network
  # Example: "203.0.113.1/32" for a specific IP
}

variable "ec2_instance_name" {
  description = "Name tag for the EC2 instance"
  type        = string
  default     = "hostaway-messages"
}

variable "attach_elastic_ip" {
  description = "Whether to allocate and attach an Elastic IP to the EC2 instance"
  type        = bool
  default     = true
}








