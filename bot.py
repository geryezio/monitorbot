"""
MonitorBot — Telegram bot for ThinkCentre server monitoring.

Command:
  /start       — Show menu (all features accessible via inline buttons)

Setup:
  1. pip install -r requirements.txt
  2. cp .env.example .env  → fill BOT_TOKEN
  3. python bot.py
"""

import asyncio
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from collector import (
    cpu_percent,
    cpu_temp,
    disk_info,
    docker_containers,
    docker_group_restart,
    docker_group_start,
    docker_group_stop,
    docker_groups,
    docker_restart,
    docker_start,
    docker_stop,
    full_status,
    net_info,
    ram_info,
    swap_info,
    top_processes,
    uptime_info,
    check_image_updates,
    check_base_image_updates,
)

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set in .env")

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL_SECONDS", 86400))

# ── Formatting helpers ──────────────────────────

def fmt_bar(percent: float, width: int = 10) -> str:
    filled = int(percent / 100 * width)
    empty = width - filled
    if percent > 90:
        bar = "█" * filled + "░" * empty
    elif percent > 70:
        bar = "▓" * filled + "░" * empty
    else:
        bar = "▒" * filled + "░" * empty
    return bar


def start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🖥 status", callback_data="qcmd:status"),
            InlineKeyboardButton("💾 disk", callback_data="qcmd:disk"),
            InlineKeyboardButton("🐳 containers", callback_data="qcmd:containers"),
        ],
        [
            InlineKeyboardButton("🌐 net", callback_data="qcmd:net"),
            InlineKeyboardButton("📊 top", callback_data="qcmd:top"),
            InlineKeyboardButton("⏱ uptime", callback_data="qcmd:uptime"),
        ],
    ])


def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="qcmd:start")]])


# ── Response helpers (shared by commands & buttons) ─

