#!/bin/sh
set -eu

echo "Initializing the schema and optional demo data..."
python seed_db.py

echo "Running the initial conversation synchronization..."
if ! python fetch_and_update.py; then
    echo "Initial synchronization failed; Ofelia will retry on schedule."
fi

echo "Database ready. Ofelia will run the next scheduled synchronizations."

exec tail -f /dev/null
