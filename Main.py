import os
import asyncio
import json
from datetime import datetime, timedelta, timezone
from dateutil import tz

import discord
from discord.ext import commands, tasks

import snscrape.modules.twitter as sntwitter
import gspread
from oauth2client.service_account import ServiceAccountCredentials

from openai import OpenAI

# -------------------- ENV VARS (SET THESE IN RAILWAY) --------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")                      # <-- your Discord bot token
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")                    # <-- your OpenAI key
SERVER_NAME = os.getenv("NEWS_SERVER_NAME", "roblox-news🌍")    # where news posts go
NEWS_CHANNEL_NAME = os.getenv("NEWS_CHANNEL_NAME", "news")      # channel in that server

APPLICATION_REVIEW_CHANNEL = os.getenv("APPLICATION_REVIEW_CHANNEL", "application-review")
APPROVER_ROLES = [r.strip() for r in os.getenv("APPROVER_ROLES", "JRowner,Owner,Co-Owner").split(",")]

# Google credentials JSON (paste full service-account JSON into this variable in Railway)
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
# Sheet IDs for each form type (File > Share with service account email) + (URL part after /d/)
TESTER_SHEET_ID = os.getenv("TESTER_SHEET_ID", "")
STAFF_SHEET_ID  = os.getenv("STAFF_SHEET_ID", "")

# -------------------- OPENAI CLIENT --------------------
oai = OpenAI(api_key=OPENAI_API_KEY)

# -------------------- DISCORD SETUP --------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

# -------------------- STATE --------------------
# Track last seen tweet IDs to avoid duplicates
last_seen_tweet = {"Roblox_RTC": None, "BloxyNews": None, "Roblox": None}

# Application store (in-memory). For persistence, swap to a DB.
# app_id -> dict(...)
applications = {}
next_app_id = 1

# Track last processed row in each sheet so we only import new applications
last_sheet_row = {"tester": 0, "staff": 0}

# -------------------- GOOGLE SHEETS --------------------
def get_gsheet_client():
    if not GOOGLE_CREDENTIALS_JSON:
        return None
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def open_worksheet(sheet_id: str):
    gc = get_gsheet_client()
    if not gc or not sheet_id:
        return None
    sh = gc.open_by_key(sheet_id)
    return sh.sheet1

# -------------------- HELPERS --------------------
def is_approver(member: discord.Member) -> bool:
    return any(r.name in APPROVER_ROLES for r in member.roles)

def utc_now():
    return datetime.now(timezone.utc)

def short_or_long(user_text: str) -> int:
    """Return max_tokens depending on whether user asked to 'make it longer'."""
    msg = user_text.lower()
    if "make it longer" in msg or "longer" in msg:
        return 600
    return 100  # ~1–2 sentences

async def ask_openai(prompt: str) -> str:
    resp = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "Be concise. Default to 1–2 sentences unless asked to be longer."},
                  {"role": "user", "content": prompt}],
        max_tokens=short_or_long(prompt),
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()

async def post_embed(channel: discord.TextChannel, title: str, description: str, fields=None, footer=None):
    embed = discord.Embed(title=title, description=description, color=0x5865F2)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    return await channel.send(embed=embed)

# -------------------- NEWS SCRAPING --------------------
def fetch_new_tweets(username: str):
    """Return list of new tweet texts since last_seen_tweet[username]. Oldest->Newest."""
    new_texts = []
    for tweet in sntwitter.TwitterUserScraper(username).get_items():
        tid = tweet.id
        if last_seen_tweet[username] is None:
            last_seen_tweet[username] = tid
            break
        if tid == last_seen_tweet[username]:
            break
        new_texts.append(tweet.content)
    if new_texts:
        # set last to newest tweet id we saw
        last_seen_tweet[username] = tweet.id
    return list(reversed(new_texts))

async def news_tick(now_utc: datetime):
    """Run on a 60s heartbeat; fire at :00, :25, :45 (UTC)."""
    # You said “like 1am, 1:25am, 1:45am” — this code uses UTC to be consistent on Railway.
    minute = now_utc.minute
    target = None
    label = None
    if minute == 0:
        target = "Roblox_RTC"; label = "RTC"
    elif minute == 25:
        target = "BloxyNews"; label = "BloxyNews"
    elif minute == 45:
        target = "Roblox"; label = "Roblox"

    if not target:
        return

    # Find the news channel in the specific server
    guild = discord.utils.get(bot.guilds, name=SERVER_NAME)
    if not guild:
        return
    channel = discord.utils.get(guild.text_channels, name=NEWS_CHANNEL_NAME)
    if not channel:
        return

    posts = fetch_new_tweets(target)
    if not posts:
        await channel.send(f"{label}: No new")
        return

    # Grouped message
    lines = [f"{label}: {p}" for p in posts]
    # Discord max message length guard
    chunk = ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 1800:
            await channel.send(chunk)
            chunk = ""
        chunk += (line + "\n")
    if chunk:
        await channel.send(chunk)

