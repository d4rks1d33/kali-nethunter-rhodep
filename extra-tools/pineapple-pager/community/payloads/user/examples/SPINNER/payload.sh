#!/bin/bash
# Title: SPINNER Example
# Author: Korben
# Description: Example usage of the SPINNER DuckyScript commands
# Version: 1.0

LOG "Starting spinner..."
id=$(START_SPINNER "Thinking")
LOG "Doing work"
sleep 5
STOP_SPINNER $id
LOG "Done!"
