#!/usr/bin/env bash

args=$*

if [[ "$args" == "" ]]; then
    echo "Please provide command to execute"
    exit 1
fi

echo "Executing $*"

function clearLastLine() {
        tput cuu 1 && tput el
}

true > exec-till-error.log
attempt=1

while true; do
    echo "Attempt #$attempt ..."
    if ! $args >> exec-till-error.log; then
        echo "Process exists with error. Done."
        exit 0;
    fi
    clearLastLine
    ((attempt=attempt+1))
done