@tasks.loop(seconds=60)
async def scheduler_loop():
    # heartbeat every 60s; act only on :00/:25/:45
    now = utc_now()
    if now.minute in (0, 25, 45) and now.second < 5:
        await news_tick(now)

# -------------------- APPLICATIONS (GOOGLE FORMS → SHEETS) --------------------
FORM_MAP = {
    "tester": TESTER_SHEET_ID,
    "staff":  STAFF_SHEET_ID,
}

@bot.command()
async def apply(ctx, app_type: str = None):
    """DM the correct Google Form link, or list available forms."""
    forms = {
        "tester": "https://docs.google.com/forms/d/e/1FAIpQLSeE-fn6-jd-JJ9gOr738MbJ8tqUq23_gwKTsk-EIJ9R-I-l7g/viewform?usp=header",
        "staff":  "https://docs.google.com/forms/d/e/1FAIpQLSe4swMgOqCEYfxIiQOi_y4VRUGTLtth6ga0Gq-Bs87t_OaY9Q/viewform?usp=sharing"
    }
    if not app_type:
        listing = "\n".join([f"- {k}: {v}" for k, v in forms.items()])
        await ctx.send(f"Available applications:\n{listing}")
        return
    key = app_type.lower()
    if key not in forms:
        await ctx.send("Invalid application. Try `/apply tester` or `/apply staff`.")
        return
    try:
        await ctx.author.send(f"📋 Fill out the **{key}** application here:\n{forms[key]}\n"
                              f"Once submitted, it will appear in **#{APPLICATION_REVIEW_CHANNEL}** for review.")
        await ctx.send(f"✅ {ctx.author.mention} I DM’d you the **{key}** application link.")
    except discord.Forbidden:
        await ctx.send("I can’t DM you. Please enable DMs and try again.")

@tasks.loop(minutes=2)
async def pull_new_form_responses():
    """Poll Google Sheets every 2 minutes, import new rows into #application-review."""
    review_guild = None
    review_channel = None
    for g in bot.guilds:
        ch = discord.utils.get(g.text_channels, name=APPLICATION_REVIEW_CHANNEL)
        if ch:
            review_guild = g
            review_channel = ch
            break
    if not review_channel:
        return

    for app_key, sheet_id in FORM_MAP.items():
        if not sheet_id:
            continue
        ws = open_worksheet(sheet_id)
        if not ws:
            continue
        rows = ws.get_all_records()  # list of dicts
        start_index = last_sheet_row.get(app_key, 0)
        if len(rows) <= start_index:
            continue

        new_rows = rows[start_index:]
        for row in new_rows:
            # Expect sheet to have at least: "Discord ID" or "Discord Name", plus answers columns
            applicant_id = str(row.get("Discord ID", "")).strip()
            applicant_name = row.get("Discord Name", "").strip()

            # Build answers block
            answers = "\n".join([f"**{k}:** {v}" for k, v in row.items() if k not in ("Discord ID", "Discord Name")])

            # Resolve member (if Discord ID was provided)
            member_mention = applicant_name or "Unknown Applicant"
            member_obj = None
            if applicant_id.isdigit() and review_guild:
                member_obj = review_guild.get_member(int(applicant_id))
                if member_obj:
                    member_mention = member_obj.mention

            # Post to review channel
            title = f"{app_key.capitalize()} Application"
            desc = f"Applicant: {member_mention}\n\n{answers}"
            msg = await post_embed(
                review_channel,
                title,
                desc,
                footer="Approvers: reply 'yes'/'no' OR react ✅/❌ (first 3 responses or auto-deny after 7 days)."
            )
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            # Save application record
            global next_app_id
            applications[next_app_id] = {
                "id": next_app_id,
                "type": app_key,
                "message_id": msg.id,
                "channel_id": msg.channel.id,
                "guild_id": msg.guild.id,
                "user_id": member_obj.id if member_obj else None,
                "applicant_name": applicant_name,
                "submitted_at": utc_now(),
                "status": "pending",
                "votes": [],  # list of (approver_id, approver_name, 'yes'/'no')
                "decided_by": None,  # approver who tipped the decision (optional)
            }
            next_app_id += 1

        last_sheet_row[app_key] = len(rows)

