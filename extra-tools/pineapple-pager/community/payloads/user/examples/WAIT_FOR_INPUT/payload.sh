#!/bin/bash
# Title: WAIT_FOR_INPUT Example
# Author: Korben
# Description: Example usage of the WAIT_FOR_INPUT DuckyScript command
# Version: 1.0

LOG "Press any button!"
resp=$(WAIT_FOR_INPUT)
LOG "User pressed: $resp"