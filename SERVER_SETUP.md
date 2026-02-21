# Office Hours — Relay Server Setup

Deploy the relay server so two Office Hours clients on different networks can connect via room codes.

## What You Need

- A VPS with a public IP (DigitalOcean, Vultr, AWS Lightsail, etc.)
- A domain name (optional but recommended — e.g., `officehours.app`)
- The file `relay_server.py` from this folder

## Step 1: Create a VPS

1. Go to [digitalocean.com](https://digitalocean.com) and create an account
2. Click **Create → Droplets**
3. Choose **Ubuntu 24.04 LTS**
4. Select the **$4/mo** plan (Basic, Regular, 512MB RAM — plenty for this)
5. Pick a region closest to your users (e.g., **San Francisco**)
6. Under Authentication, choose **SSH Key** (more secure) or **Password**
7. Click **Create Droplet**
8. Note your droplet's **public IP address** (e.g., `143.198.45.67`)

## Step 2: Upload the Relay Script

From your Mac terminal:

```bash
scp /path/to/OfficeHours/relay_server.py root@YOUR_SERVER_IP:~/
```

## Step 3: SSH In and Configure

```bash
ssh root@YOUR_SERVER_IP
```

### Verify Python is installed:
```bash
python3 --version
# Should show Python 3.10+ (pre-installed on Ubuntu 24.04)
```

### Open the firewall port:
```bash
ufw allow 50002
```

### Test it manually first:
```bash
python3 relay_server.py --port 50002
# You should see: [Server] Office Hours Relay listening on 0.0.0.0:50002
# Press Ctrl+C to stop
```

## Step 4: Run as a Background Service

Create a systemd service so the relay starts automatically and survives reboots:

```bash
cat > /etc/systemd/system/officehours-relay.service << 'EOF'
[Unit]
Description=Office Hours Relay Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/usr/bin/python3 /root/relay_server.py --port 50002
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start it:
```bash
systemctl daemon-reload
systemctl enable officehours-relay
systemctl start officehours-relay
```

Verify it's running:
```bash
systemctl status officehours-relay
```

View logs:
```bash
journalctl -u officehours-relay -f
```

## Step 5: Point Your Domain (Optional)

1. Buy a domain from [Namecheap](https://namecheap.com), [Google Domains](https://domains.google), etc.
2. In your domain registrar's DNS settings, add an **A record**:
   - **Host**: `@` (or `relay` for a subdomain like `relay.yourdomain.com`)
   - **Value**: Your server's IP address (e.g., `143.198.45.67`)
   - **TTL**: 300
3. Wait 5-10 minutes for DNS propagation

## Step 6: Test the Connection

On **Client A** (any Mac with the Office Hours app):
1. Open the app → go to the **🌐 Remote** tab
2. Enter your server IP or domain in the **Server** field
3. Click **Create Room** → note the room code (e.g., `OH-7X3K`)

On **Client B** (a different network):
1. Open the app → go to the **🌐 Remote** tab
2. Enter the same server address
3. Enter the room code → click **Join Room**

Both clients should show "Connected via relay" and you can talk!

## Useful Commands

| Command | What it does |
|---------|-------------|
| `systemctl start officehours-relay` | Start the relay |
| `systemctl stop officehours-relay` | Stop the relay |
| `systemctl restart officehours-relay` | Restart after updating `relay_server.py` |
| `journalctl -u officehours-relay -f` | Watch live logs |
| `journalctl -u officehours-relay --since "1 hour ago"` | Recent logs |

## Updating the Relay

When you update `relay_server.py`:
```bash
scp /path/to/OfficeHours/relay_server.py root@YOUR_SERVER_IP:~/
ssh root@YOUR_SERVER_IP "systemctl restart officehours-relay"
```

## Network Requirements

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 50002 | TCP | Inbound | Control messages, file transfer |
| 50002 | UDP | Inbound | Audio relay |
| 22 | TCP | Inbound | SSH (your admin access) |
