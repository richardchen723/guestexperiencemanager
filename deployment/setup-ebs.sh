#!/bin/bash
# EBS Volume Setup Script for Hostaway Messages
# This script formats, mounts, and configures the EBS volume for persistent storage
# Run this script as root: sudo ./deployment/setup-ebs.sh

set -e  # Exit on error

echo "========================================="
echo "EBS Volume Setup for Hostaway Messages"
echo "========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Please run as root (use sudo)${NC}"
    exit 1
fi

# EBS volume device (attached as /dev/sdf, may appear as /dev/nvme1n1 on newer instances)
EBS_DEVICE=""
MOUNT_POINT="/opt/hostaway-messages"

# Detect EBS volume device
echo -e "${GREEN}Step 1: Detecting EBS volume...${NC}"

# Check for NVMe device (newer instances)
if [ -b /dev/nvme1n1 ]; then
    EBS_DEVICE="/dev/nvme1n1"
    echo -e "${GREEN}Found EBS volume at /dev/nvme1n1${NC}"
# Check for /dev/sdf (older instances)
elif [ -b /dev/sdf ]; then
    EBS_DEVICE="/dev/sdf"
    echo -e "${GREEN}Found EBS volume at /dev/sdf${NC}"
else
    echo -e "${RED}Error: EBS volume not found. Expected /dev/nvme1n1 or /dev/sdf${NC}"
    echo "Available block devices:"
    lsblk
    exit 1
fi

# Check if device is already mounted
if mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
    echo -e "${YELLOW}EBS volume is already mounted at $MOUNT_POINT${NC}"
    echo "Skipping mount step."
    exit 0
fi

# Check if device has a filesystem
echo -e "${GREEN}Step 2: Checking filesystem...${NC}"
if blkid "$EBS_DEVICE" > /dev/null 2>&1; then
    echo -e "${YELLOW}Filesystem already exists on $EBS_DEVICE${NC}"
    FILESYSTEM_TYPE=$(blkid -s TYPE -o value "$EBS_DEVICE")
    echo "Filesystem type: $FILESYSTEM_TYPE"
else
    echo -e "${GREEN}Step 3: Creating ext4 filesystem on $EBS_DEVICE...${NC}"
    echo -e "${YELLOW}WARNING: This will format the device. All data will be lost!${NC}"
    read -p "Continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
    
    mkfs.ext4 -F "$EBS_DEVICE"
    echo -e "${GREEN}Filesystem created${NC}"
fi

# Create mount point
echo -e "${GREEN}Step 4: Creating mount point...${NC}"
if [ -d "$MOUNT_POINT" ]; then
    # If directory exists but is not a mount point, backup and recreate
    if ! mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
        echo -e "${YELLOW}Directory $MOUNT_POINT exists but is not a mount point${NC}"
        echo "Moving existing directory to ${MOUNT_POINT}.backup"
        mv "$MOUNT_POINT" "${MOUNT_POINT}.backup"
    fi
fi

mkdir -p "$MOUNT_POINT"

# Mount the volume
echo -e "${GREEN}Step 5: Mounting EBS volume...${NC}"
mount "$EBS_DEVICE" "$MOUNT_POINT"
echo -e "${GREEN}Volume mounted successfully${NC}"

# Add to /etc/fstab for automatic mounting on reboot
echo -e "${GREEN}Step 6: Configuring automatic mount on reboot...${NC}"

# Get UUID of the device
UUID=$(blkid -s UUID -o value "$EBS_DEVICE")

# Check if already in fstab
if grep -q "$UUID" /etc/fstab; then
    echo -e "${YELLOW}Entry already exists in /etc/fstab${NC}"
else
    # Add entry to fstab
    echo "# EBS volume for Hostaway Messages (added by setup-ebs.sh)" >> /etc/fstab
    echo "UUID=$UUID $MOUNT_POINT ext4 defaults,nofail 0 2" >> /etc/fstab
    echo -e "${GREEN}Added to /etc/fstab${NC}"
fi

# Create directory structure
echo -e "${GREEN}Step 7: Creating directory structure...${NC}"
mkdir -p "$MOUNT_POINT"/{app,venv,conversations,logs,backups,postgresql}

# Set ownership (create hostaway user if it doesn't exist)
if ! id "hostaway" &>/dev/null; then
    echo -e "${YELLOW}User 'hostaway' does not exist. Creating...${NC}"
    useradd -m -s /bin/bash hostaway
fi

chown -R hostaway:hostaway "$MOUNT_POINT"
echo -e "${GREEN}Directory structure created and permissions set${NC}"

# Verify mount
echo -e "${GREEN}Step 8: Verifying mount...${NC}"
if mountpoint -q "$MOUNT_POINT"; then
    echo -e "${GREEN}Mount verified successfully${NC}"
    df -h "$MOUNT_POINT"
else
    echo -e "${RED}Error: Mount verification failed${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}=========================================${NC}"
echo -e "${GREEN}EBS volume setup completed successfully!${NC}"
echo -e "${GREEN}=========================================${NC}"
echo ""
echo "EBS volume details:"
echo "  Device: $EBS_DEVICE"
echo "  Mount point: $MOUNT_POINT"
echo "  UUID: $UUID"
echo "  Filesystem: ext4"
echo ""
echo "Next steps:"
echo "1. Run: sudo ./deployment/ec2-setup.sh (if not already done)"
echo "2. Run: sudo -u postgres ./deployment/setup-postgres.sh"
echo "3. Clone your repository to $MOUNT_POINT/app"
echo "4. Run: sudo ./deployment/setup-env.sh"
echo "5. Run: sudo ./deployment/deploy.sh"
echo ""



