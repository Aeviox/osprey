#!/usr/bin/env bash

# ensure the CWD is the directory of this script
cd "$(dirname "$0")"

# update from git
echo "Updating From Git..."
git pull

# kill any remaining python processes
sudo pkill python
sudo pkill python3

# run python file that starts the server
python3 ../app/run_server.py