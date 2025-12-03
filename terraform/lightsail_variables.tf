# Lightsail-specific variables

variable "lightsail_instance_name" {
  description = "Name for the Lightsail instance"
  type        = string
  default     = "hostaway-messages"
}

variable "lightsail_availability_zone" {
  description = "Availability zone for the Lightsail instance"
  type        = string
  default     = "us-east-1a"
}

variable "lightsail_bundle_id" {
  description = "Lightsail bundle ID (instance size). Options: nano_3_0, micro_3_0, small_3_0, medium_3_0, large_3_0, xlarge_3_0, 2xlarge_3_0"
  type        = string
  default     = "micro_3_0" # 1GB RAM, 2 vCPU - restoring instance
  # Options:
  # nano_3_0: 0.5GB RAM, 2 vCPU (~$5/month)
  # micro_3_0: 1GB RAM, 2 vCPU (~$7/month)
  # small_3_0: 2GB RAM, 2 vCPU (~$12/month)
  # medium_3_0: 4GB RAM, 2 vCPU (~$24/month) - CURRENT
  # large_3_0: 8GB RAM, 2 vCPU (~$44/month)
  # xlarge_3_0: 16GB RAM, 4 vCPU (~$84/month)
}

variable "attach_static_ip" {
  description = "Whether to attach a static IP to the Lightsail instance"
  type        = bool
  default     = true
}

