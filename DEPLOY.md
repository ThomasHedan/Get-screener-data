# Deploying the screener

Three containers on one private network, one port open to the internet:

```
Internet ──▶ caddy (80/443, TLS)
              └─▶ web (:3000)  ──reads──┐
                                   screener-data volume
                 extractor ──writes─────┘
```

`extractor` and `web` publish no ports. Nothing but Caddy is reachable from
outside the host.

## Why this replaces the GitHub Actions schedule

The board used to be a static export rebuilt by a scheduled workflow that
committed `data/today.json`. That schedule is best-effort: on 2026-09-10 an
active, correctly configured workflow never fired once, and because a commit
was the only path to fresh data, the board simply showed the previous day.

Here the extractor owns its own clock and the site reads its output on every
request. A missed scan costs one scan, not the board, and the next tick is
five minutes away.

The workflow is kept as `workflow_dispatch` only — a manual escape hatch that
no longer competes with the extractor for `data/today.json`.

---

## 1. Any machine with Docker

```bash
git clone https://github.com/ThomasHedan/Get-screener-data.git
cd Get-screener-data
cp .env.example .env          # edit SCREENER_DOMAIN
docker compose up -d --build
```

Then:

```bash
docker compose logs -f extractor     # "Published regular scan: 5 in play of 6098 scanned"
curl -s localhost | grep -o "Warrior Trading Screener"
```

The first scan lands on the next tick, and only while a US session is live
(pre-market 04:00 ET through after-hours 20:00 ET, trading days only). Outside
those hours the extractor logs nothing and makes no network call — that is
correct, not a fault.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SCREENER_DOMAIN` | `:80` | Hostname to serve. A real hostname gets automatic Let's Encrypt TLS; `:80` is plain HTTP on the IP, for smoke tests only. |
| `SCAN_INTERVAL_SECONDS` | `300` | Seconds between scans during live sessions. |

Keep the interval at 300 or above. TradingView's scanner endpoint is
undocumented and unmetered by courtesy, not by contract — scanning the whole
market every few seconds is how that courtesy gets withdrawn.

---

## 2. Oracle Cloud (Always Free)

The only genuinely free tier with enough headroom to be comfortable:
up to 4 Ampere A1 cores and 24 GB RAM.

**Provisioning is the hard part, not the deploy.** The free ARM shapes are
frequently out of capacity in a given availability domain, and the error says
so plainly. Retry, or try another AD in the same region. A card is required
for identity verification even though the shape is free.

1. Create an **Ampere A1 (VM.Standard.A1.Flex)** instance — 2 OCPU / 12 GB is
   ample. Ubuntu 22.04 or Oracle Linux 9.
2. **Security List / NSG**: add ingress rules for TCP **80** and **443** from
   `0.0.0.0/0`.
3. **Open the host firewall too.** This is the step that catches everyone —
   Oracle's images ship with local rules that drop 80/443 even after the
   security list is open, so the port looks closed with no useful error:

   ```bash
   # Ubuntu
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save

   # Oracle Linux
   sudo firewall-cmd --permanent --add-service=http --add-service=https
   sudo firewall-cmd --reload
   ```

4. Install Docker and deploy:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   git clone https://github.com/ThomasHedan/Get-screener-data.git
   cd Get-screener-data && cp .env.example .env
   docker compose up -d --build
   ```

**ARM is handled for you**: the images build from source on the VM, so they
build natively as arm64. There is no cross-build step and no registry to push
to.

One caveat worth knowing before you rely on it: Oracle reclaims Always Free
compute that stays idle. This workload is not idle during market hours, but do
not treat the instance as permanent storage.

---

## 3. Google Cloud (`e2-micro` free tier)

Works, with one real constraint: **1 GB of RAM**, and `npm run build` for
Next.js will very likely be OOM-killed there. Add swap before deploying:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Then open the firewall and deploy as above:

```bash
gcloud compute firewall-rules create allow-web \
  --allow tcp:80,tcp:443 --target-tags=http-server
```

The free `e2-micro` is limited to `us-west1`, `us-central1` and `us-east1`.
`us-east1` is closest to the exchanges, which matters only marginally here —
the scan is one request every five minutes, not a latency-sensitive feed.

---

## 4. TLS

Point an A record at the host, put the hostname in `.env`, and restart Caddy:

```bash
echo "SCREENER_DOMAIN=screener.example.com" >> .env
docker compose up -d caddy
```

Caddy requests and renews the certificate on its own. The `caddy-data` volume
holds the certificates — keep it, or the next start re-requests them and can
hit Let's Encrypt rate limits.

---

## 5. Operations

```bash
docker compose logs -f extractor        # scan-by-scan
docker compose ps                       # web reports healthy/unhealthy
docker compose pull && docker compose up -d --build     # update
```

**Read a past session.** Every scan is archived alongside the current board:

```bash
docker compose exec web ls /data/history/2026-09-10/
docker compose exec web cat /data/history/2026-09-10/143000.json
```

**Back up the archive:**

```bash
docker run --rm -v get-screener-data_screener-data:/d -v "$PWD":/out \
  alpine tar czf /out/screener-archive.tgz -C /d history
```

**What is not monitored.** Nothing pages you if the host dies. That is the real
cost of moving off managed infrastructure, and it is worth closing with an
external uptime check against the dashboard URL before you depend on this.

---

## 6. Verifying a live session

The board carries its own timestamp and a staleness notice, so check the page
rather than the logs:

| Paris | ET | What should be on the page |
|---|---|---|
| 15:25 | 09:25 | Pre-market notice, scan under 5 min old |
| 15:35 | 09:35 | No notice, `Open`, gap-and-go names with high RVOL |
| 16:30 | 10:30 | No notice, `Open` |
| 22:00 | 16:00 | After-hours notice shortly after |

If `generated_at` is more than five minutes old during a session, the
extractor is the place to look:

```bash
docker compose logs --tail=50 extractor
```

Two things the notice is telling you honestly rather than hiding: TradingView's
relative volume is **time-of-day normalized** (four digits at 09:35 is correct,
not a bug), and no free news source backs this path, so every row lands as
`relaxed` — nothing checked for a catalyst.
