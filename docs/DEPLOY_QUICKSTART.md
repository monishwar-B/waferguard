# Deploying WaferGuard

Two ways to run the full system: PostgreSQL database, Redis job queue, batch workers,
web app, Prometheus + Grafana monitoring, and optional HTTPS.

| | Option A: your PC / local network | Option B: on the internet |
|---|---|---|
| Who can use it | You, plus phones, tablets and PCs on the same Wi-Fi/LAN | Anyone with the link and a login |
| Cost | Free | A cloud VM (free tiers and student credits cover it) |
| Time | ~20 minutes | ~45 minutes |

Both use the same `docker compose` setup. The full stack was verified against real PostgreSQL 16
and Redis 7 with two workers: simultaneous cold start, batch processing, live updates, and
recovery from a Redis restart.

---

## Before either option: prepare your model (one time)

If you retrained the model on your PC, convert its gradient-boosting part to the portable
format so it loads in Docker regardless of library versions. From the project folder:

```powershell
python scripts/convert_gbm.py models/wafer-ensemble
```

It should print `converted gbm_features -> gbm_features.npz (verified ...)`. The shipped
model is already converted, so this does nothing if you didn't retrain.

---

## Option A: Windows PC or local network (Docker Desktop)

### 1. Install Docker Desktop (one time)
1. Download it from https://www.docker.com/products/docker-desktop/ and install it with the
   default "Use WSL 2" option ticked.
2. Restart Windows when asked, then start **Docker Desktop** and wait until it shows
   "Engine running".
3. Check it in PowerShell: `docker --version` and `docker compose version` should both print versions.

If Docker says virtualization is disabled, enable "Intel VT-x" / "AMD-V" (SVM) in the BIOS.

### 2. Set your passwords
In the project folder (the one with `docker-compose.yml`):

```powershell
Copy-Item .env.example .env
notepad .env
```

Change every `replace-me` value. For `WG_JWT_SECRET`, generate a long random value with:

```powershell
-join ((48..57)+(65..90)+(97..122) | Get-Random -Count 64 | ForEach-Object {[char]$_})
```

### 3. Start everything

```powershell
docker compose up -d --build
```

The first build downloads images and builds the app (5–15 minutes). Later starts take seconds.
Watch progress with `docker compose ps`. When `api` shows **(healthy)**, open:

- **App:** http://localhost:8000, logging in with `admin` and your `WG_ADMIN_PASSWORD`
- **Grafana:** http://localhost:3000 (`admin` / your `GRAFANA_PASSWORD`), dashboard "WaferGuard line overview"
- **API docs:** http://localhost:8000/docs

### 4. Use it from other devices on your network
1. Find your PC's address with `ipconfig`. Look for "IPv4 Address", e.g. `192.168.1.25`.
2. Allow the port through Windows Firewall (PowerShell **as Administrator**):
   ```powershell
   New-NetFirewallRule -DisplayName "WaferGuard" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
3. On a phone or tablet on the same Wi-Fi, open `http://192.168.1.25:8000`.

**Station camera over the network:** browsers only allow camera access on HTTPS pages (or on
`localhost`). To use a tablet's camera, start the HTTPS front door with `WG_DOMAIN` set to
your PC's IP in `.env`:
```powershell
docker compose --profile https up -d
```
Then open `https://192.168.1.25`. The certificate is self-signed, so accept the browser
warning once per device.

### Everyday commands
| Task | Command |
|---|---|
| Stop everything | `docker compose down` (data is kept) |
| Start again | `docker compose up -d` |
| See logs | `docker compose logs -f api worker` |
| More batch throughput | `docker compose up -d --scale worker=4` |
| Update after code changes | `docker compose up -d --build` |
| Back up the database | `docker compose exec postgres pg_dump -U waferguard waferguard > backup.sql` |
| Delete **all** data | `docker compose down -v` (irreversible) |

---

## Option B: on the internet (cloud VM with HTTPS)

Any Linux VM works: AWS EC2, Azure VM, Google Compute Engine, DigitalOcean. Student programs
(GitHub Student Developer Pack, Azure for Students, AWS Educate) usually cover the cost.