async def send_status(message, context):
    data = await asyncio.to_thread(full_status)

    cpu = data["cpu"]
    ram = data["ram"]
    swap = data["swap"]
    disk = data["disk"]
    net = data["network"]
    up = data["uptime"]
    dk = data["docker"]

    root_disk = next((d for d in disk if d["mount"] == "/"), disk[0] if disk else None)

    text = (
        f"🖥 *Server Status*\n"
        f"⏱ `{datetime.now().strftime('%H:%M %d/%m/%Y')}`\n\n"
        f"*CPU:* `{cpu['percent']}%` 🌡 {cpu['temp']}  {fmt_bar(cpu['percent'])}\n"
        f"*RAM:* `{ram['percent']}%` ({ram['used_gb']}/{ram['total_gb']} GB)  {fmt_bar(ram['percent'])}\n"
        f"*Swap:* `{swap['percent']}%` ({swap['used_gb']}/{swap['total_gb']} GB)\n"
    )

    if root_disk:
        text += (
            f"*Disk (/):* `{root_disk['percent']}%` "
            f"({root_disk['used_gb']}/{root_disk['total_gb']} GB)  {fmt_bar(root_disk['percent'])}\n"
        )

    text += (
        f"\n*Docker:* 🟢{dk['running']} 🔴{dk['stopped']} out of {dk['total']} containers\n"
        f"*Uptime:* {up['uptime']}\n"
    )

    if net.get("tailscale") and net["tailscale"] != "not running":
        text += f"*Tailscale:* `{net['tailscale']}`\n"

    await message.reply_text(text, reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def send_disk(message, context):
    disks = await asyncio.to_thread(disk_info)

    if not disks:
        await message.reply_text("❌ Failed to read disk info.")
        return

    lines = ["💾 *Disk Usage*", ""]
    for d in disks:
        bar = fmt_bar(d["percent"], width=14)
        lines.append(
            f"📁 `{d['mount']}`\n"
            f"   {d['used_gb']}/{d['total_gb']} GB\n"
            f"   `{bar}` `{d['percent']}%`"
        )

    await message.reply_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def send_containers(message, context):
    groups = await asyncio.to_thread(docker_groups)

    if not groups:
        await message.reply_text("❌ Docker not accessible / no containers.")
        return

    buttons = []
    row = []
    for i, (project, containers) in enumerate(groups.items(), 1):
        running = sum(1 for c in containers if c["status"].startswith("🟢"))
        total = len(containers)
        icon = "🟢" if running == total else ("🔴" if running == 0 else "🟠")
        label = f"{icon} {project} ({running}/{total})"
        row.append(InlineKeyboardButton(label, callback_data=f"grp:{project}"))
        if i % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🔄 Check All Image Updates", callback_data="qcmd:updates")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="qcmd:start")])
    keyboard = InlineKeyboardMarkup(buttons)
    await message.reply_text("🐳 *Docker Groups:*", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def send_net(message, context):
    net = await asyncio.to_thread(net_info)

    lines = ["🌐 *Network Info*", ""]
    for iface, ip in net.items():
        if iface == "tailscale":
            continue
        lines.append(f"`{iface}` → `{ip}`")

    ts = net.get("tailscale", "not installed")
    lines.append(f"\n🔷 *Tailscale:* `{ts}`")

    await message.reply_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def send_top(message, context):
    procs = await asyncio.to_thread(top_processes, 5)

    lines = ["📊 *Top 5 Processes (CPU)*", ""]
    for i, p in enumerate(procs, 1):
        lines.append(
            f"{i}. `{p['name'][:20]}`  CPU:`{p['cpu_percent']:.1f}%`  RAM:`{p['memory_percent']}%`"
        )

    await message.reply_text("\n".join(lines), reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def send_uptime(message, context):
    up = await asyncio.to_thread(uptime_info)
    await message.reply_text(
        f"⏱ *Server Uptime*\n\n"
        f"Online since: `{up['boot_time']}`\n"
        f"Duration: *{up['uptime']}*",
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _updates_text(title, updated, up_to_date, errors, base_updated, base_ok, base_not_local, base_errors):
    """Build the /updates message: container images + Dockerfile base images."""
    lines = [title]

    if updated:
        lines.append("✅ *Container images — updates available:*")
        for u in updated:
            lines.append(f"• `{u}`")
        lines.append("_(Run `docker compose pull && docker compose up -d` in the project folder)_")
    else:
        lines.append("✅ *Container images:* all up to date")

    if base_updated:
        lines.append("\n🧱 *Dockerfile base images — updates available:*")
        for b in base_updated:
            projects = ", ".join(f"`{p}`" for p in b["projects"])
            lines.append(f"• `{b['image']}` — {projects}")
        lines.append("_(Run `docker compose build --pull && docker compose up -d` in those project folders)_")
    elif base_ok:
        # base_not_local = base images not stored locally (usually because BuildKit
        # only uses them during a build, not as tagged images).
        # Don't claim "all up to date" while some of them were not checked.
        label = "no updates detected" if base_not_local else "all up to date"
        lines.append(f"\n🧱 *Dockerfile base images:* {label}")

    if errors or base_errors:
        lines.append(f"\n⚠️ Errors: {', '.join(errors + base_errors)}")

    return "\n".join(lines)


async def send_updates(message, context):
    msg = await message.reply_text("⏳ *Checking for image updates...*\n_(Checking version only, no images will be downloaded)_", parse_mode=ParseMode.MARKDOWN)

    updated, up_to_date, errors = await asyncio.to_thread(check_image_updates)
    base_updated, base_ok, base_not_local, base_errors = await asyncio.to_thread(check_base_image_updates)

    text = _updates_text(
        "🔄 *Image Update Check Results*\n",
        updated, up_to_date, errors,
        base_updated, base_ok, base_not_local, base_errors,
    )

    await msg.edit_text(text, reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def background_update_check(context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_CHAT_ID:
        print("⚠️ ADMIN_CHAT_ID not set, skipping update check")
        return

    updated, up_to_date, errors = await asyncio.to_thread(check_image_updates)
    base_updated, base_ok, base_not_local, base_errors = await asyncio.to_thread(check_base_image_updates)

    text = _updates_text(
        "🔔 *Automatic Update Alert* 🔔\n",
        updated, up_to_date, errors,
        base_updated, base_ok, base_not_local, base_errors,
    )

    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode=ParseMode.MARKDOWN)
        print(f"📤 Update check sent to {ADMIN_CHAT_ID}")
    except Exception as e:
        print(f"❌ Failed to send update check: {e}")


# ── Command handlers ────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *MonitorBot is ready!*",
        reply_markup=start_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Quick command button handler ─────────────────

async def start_nav_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cmd = query.data.split(":", 1)[1]

    if cmd == "start":
        await query.edit_message_text(
            "🤖 *MonitorBot is ready!*",
            reply_markup=start_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    targets = {
        "status": send_status,
        "disk": send_disk,
        "containers": send_containers,
        "net": send_net,
        "top": send_top,
        "uptime": send_uptime,
        "updates": send_updates,
    }
    await targets[cmd](query.message, context)
    try:
        await query.message.delete()
    except Exception:
        pass


# ── Callback handler (interactive menu) ──────────

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("grp:"):
        # Show containers in group + group actions
        project = data.split(":", 1)[1]
        groups = await asyncio.to_thread(docker_groups)
        containers = groups.get(project, [])

        lines = [f"🐳 *{project}*", ""]
        for c in containers:
            lines.append(f"{c['status']} `{c['name']}`")

        # monitor-bot: info only, no controls
        if any(c["name"] in ("monitor-bot", "monitor_bot") for c in containers):
            lines.append("\n⚠️ _Manual control via terminal_")
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back:list"),
            ]])
            await query.edit_message_text(
                "\n".join(lines), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
            )
            return

        lines.append("")

        running = sum(1 for c in containers if c["status"].startswith("🟢"))
        stopped = len(containers) - running

        buttons = [
            [
                InlineKeyboardButton("🔄 Restart", callback_data=f"gdo:restart:{project}"),
            ],
        ]

        if stopped > 0:
            buttons[0].append(InlineKeyboardButton("▶️ Start", callback_data=f"gdo:start:{project}"))
        if running > 0:
            buttons[0].append(InlineKeyboardButton("⏹ Stop", callback_data=f"gdo:stop:{project}"))

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back:list")])
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            "\n".join(lines), reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("gdo:"):
        # Execute group action
        _, action, project = data.split(":", 2)
        funcs = {
            "restart": docker_group_restart,
            "stop": docker_group_stop,
            "start": docker_group_start,
        }
        ok, msg = await asyncio.to_thread(funcs[action], project)
        emoji = "✅" if ok else "❌"
        label = {"restart": "Restart", "stop": "Stop", "start": "Start"}[action]
        await query.edit_message_text(
            f"{emoji} *{label}* — `{project}`\n\n{msg}",
            reply_markup=back_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("cont:"):
        # Single container action
        name = data.split(":", 1)[1]
        buttons = [
            [
                InlineKeyboardButton("🔄 Restart", callback_data=f"do:restart:{name}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"do:stop:{name}"),
                InlineKeyboardButton("▶️ Start", callback_data=f"do:start:{name}"),
            ],
            [InlineKeyboardButton("🔙 Back", callback_data="back:list")],
        ]
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(
            f"🐳 *{name}* — select action:", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )

    elif data.startswith("do:"):
        _, action, name = data.split(":", 2)
        func = {"restart": docker_restart, "stop": docker_stop, "start": docker_start}[action]
        ok, msg = await asyncio.to_thread(func, name)
        emoji = "✅" if ok else "❌"
        label = {"restart": "Restart", "stop": "Stop", "start": "Start"}[action]
        await query.edit_message_text(f"{emoji} *{label}* `{name}`\n{msg}", reply_markup=back_keyboard(), parse_mode=ParseMode.MARKDOWN)

    elif data == "back:list":
        # Back to group list
        groups = await asyncio.to_thread(docker_groups)
        buttons = []
        row = []
        for i, (project, containers) in enumerate(groups.items(), 1):
            running = sum(1 for c in containers if c["status"].startswith("🟢"))
            total = len(containers)
            icon = "🟢" if running == total else ("🔴" if running == 0 else "🟠")
            label = f"{icon} {project} ({running}/{total})"
            row.append(InlineKeyboardButton(label, callback_data=f"grp:{project}"))
            if i % 2 == 0:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("🔄 Check All Image Updates", callback_data="qcmd:updates")])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="qcmd:start")])
        keyboard = InlineKeyboardMarkup(buttons)
        await query.edit_message_text("🐳 *Docker Groups:*", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)




# ── Error handler ───────────────────────────────

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Report failing handlers to Telegram so they don't fail silently in the log.

    Deliberately without parse_mode: raw Docker error messages can contain
    Markdown characters (underscores/backticks) that would break the delivery.
    """
    err = context.error
    print(f"❌ Error: {err}")

    text = f"❌ MonitorBot error\n\n{type(err).__name__}: {err}"

    try:
        chat = getattr(update, "effective_chat", None)
        if chat is not None:
            await chat.send_message(text)
        elif ADMIN_CHAT_ID:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    except Exception as e:
        print(f"❌ Failed to send error to Telegram: {e}")


# ── Main ────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Auto-register commands with Telegram (no BotFather needed)
    async def _setup(app):
        await app.bot.set_my_commands([BotCommand("start", "Show menu")])

    app.post_init = _setup

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(start_nav_handler, pattern=r"^qcmd:"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(on_error)

    # Schedule background update check
    app.job_queue.run_repeating(background_update_check, interval=UPDATE_INTERVAL, first=10)

    print("🤖 MonitorBot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
