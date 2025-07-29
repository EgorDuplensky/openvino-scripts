#!/usr/bin/env bash

# Check if any command-line arguments were provided
if [ "$#" -gt 0 ]; then
    # Arguments were provided, treat the first argument as a file
    input_file="$1"
else
    # No arguments were provided, check if stdin is connected to a terminal
    if [ -t 0 ]; then
        echo "Please provide either a file as a command-line argument or input via pipe."
        exit 1
    fi
    # Read from stdin
    input_file="/dev/stdin"
fi

awk -F ',' -v OFS=, 'NF{NF-=1};1' < "$input_file"