### 1. Create the VM
- **Ubuntu 22.04 or 24.04**, at least **2 vCPU / 4 GB RAM** and a **30 GB** disk.
- Open inbound ports **22** (SSH), **80** and **443** in the VM's firewall / security group.
  Do *not* open 8000, 3000, 9090 or 5432 to the internet.

### 2. Point a domain at it (for a real certificate)
Create a DNS **A record**, e.g. `waferguard.yourdomain.com` → the VM's public IP. A free
subdomain from DuckDNS (duckdns.org) also works.

### 3. Install Docker and copy the project

```bash
ssh ubuntu@<vm-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

From your PC, copy the project up (run in PowerShell in the folder *above* the project):
```powershell
scp -r .\waferguard ubuntu@<vm-ip>:~/
```
The `dataset` folder isn't needed on the server; delete it from the copy to save time.

### 4. Configure and start

```bash
cd ~/waferguard
cp .env.example .env
nano .env        # set all passwords, and WG_DOMAIN=waferguard.yourdomain.com
docker compose --profile https up -d --build
```

Caddy obtains a Let's Encrypt certificate automatically. Open
`https://waferguard.yourdomain.com` and log in.

### 5. Lock it down
- In `docker-compose.yml`, change the api port line to `"127.0.0.1:${WG_PORT:-8000}:8000"`,
  so the app is reachable only through HTTPS. Do the same for Grafana (`127.0.0.1:3000:3000`)
  and Prometheus, then reach them through an SSH tunnel:
  `ssh -L 3000:localhost:3000 ubuntu@<vm-ip>`.
- Change the admin password on first login and create named accounts for everyone else
  (Administration → Users).
- Back up nightly, e.g. with a cron job running the `pg_dump` command above.

---

## Troubleshooting a deployment

| Symptom | Fix |
|---|---|
| `api` never becomes healthy | `docker compose logs api`. "model not loaded" means `models/wafer-ensemble/manifest.json` is missing or the model needs `scripts/convert_gbm.py`. |
| "cannot load … convert it once with: python scripts/convert_gbm.py" | Run that command on the PC where you trained the model, then `docker compose restart api worker`. |
| Port 8000 already in use | Set `WG_PORT=8080` in `.env` and open http://localhost:8080. |
| Batch jobs stay "queued" | `docker compose ps worker` must show it running; `docker compose logs worker`. |
| HTTPS certificate not issued | The DNS record must point at the VM, and ports 80 and 443 must be open. `docker compose logs caddy` shows the reason. |
| Tablet camera button does nothing | The page must be HTTPS (see Option A step 4, or Option B). |


---

## Option C: free website on Streamlit Community Cloud (no card, no server)

`demo/streamlit_app.py` is a Streamlit edition of WaferGuard. It uses the same model, localization,
severity grading, root-cause guide and PDF reports, and deploys from this GitHub repository to a free
permanent `https://<name>.streamlit.app` address that works on any PC or phone.

It leaves out what needs a server: user accounts, the permanent database and audit trail, alerts,
batch workers and industrial cameras. History lasts for the browser session. For those features,
use Option A or B.

1. Push this repository to GitHub (it may be private).
2. Go to **share.streamlit.io**, sign in with GitHub and allow access to your repositories.
3. **Create app -> Deploy a public app from GitHub**:
   - Repository: `<your-username>/waferguard`, branch `main`
   - Main file path: `demo/streamlit_app.py`
   - App URL: choose a name, e.g. `waferguard-cit`
   - **Advanced settings:** Python **3.12**. Optionally, under **Secrets**, add
     `APP_PASSWORD = "your-password"` so visitors must enter a password.
4. Click **Deploy**. The first build takes 3-6 minutes, after which the link is live.

Updates deploy automatically on every `git push`. Apps sleep after a period without visitors;
the next visitor wakes it with one click (about 30 seconds).

To try it on your PC first: `pip install streamlit`, then run the app with Streamlit
(`python -m streamlit run demo/streamlit_app.py`).
