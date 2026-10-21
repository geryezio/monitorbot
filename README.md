# 🐳 MonitorBot

> Control and monitor your home server directly from Telegram — no SSH required.

[![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Telegram](https://img.shields.io/badge/Telegram_Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![License](https://img.shields.io/badge/License-MIT-CBA135?style=for-the-badge)](LICENSE)

---

## 📖 Overview

**MonitorBot** is a self-hosted Telegram bot that monitors your server in real-time — CPU, RAM, disk, network, Docker containers, and Tailscale addresses. It runs inside a Docker container with read-only access to the host filesystem, keeping it safe and isolated.

No terminal or SSH needed. Just open Telegram, type `/status`, and all your server info is at your fingertips.

---

## ✨ Key Features

- 🖥️ **`/status`** — Full summary: CPU, RAM, swap, root disk, Docker overview, uptime, and Tailscale
- 💾 **`/disk`** — All physical mount points with usage bars and percentages
- 🐳 **`/containers`** — Docker containers grouped by compose project; restart, stop, and start via inline buttons
- 🌐 **`/net`** — Local IP addresses per interface & Tailscale address
- 📊 **`/top`** — Top 5 processes by CPU and RAM usage
- ⏱️ **`/uptime`** — Time since the last server reboot
- 🔄 **`/updates`** — Check Docker image updates (registry vs local digest) **plus base images from each project's Dockerfile** (`FROM php:8.5-fpm`, `node:24`, dst); automatic periodic notifications
- 🔘 **Inline Keyboard** — Quick navigation without retyping commands
- 📝 **Auto-register Commands** — All commands appear in the Telegram bot menu with no BotFather setup

---

## 🛠️ Tech Stack

| Component | Technology | Role |
| --- | --- | --- |
| **Runtime** | Python 3.14 | Bot logic and data collection |
| **Bot Framework** | `python-telegram-bot` v20+ | Command handling, callback queries, and job queue |
| **System Metrics** | `psutil` 5.9+ | Reading CPU, RAM, disk, network, and processes from host `/proc` |
| **Docker SDK** | `docker` 7.0+ | Container management (list, start, stop, restart) via Docker socket |
| **Registry Check** | `urllib` (stdlib) | Comparing local vs remote registry image digests |
| **Environment** | `python-dotenv` | Loading configuration from `.env` file |
| **Containerization** | Docker + Docker Compose | Build, deploy, and isolate the bot from the host system |
| **Base Image** | `python:3.14-slim` | Lightweight image (~50 MB) for storage efficiency |

---

## 🚀 Getting Started

<details>
<summary><b>1. Clone the Repository</b></summary>

```bash
git clone https://github.com/geryezio/monitorbot.git
cd monitorbot
```

</details>

<details>
<summary><b>2. Configure Environment</b></summary>

Copy the environment template and fill in your Telegram bot token from [@BotFather](https://t.me/BotFather):

```bash
cp .env.example .env
# Edit .env → fill in your BOT_TOKEN
```

</details>

<details>
<summary><b>3. Build & Run</b></summary>

```bash
docker compose up -d --build
```

The bot will run in the background. Check logs with:

```bash
docker compose logs -f
```

</details>

<details>
<summary><b>4. Verify</b></summary>

Open Telegram, find your bot, and send `/start`. If the inline menu appears, the bot is ready.

</details>

---

## 🔑 Environment Configuration

| Variable | Required | Default | Description |
| --- | :---: | --- | --- |
| `BOT_TOKEN` | ✅ | — | Telegram bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_CHAT_ID` | ❌ | — | Telegram chat ID for automatic update notifications; if empty, periodic checks are skipped |
| `UPDATE_INTERVAL_SECONDS` | ❌ | `86400` | Interval (in seconds) for automatic Docker image update checks |

---

## 📄 License

Distributed under the [MIT License](LICENSE). © 2026 [geryezio](https://github.com/geryezio).

---

<p align="center">
  <sub>Built with 🐍 Python · 🐳 Docker · 💬 Telegram</sub>
</p>
