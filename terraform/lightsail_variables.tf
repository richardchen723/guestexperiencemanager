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
  description = "Lightsail bundle ID (instance size). Options: nano_2_0, micro_2_0, small_2_0, medium_2_0, large_2_0, xlarge_2_0, 2xlarge_2_0"
  type        = string
  default     = "micro_2_0"  # 1GB RAM, 1 vCPU - start with this, can upgrade later
  # Options:
  # nano_2_0: 0.5GB RAM, 1 vCPU (~$3.50/month)
  # micro_2_0: 1GB RAM, 1 vCPU (~$5/month)
  # small_2_0: 2GB RAM, 1 vCPU (~$10/month) - RECOMMENDED
  # medium_2_0: 4GB RAM, 2 vCPU (~$20/month)
  # large_2_0: 8GB RAM, 2 vCPU (~$40/month)
}

variable "attach_static_ip" {
  description = "Whether to attach a static IP to the Lightsail instance"
  type        = bool
  default     = true
}

