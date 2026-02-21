#!/bin/bash

# Navigate to the folder this script lives in
cd "$(dirname "$0")"

echo ""
echo "What changed? (press Enter for auto timestamp)"
read -r MSG

if [ -z "$MSG" ]; then
  MSG="Update $(date '+%Y-%m-%d %H:%M')"
fi

git add .
git commit -m "$MSG"
git push

echo ""
echo "Done! ohinter.com will now serve the latest version."
echo ""
read -p "Press Enter to close..."
