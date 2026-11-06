#!/bin/sh
set -e
# Le scheduler partage la base : seul le service web applique les migrations.
if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  flask db upgrade
fi
exec "$@"
