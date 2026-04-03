#!/bin/sh
# Ensure data directory is writable by the truani user.
chown truani:truani /app/data 2>/dev/null
# Install/update dependencies (handles post-update requirement changes)
exec gosu truani sh -c 'pip install --quiet --user -r requirements.txt || echo "Warning: pip install failed"; python app.py'