@tasks.loop(hours=1)
async def sweep_expired_apps():
    """Auto-deny applications pending >= 7 days; DM applicant if possible."""
    for app in list(applications.values()):
        if app["status"] != "pending":
            continue
        if utc_now() - app["submitted_at"] >= timedelta(days=7):
            # Decide based only on existing votes (could be 0,1,2); majority rules; tie -> deny
            yes = sum(1 for _, _, v in app["votes"] if v == "yes")
            no  = sum(1 for _, _, v in app["votes"] if v == "no")
            app["status"] = "approved" if yes > no else "denied"

            # DM and log
            guild = bot.get_guild(app["guild_id"])
            channel = guild.get_channel(app["channel_id"]) if guild else None
            user = guild.get_member(app["user_id"]) if (guild and app["user_id"]) else None

            if user:
                try:
                    if app["status"] == "approved":
                        await user.send(f"✅ Your **{app['type']}** application was approved (auto after 7 days).")
                    else:
                        await user.send(f"❌ Your **{app['type']}** application was denied (auto after 7 days).")
                except discord.Forbidden:
                    pass
            if channel:
                await channel.send(f"⏰ Application auto-decided after 7 days: **{app['type']}** → **{app['status'].upper()}**")

# Handle reactions
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or not reaction.message.guild:
        return
    # Find app by message_id
    app = next((a for a in applications.values() if a["message_id"] == reaction.message.id), None)
    if not app or app["status"] != "pending":
        return
    member = reaction.message.guild.get_member(user.id)
    if not member or not is_approver(member):
        return

    # If this approver already voted, ignore duplicates
    if any(uid == user.id for uid, _, _ in app["votes"]):
        return

    vote = None
    if str(reaction.emoji) == "✅":
        vote = "yes"
    elif str(reaction.emoji) == "❌":
        vote = "no"
    if not vote:
        return

    app["votes"].append((user.id, member.display_name, vote))
    await maybe_finalize_app(reaction.message.channel, app)

# Handle yes/no replies as message replies to the application embed
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # AI mention replies (short; longer if asked)
    if bot.user in message.mentions:
        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if prompt:
            try:
                reply = await ask_openai(prompt)
                await message.reply(reply)
            except Exception as e:
                await message.reply(f"AI error: {e}")

    # Approvals by reply (must be a reply to the app message)
    if message.reference and message.content.lower().strip() in ("yes", "no"):
        try:
            ref = await message.channel.fetch_message(message.reference.message_id)
        except discord.NotFound:
            ref = None
        if ref:
            app = next((a for a in applications.values() if a["message_id"] == ref.id), None)
            if app and app["status"] == "pending":
                member = message.guild.get_member(message.author.id)
                if member and is_approver(member):
                    # ignore duplicate vote by same approver
                    if not any(uid == member.id for uid, _, _ in app["votes"]):
                        app["votes"].append((member.id, member.display_name, "yes" if message.content.lower()=="yes" else "no"))
                        await maybe_finalize_app(message.channel, app)

    await bot.process_commands(message)

async def maybe_finalize_app(channel: discord.TextChannel, app: dict):
    """Finalize when 3 votes reached; otherwise wait up to 7 days."""
    if len(app["votes"]) >= 3:
        yes = sum(1 for _, _, v in app["votes"] if v == "yes")
        no  = sum(1 for _, _, v in app["votes"] if v == "no")
        app["status"] = "approved" if yes > no else "denied"
        # mark who cast the deciding vote (the 3rd)
        app["decided_by"] = app["votes"][-1][1]  # approver display name

        # DM applicant if we know them
        guild = channel.guild
        user = guild.get_member(app["user_id"]) if app["user_id"] else None
        if user:
            try:
                if app["status"] == "approved":
                    await user.send(f"✅ Your **{app['type']}** application was **approved** by **{app['decided_by']}**.")
                else:
                    await user.send(f"❌ Your **{app['type']}** application was **denied** by **{app['decided_by']}**.")
            except discord.Forbidden:
                pass

        await channel.send(f"📣 Application **{app['type']}** decided → **{app['status'].upper()}** "
                           f"(votes: {yes}✅ / {no}❌).")

# /apphistory
@bot.command(name="apphistory")
async def apphistory(ctx, member: discord.Member = None):
    member = member or ctx.author
    records = [a for a in applications.values() if a.get("user_id") == member.id]
    if not records:
        await ctx.send("📭 No application history found.")
        return
    embed = discord.Embed(title=f"Application History for {member.display_name}", color=0x2ecc71)
    for a in sorted(records, key=lambda x: x["submitted_at"]):
        approver = a.get("decided_by") or "—"
        when = a["submitted_at"].astimezone(tz.tzlocal()).strftime("%Y-%m-%d %H:%M")
        status = "⏳ Pending" if a["status"] == "pending" else ("✅ Approved" if a["status"]=="approved" else "❌ Denied")
        embed.add_field(
            name=f"{a['type'].capitalize()} — {status}",
            value=f"Submitted: {when}\nApproved by: {approver}",
            inline=False
        )
    await ctx.send(embed=embed)

# -------------------- LIFECYCLE --------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (guilds: {[g.name for g in bot.guilds]})")
    scheduler_loop.start()
    pull_new_form_responses.start()
    sweep_expired_apps.start()

# -------------------- RUN --------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN env var")
    bot.run(DISCORD_TOKEN)
