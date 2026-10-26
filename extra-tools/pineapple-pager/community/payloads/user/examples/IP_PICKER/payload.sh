#!/bin/bash
# Title: IP_PICKER Example
# Author: Korben
# Description: Example usage of the IP_PICKER DuckyScript command
# Version: 1.0

LOG "Launching IP picker..."
resp=$(IP_PICKER "Home?" "127.0.0.1")
case $? in
    $DUCKYSCRIPT_CANCELLED)
        LOG "User cancelled"
        exit 1
        ;;
    $DUCKYSCRIPT_REJECTED)
        LOG "Dialog rejected"
        exit 1
        ;;
    $DUCKYSCRIPT_ERROR)
        LOG "An error occurred"
        exit 1
        ;;
esac

LOG "User Picked $resp"

