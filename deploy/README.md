# Always-on hosting on an Oracle Cloud Always Free VM

Goal: the dashboard link stays reachable even when the Mac is off, at the
**same URL the family already has** (`recollect-stardom-carve.ngrok-free.dev`),
because ngrok's reserved domain moves with the app.

Oracle's Always Free ARM instance (2 cores / 12GB RAM) is far more than this
app needs and costs $0 indefinitely.

---

## Part 1 — What you have to do yourself (account signup)

1. Sign up at <https://www.oracle.com/cloud/free/>.
   - A credit card is required for identity verification. Always Free
     resources are not charged, but read the signup screens yourself and
     decide if you're comfortable.
   - Pick a home region close to you, but see the capacity note below.

2. Create the VM: **Compute → Instances → Create instance**
   - **Image:** Ubuntu 22.04 or 24.04
   - **Shape:** `VM.Standard.A1.Flex` (Ampere ARM), 1–2 OCPU, 6–12 GB RAM
     — must show the **"Always Free eligible"** label
   - **SSH keys:** upload your Mac's public key (`~/.ssh/id_rsa.pub`), or
     let Oracle generate one and save the private key
   - Create, then copy the instance's **public IP address**

   > **Capacity note:** ARM (A1) instances frequently fail with
   > "Out of host capacity" in busy regions. If that happens, retry later,
   > try a different availability domain, or fall back to the always-free
   > x86 `VM.Standard.E2.1.Micro` shape (weaker, but the scripts handle both).

3. Open the firewall so ngrok can reach out (outbound only — no inbound
   ports need opening, which is part of why the tunnel approach is safer
   than exposing the VM directly).
   Oracle's default Ubuntu images also have local iptables rules; nothing
   needs changing for outbound-only tunnels.

4. Confirm you can connect from your Mac:
   ```bash
   ssh -i ~/.ssh/id_rsa ubuntu@<PUBLIC_IP>
   ```

Once `ssh` works, tell me the public IP and I'll run the rest.

---

## Part 2 — Deployment (can be automated)

On the VM:
```bash
curl -sO https://raw.githubusercontent.com/cpos97/windstar-tahiti-price-tracker/main/deploy/setup-vm.sh
bash setup-vm.sh
```

From your Mac (copies credentials, saved login session, price history):
```bash
VM=ubuntu@<PUBLIC_IP> bash deploy/sync-to-vm.sh
```

Back on the VM:
```bash
ngrok config add-authtoken <YOUR_NGROK_TOKEN>
cd ~/cruise-price-tracker && bash deploy/install-services.sh
```

That installs two systemd services, both `Restart=always` and enabled at
boot, so the app and tunnel come back automatically after a reboot:

| Service | Purpose |
|---|---|
| `cruise-tracker.service` | FastAPI app + 30-min price / daily cabin scheduler |
| `cruise-tunnel.service`  | ngrok tunnel on the reserved domain |

Useful commands:
```bash
sudo systemctl status cruise-tracker
sudo journalctl -u cruise-tracker -f
tail -f ~/cruise-price-tracker/data/tracker-server.log
```

---

## Part 3 — After cutover

- **Stop the Mac copy**, or you'll have two schedulers scraping and both
  able to send price-drop emails:
  ```bash
  pkill -f "run.py"; pkill -x ngrok
  ```
- **Disable the GitHub Actions cron.** It exists only to cover "Mac is
  off", which the VM now covers. Leaving it on means duplicate checks and
  possible duplicate alert emails:
  Actions → *Hourly cruise price check* → ⋯ → **Disable workflow**
- **Re-check the ID90 login session periodically.** The saved session
  expires. Refreshing it needs an interactive browser, which is awkward
  headless — easiest path is to re-run the login script on your Mac and
  re-run `deploy/sync-to-vm.sh` to push the refreshed session up.

## Security notes

- `.env` and `browser_session.json` are `chmod 600` on the VM and are
  gitignored, so they never reach GitHub.
- No inbound ports are exposed; the tunnel dials out. The site itself is
  still behind the `SITE_PASSWORD` login.
- Your ID90 / Perx credentials will live on the VM. That's inherent to
  moving the scraper off your Mac — worth knowing, not a bug.
- Keep the VM patched: `sudo apt-get update && sudo apt-get upgrade -y`.

## Custom domain + HTTPS (replaces the ngrok tunnel)

The site is served directly from the VM at **https://postmatahiti.duckdns.org**
via Caddy, with an automatic Let's Encrypt certificate. The ngrok tunnel
(`cruise-tunnel.service`) is disabled — no more click-through interstitial.

Pieces involved:

- **DNS**: DuckDNS subdomain `postmatahiti` → the VM's public IP.
- **`~/duckdns/duck.sh`** (cron, every 5 min): re-points DuckDNS at whatever
  IP the VM currently has. Oracle public IPs are *ephemeral* and can change
  if the instance is stopped/started, which would otherwise break the site.
- **`/etc/caddy/Caddyfile`**: terminates TLS, reverse-proxies to
  `127.0.0.1:8765`. Caddy renews the cert on its own.
- **Firewall, two layers** — both are required:
  1. Host `iptables` (persisted via `netfilter-persistent`)
  2. OCI VCN security list ingress rules for TCP 80 and 443
     (Networking → VCN → Default Security List → Add Ingress Rules)

Port 80 must stay open: it's used for HTTP→HTTPS redirect and for
Let's Encrypt renewal challenges.

To go back to ngrok: `sudo systemctl enable --now cruise-tunnel`.
