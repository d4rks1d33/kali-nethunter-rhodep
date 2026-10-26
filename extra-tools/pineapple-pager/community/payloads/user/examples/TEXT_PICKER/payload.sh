#!/bin/bash
# Title: TEXT_PICKER Example
# Author: Korben
# Description: Example usage of the TEXT_PICKER DuckyScript command
# Version: 1.0

LOG "Launching text picker..."
resp=$(TEXT_PICKER "Favorite Password?" "pineapplesareyummy")
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
