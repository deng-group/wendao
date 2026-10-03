# Server Deployment

This puts the course website and the Wendao widget API on one Linux server:

```text
https://YOUR_DOMAIN/                  -> static course website
https://YOUR_DOMAIN/api/answer        -> Wendao widget API (Flask + Gunicorn)
https://YOUR_DOMAIN/api/answer/stream -> streaming answers, used by the course widget
```

The server keeps three folders:

```text
/srv/mle-course-helper/wendao/       the Wendao tool (this repository)
/srv/mle-course-helper/workspace/    the course workspace: wendao.toml, concepts.json, build/
/srv/mle-course-helper/book/         the built course website
```

The widget uses `http://127.0.0.1:5055` when you test locally. On a real domain it calls the same
address as the page, so Nginx sends `/api/*` to Wendao.

**Is your course website static** (Cloudflare Pages, GitHub Pages, Netlify)? Then it can't run Wendao. Run
Wendao on any machine that stays on (a lab server or an office PC) and give it its own address. Do steps 1 to 5 below
on that machine, then follow [Static course website: Cloudflare Tunnel](#static-course-website-cloudflare-tunnel)
instead of steps 6 and 7.

## 1. Server packages

Ubuntu example:

```bash
sudo apt update
sudo apt install -y git rsync nginx python3 curl
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create the folders and a service user:

```bash
sudo useradd --system --home /srv/mle-course-helper --shell /usr/sbin/nologin mlehelper || true
sudo mkdir -p /srv/mle-course-helper/{wendao,workspace,book}
sudo chown -R "$USER":"$USER" /srv/mle-course-helper
```

## 2. Upload Wendao, the workspace, and the website

Build the workspace on your laptop first (`wendao build`), so the server doesn't need the lecture notes.
Then, from this repository:

```bash
bash deploy/sync_to_server.sh USER@SERVER /path/to/MLE4217_5219_book [/path/to/workspace]
```

This builds the book, then uploads Wendao, the workspace (default: `examples/mle4217_5219`), and the
built website. It never uploads `.env` files.

## 3. Install Wendao

On the server:

```bash
cd /srv/mle-course-helper/wendao
bash deploy/install_backend.sh
mkdir -p /srv/mle-course-helper/wendao/.cache/huggingface
sudo chown -R mlehelper:mlehelper /srv/mle-course-helper/wendao/.cache
```

The script installs the locked dependencies and runs one test search. The first run takes a while
because it downloads the search model.

## 4. Add the API key

```bash
sudo cp /srv/mle-course-helper/wendao/deploy/env.example /etc/mle-course-helper.env
sudo nano /etc/mle-course-helper.env
sudo chmod 600 /etc/mle-course-helper.env
```

The model itself is chosen under `[model]` in the workspace's `wendao.toml`. Put only the key in this
file, for example `ANTHROPIC_AUTH_TOKEN=...`. Anything you set here overrides `wendao.toml`.

## 5. Start the service

```bash
sudo cp /srv/mle-course-helper/wendao/deploy/mle-course-helper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mle-course-helper
sudo systemctl status mle-course-helper --no-pager
```

Check it:

```bash
curl http://127.0.0.1:5055/api/health
curl -X POST http://127.0.0.1:5055/api/answer -H 'Content-Type: application/json' -d '{"query":"What is MACE?"}'
sudo journalctl -u mle-course-helper -f     # logs
```

## 6. Nginx

Edit the domain in the config, then enable it:

```bash
sudo cp /srv/mle-course-helper/wendao/deploy/nginx-mle-course-helper.conf /etc/nginx/sites-available/mle-course-helper
sudo nano /etc/nginx/sites-available/mle-course-helper
sudo ln -sf /etc/nginx/sites-available/mle-course-helper /etc/nginx/sites-enabled/mle-course-helper
sudo nginx -t
sudo systemctl reload nginx
```

Open `http://YOUR_DOMAIN/` and try the widget in the bottom-right corner.

## 6b. The course website (optional)

Steps 5 and 6 run the API for the chat widget inside your course book. To also give students the Wendao website itself
(the knowledge graph with the AI agent), run a second service and give it its own domain, for example
`wendao.course.example.edu`:

```bash
sudo cp /srv/mle-course-helper/wendao/deploy/wendao-site.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wendao-site
```

Then add an Nginx site for that domain that sends everything to `http://127.0.0.1:5057` (use the same `proxy_set_header`
lines and `proxy_buffering off;` as the `/api/` block in `nginx-mle-course-helper.conf`).

How students use AI is set under `[student]` in the workspace's `wendao.toml`. With `ai = "teacher"`, set
`questions_per_day` to cap each student's daily questions on your key. Put the website's address in `[student] server`
so that course files made with `wendao pack` send their questions to it.

With a class list (`[student] roster = "students.csv"`), students sign in with their email and the daily limit counts per
student. The server keeps the counts in `usage.db` and its sign-in key in `.wendao-secret`, both next to `wendao.toml`.
`sync_to_server.sh` never overwrites or deletes them, and the class list is reread when you upload a new one. Check usage
on the server with `cd /srv/mle-course-helper/workspace && /srv/mle-course-helper/wendao/.venv/bin/wendao students`.

## 7. HTTPS

If the server is public and the domain points to it:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

## Static course website: Cloudflare Tunnel

A Cloudflare Tunnel gives the Wendao machine a public HTTPS address, such as `https://wendao.example.edu`,
without opening any ports or setting up Nginx. You need your domain on Cloudflare (it is, if your site runs on
Cloudflare Pages with your own domain).

1. In the Cloudflare dashboard, open **Zero Trust → Networks → Tunnels → Create a tunnel**, pick **Cloudflared**, and
   name it `wendao`.
2. Cloudflare shows an install command for the connector. Run it on the Wendao machine. It looks like
   `sudo cloudflared service install eyJ...`, and it keeps the tunnel running after restarts.
3. Under **Public hostname**, add a subdomain, for example `wendao` on your domain. Set the service to
   **HTTP** and `localhost:5055`.

Check it from any computer:

```bash
curl https://wendao.example.edu/api/health
```

Then point the widget at that address when you build the website:

```bash
pip install --no-deps "wendao>=0.3"     # enough for `wendao widget install`; quick in a Pages build
wendao widget install _build/html --api https://wendao.example.edu
```

The API accepts requests from the website in `[course] website` in `wendao.toml` (add others under
`[student] allowed_origins`). Behind the tunnel, Wendao reads each student's address from Cloudflare's
`CF-Connecting-IP` header, so daily limits work per student.

If the website is built by Cloudflare Pages from your repository, put both lines in its build (for example in
`make web`), after the site is built.

## 8. Updating later

After changing Wendao, the notes, or the workspace, rebuild locally (`wendao build`) and run the same
upload, then restart:

```bash
bash deploy/sync_to_server.sh USER@SERVER /path/to/MLE4217_5219_book [/path/to/workspace]
ssh USER@SERVER 'cd /srv/mle-course-helper/wendao && bash deploy/install_backend.sh && sudo systemctl restart mle-course-helper'
```

## Keys

Never commit `.env` files or API keys. If a key was ever committed or pasted into a script, replace it
with a new one before you deploy.
