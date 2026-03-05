# Office Hours — Terminal Cheat Sheet

All commands assume you're in `~/office-hours`. If not, run `cd ~/office-hours` first.

## Day-to-Day

```bash
# Launch the app (no git pull, runs local code)
./start.command

# Launch without the script (same thing)
source venv/bin/activate && python3 main.py
```

## Push Changes to GitHub

```bash
# Option 1: Double-click push.command in Finder

# Option 2: From terminal
git add -A && git commit -m "describe what changed" && git push origin main
```

## Update Relay Server (DigitalOcean)

```bash
# One-liner: download latest from GitHub, restart process
ssh root@165.22.175.71 "cd /root && curl -sL https://raw.githubusercontent.com/matthannam-fart/office-hours/main/relay_server.py -o relay_server.py && pkill -f relay_server.py; nohup python3 relay_server.py > relay.log 2>&1 &"
```

```bash
# Or step by step:
ssh root@165.22.175.71
cd /root
curl -sL https://raw.githubusercontent.com/matthannam-fart/office-hours/main/relay_server.py -o relay_server.py
pkill -f relay_server.py
nohup python3 relay_server.py > relay.log 2>&1 &
exit
```

## Check Relay Server

```bash
# Is it running?
ssh root@165.22.175.71 "pgrep -a relay"

# View recent logs
ssh root@165.22.175.71 "tail -50 /root/relay.log"
```

## Update Another Mac

Just run `install_and_run.command` on that Mac — it auto-pulls from GitHub.

## Git Troubleshooting

```bash
# See what's changed locally
git status

# See recent commits
git log --oneline -5

# Discard all local changes (careful!)
git checkout .

# Force pull from GitHub (overwrites local)
git fetch origin && git reset --hard origin/main
```
