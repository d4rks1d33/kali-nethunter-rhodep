#!/bin/bash
# Title: CONFIRMATION_DIALOG Example
# Author: Korben
# Description: Example usage of the CONFIRMATION_DIALOG DuckyScript command
# Version: 1.0

LOG "Launching confirmation dialog..."
resp=$(CONFIRMATION_DIALOG "Continue?")
case $? in
    $DUCKYSCRIPT_REJECTED)
        LOG "Dialog rejected"
        exit 1
        ;;
    $DUCKYSCRIPT_ERROR)
        LOG "An error occurred"
        exit 1
        ;;
esac

case "$resp" in
    $DUCKYSCRIPT_USER_CONFIRMED)
        LOG "User selected yes"
        ;;
    $DUCKYSCRIPT_USER_DENIED)
        LOG "User selected no"
        ;;
    *)
        LOG "Unknown response: $resp"
        ;;
esac