#!/bin/bash
# Title: NUMBER_PICKER Example
# Author: Korben
# Description: Example usage of the NUMBER_PICKER DuckyScript command
# Version: 1.0

LOG "Launching number picker..."
resp=$(NUMBER_PICKER "Enter a number" 1337)
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