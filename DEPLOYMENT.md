# Deployment Guide: El Al Rescue Flight Finder on a VPS

Step-by-step guide to deploy this app on a cloud server so it runs 24/7, even when your laptop is off or asleep.

---

## Table of Contents

1. [What You're Setting Up](#1-what-youre-setting-up)
2. [Choose a VPS Provider](#2-choose-a-vps-provider)
3. [Create Your VPS](#3-create-your-vps)
4. [Connect to Your VPS](#4-connect-to-your-vps)
5. [Install Docker](#5-install-docker)
6. [Upload the App](#6-upload-the-app)
7. [Configure Credentials](#7-configure-credentials)
8. [Build and Start](#8-build-and-start)
9. [Verify It Works](#9-verify-it-works)
10. [Day-to-Day Management](#10-day-to-day-management)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What You're Setting Up

You're renting a small virtual server (VPS) in the cloud. It's a Linux computer that stays on 24/7. You'll install Docker on it, which packages the app and all its dependencies (Python, Playwright, Chromium) into a single container that runs with one command.

**What you'll need:**
- A credit card (for the VPS — costs ~$4-6/month)
- Your Gmail App Password (the one already in your `.env` file)
- About 30 minutes

**What you'll end up with:**
- The app running 24/7, crawling El Al every hour
- A dashboard you can access from any browser at `http://<your-server-ip>:5000`
- Email alerts working around the clock
- Password protection on the dashboard

---

## 2. Choose a VPS Provider

Any of these work. Pick whichever you're most comfortable with.

### Option A: DigitalOcean (Recommended for beginners)

- **Cost:** $4/month (smallest "Droplet")
- **Why:** Simplest interface, excellent documentation, beginner-friendly
- **Sign up:** https://www.digitalocean.com
- You'll get a $200 free credit for 60 days as a new user

### Option B: Hetzner (Cheapest)

- **Cost:** €3.79/month (~$4)
- **Why:** Best price-to-performance ratio, European data centers
- **Sign up:** https://www.hetzner.com/cloud

### Option C: Oracle Cloud (Free forever)

- **Cost:** Free (Always Free tier includes a small VM)
- **Why:** Literally free, but the setup is more complex and availability varies
- **Sign up:** https://www.oracle.com/cloud/free
- **Caveat:** The free tier VM (1 GB RAM) is tight but should work. Oracle's UI is more confusing than the others.

### What specs do you need?

The absolute minimum:
- **1 CPU core**
- **1 GB RAM** (2 GB preferred — Playwright/Chromium is memory-hungry)
- **20 GB disk**
- **Ubuntu 22.04 or 24.04** as the operating system

If available, choose a server location close to Israel (Europe is fine) for slightly faster crawls.

---

## 3. Create Your VPS

Instructions below are for **DigitalOcean** (the recommended option). Other providers are similar.

### 3.1 Create an account

1. Go to https://www.digitalocean.com and click **Sign Up**
2. Create an account with your email or GitHub
3. Add a payment method (credit card)

### 3.2 Create a Droplet (their name for a VPS)

1. Click **Create** → **Droplets** (or the green "Create" button on the dashboard)
2. **Choose a region:** Pick one in Europe (Frankfurt or Amsterdam are good choices)
3. **Choose an image:** Select **Ubuntu 24.04 (LTS)**
4. **Choose size:**
   - Click **Basic**
   - Select **Regular (SSD)**
   - Pick the **$6/month** option (1 GB RAM, 1 CPU, 25 GB SSD)
   - The $4/month option (512 MB RAM) might be too small for Playwright
5. **Authentication:** Choose **Password** and set a strong root password
   - Write this password down — you'll need it to connect
   - Alternatively, if you know what SSH keys are, use those instead (more secure)
6. **Hostname:** Enter something like `elal-flight-finder`
7. Click **Create Droplet**

It takes about 30 seconds. When it's done, you'll see your server's **IP address** (something like `164.92.xxx.xxx`). Copy it — you'll need it throughout this guide.

---

## 4. Connect to Your VPS

You need to open a terminal connection to your server. This is called SSH.

### From Windows (your laptop)

Open a terminal (PowerShell, Command Prompt, or Git Bash) and run:

```bash
ssh root@YOUR_SERVER_IP
```

Replace `YOUR_SERVER_IP` with the IP from step 3 (e.g., `ssh root@164.92.123.45`).

- The first time, it will ask "Are you sure you want to continue connecting?" — type `yes`
- Enter the root password you set in step 3.2

You're now connected to your server. Everything you type runs on the server, not your laptop.

> **Tip:** If you get "connection refused", wait a minute — the server might still be starting up.

---

## 5. Install Docker

Run these commands one by one on your server (copy-paste each line):

```bash
# Update the package list
apt update

# Install Docker
apt install -y docker.io docker-compose-plugin

# Enable Docker to start on boot
systemctl enable docker

# Verify Docker is installed
docker --version
```

You should see something like `Docker version 24.x.x`. If you see an error, something went wrong — see [Troubleshooting](#11-troubleshooting).

---

## 6. Upload the App

You have two options: clone from GitHub (if your repo is pushed) or upload directly.

### Option A: Clone from GitHub (easiest if repo is pushed)

```bash
# Install git if needed
apt install -y git

# Clone your repository
git clone https://github.com/YOUR_USERNAME/ElAlRescueFlightFinder.git
cd ElAlRescueFlightFinder
```

Make sure the `feature/docker-deployment` branch is checked out (or merged to master):

```bash
git checkout feature/docker-deployment
```

### Option B: Upload from your laptop (if repo is private/not on GitHub)

Open a **new** terminal window on your laptop (keep the SSH session open in the other one) and run:

```bash
# From your project directory on your laptop
scp -r . root@YOUR_SERVER_IP:~/ElAlRescueFlightFinder
```

Then switch back to the SSH terminal:

```bash
cd ~/ElAlRescueFlightFinder
```

---

## 7. Configure Credentials

Create the `.env` file on the server with your email credentials and a dashboard password.

```bash
cat > .env << 'ENVFILE'
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
EMAIL_FROM=your-email@gmail.com
POLL_INTERVAL_MINUTES=60
NEWS_POLL_INTERVAL_MINUTES=30
BASIC_AUTH_USER=admin
BASIC_AUTH_PASS=choose-a-strong-password-here
ENVFILE
```

**Replace the placeholder values:**
- `your-email@gmail.com` → your actual Gmail address
- `your-gmail-app-password` → your Gmail App Password (the 16-character code like `emkf sgnx spor jnil`)
- `choose-a-strong-password-here` → a password for the dashboard (this is what you'll enter when opening the dashboard in a browser)

**Verify the file looks correct:**

```bash
cat .env
```

> **Security note:** The `.env` file contains your email password. It stays on the server and is never included in the Docker image (thanks to `.dockerignore`).

---

## 8. Build and Start

This is the moment of truth. One command builds everything and starts the app:

```bash
docker compose up -d --build
```

**What this does:**
1. Builds a Docker image (downloads Python, installs dependencies, installs Chromium — takes 3-5 minutes the first time)
2. Starts the container in the background (`-d` means "detached")
3. The app begins crawling immediately

**Watch the build progress.** If it finishes without errors, you're good. If it fails, see [Troubleshooting](#11-troubleshooting).

**Check that it's running:**

```bash
docker compose ps
```

You should see:

```
NAME                   STATUS          PORTS
elal-flight-finder     Up X seconds    0.0.0.0:5000->5000/tcp
```

---

## 9. Verify It Works

### 9.1 Check the logs

```bash
docker compose logs -f
```

You should see:
- `Starting El Al Rescue Flight Finder...`
- `Running in headless mode (no system tray)`
- `Dashboard available at http://0.0.0.0:5000`
- `Starting seat availability crawl...`
- `Seat availability crawl complete: XXX total, X new`

Press `Ctrl+C` to stop watching logs (the app keeps running).

### 9.2 Open the dashboard

Open a browser on your laptop and go to:

```
http://YOUR_SERVER_IP:5000
```

You'll be prompted for a username and password — enter the `BASIC_AUTH_USER` and `BASIC_AUTH_PASS` you set in step 7.

You should see the familiar flight dashboard, now running on the server.

### 9.3 Verify alerts work

If you already have alert configurations from your local setup, you'll need to set them up again (the server has a fresh database). You can either:
- Add alerts manually through the dashboard UI
- Run the setup script inside the container:

```bash
docker compose exec flightfinder python setup_alerts.py --date 2026-03-20 --email your-email@gmail.com
```

### 9.4 Open the firewall (if dashboard isn't accessible)

Some VPS providers block port 5000 by default. If you can't reach the dashboard:

**DigitalOcean:** Ports are open by default — no firewall changes needed.

**If you set up UFW (Ubuntu firewall):**
```bash
ufw allow 5000/tcp
```

**Hetzner Cloud:** Go to your server's "Firewalls" tab in the Hetzner console and add a rule allowing TCP port 5000 from any source.

---

## 10. Day-to-Day Management

### View logs

```bash
cd ~/ElAlRescueFlightFinder
docker compose logs -f              # Live logs (Ctrl+C to stop watching)
docker compose logs --tail 50       # Last 50 lines
```

### Restart the app

```bash
docker compose restart
```

### Stop the app

```bash
docker compose down
```

### Start it again

```bash
docker compose up -d
```

### Update the app (after pulling new code)

```bash
cd ~/ElAlRescueFlightFinder
git pull
docker compose up -d --build
```

### Check if the app survived a server reboot

The `restart: unless-stopped` policy means Docker automatically restarts the app after a reboot. To test:

```bash
reboot
```

Wait a minute, SSH back in, and check:

```bash
docker compose ps
docker compose logs --tail 20
```

### Check disk usage

```bash
docker system df          # Docker disk usage
df -h                     # Overall disk usage
```

### Clean up old Docker images (if disk gets full)

```bash
docker system prune -f
```

---

## 11. Troubleshooting

### "Connection refused" when SSHing

- Wait 1-2 minutes after creating the server — it needs time to boot
- Double-check the IP address
- Make sure you're using `ssh root@IP` (not `ssh IP`)

### Docker build fails

**"E: Unable to locate package":**
```bash
apt update && apt install -y docker.io docker-compose-plugin
```

**Out of memory during build (on 1GB RAM servers):**
```bash
# Create a swap file to give more memory
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Try building again
docker compose up -d --build
```

### App starts but crawl fails

Check logs for Playwright errors:

```bash
docker compose logs | grep -i "error\|exception\|playwright"
```

Common issues:
- **"Browser closed unexpectedly"** — usually a memory issue. Add swap (see above) or upgrade to a 2 GB RAM server.
- **"net::ERR_NAME_NOT_RESOLVED"** — DNS issue. Check internet connectivity: `docker compose exec flightfinder ping -c 3 google.com`

### Can't access dashboard from browser

1. Verify the app is running: `docker compose ps` (should show "Up")
2. Test locally on the server: `curl http://localhost:5000/api/status`
3. If curl works but browser doesn't — it's a firewall issue (see step 9.4)
4. If curl fails — check logs: `docker compose logs --tail 30`

### Forgot dashboard password

Edit the `.env` file and restart:

```bash
nano .env                    # Edit BASIC_AUTH_PASS
docker compose restart       # Apply changes
```

> **Tip on using nano:** Arrow keys to navigate, type to edit, `Ctrl+O` then `Enter` to save, `Ctrl+X` to exit.

### Need to check the database

```bash
docker compose exec flightfinder python -c "
from database import get_connection
conn = get_connection()
# Example: count flights
count = conn.execute('SELECT COUNT(*) FROM flights').fetchone()[0]
print(f'Total flights in DB: {count}')
"
```

---

## Cost Summary

| Item | Monthly Cost |
|------|-------------|
| DigitalOcean Droplet (1 CPU, 1-2 GB RAM) | $4-6 |
| Domain name (optional) | $0-1 |
| **Total** | **$4-7/month** |

The app itself uses minimal bandwidth and CPU. The main resource consumer is Playwright/Chromium during crawls (~200-400 MB RAM).
