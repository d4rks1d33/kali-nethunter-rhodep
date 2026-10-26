# Title:                NMap Subnet
# Description:          Dumps NMap scan data of selected subnet to both storage and log
# Author:               cococode
# Version:              1.0

# Options
LOOTDIR=/root/loot/nmapTarget

# Get and format connected subnets from network interfaces
subnets=$(ip -o -f inet addr show | awk '/scope global/ {print $4}')
subnetArray=($subnets)
subnetPrompt=$(echo "$subnets" | awk '{print NR,$0}')

# Prompt user to select a subnet to target
PROMPT "Target subnets:\n\n$subnetPrompt"
targetIndex=$(NUMBER_PICKER "Enter index of target" "1")
targetSubnet=${subnetArray[$targetIndex-1]}

# Create loot destination if needed
mkdir -p $LOOTDIR
lootfile=$LOOTDIR/$(date -Is)

LOG "Running nmap scan on $targetSubnet..."
LOG "Results will be saved to: $lootfile\n"

# Run payload command, stream output to log
nmap -Pn -sS -F -oA $lootfile $targetSubnet | tr '\n' '\0' | xargs -0 -n 1 LOG
