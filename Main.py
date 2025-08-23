import discord
from discord.ext import commands
from difflib import get_close_matches
import json
import os

# ---------------- CONFIG ---------------- #
BOT_OWNER_NAME = "DesFN"  # Change to your Discord username
CONFIG_FILE = "server_config.json"

# ---------------- BOT SETUP -------------- #
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True  # required for listening to messages
bot = commands.Bot(command_prefix="!", intents=intents)

# Load saved config
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "r") as f:
        server_config = json.load(f)
else:
    server_config = {}

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(server_config, f, indent=4)

# ---------------- EVENTS ----------------- #
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_guild_join(guild):
    """When bot joins a new server → ask owner which roles to track + news channel"""
    owner = discord.utils.get(bot.get_all_members(), name=BOT_OWNER_NAME)
    if not owner:
        return
    
    roles_list = [role.name for role in guild.roles if role.name != "@everyone"]
    channels_list = [channel.name for channel in guild.text_channels]

    msg = f"👋 Joined **{guild.name}**\n\n**Roles:** {roles_list}\n\nWhich roles should I track? (comma separated)"
    await owner.send(msg)

    def check_role(m):
        return m.author == owner and isinstance(m.channel, discord.DMChannel)

    try:
        reply = await bot.wait_for("message", check=check_role, timeout=120)
        requested_roles = [r.strip() for r in reply.content.split(",")]

        chosen_roles = {}
        for r in requested_roles:
            match = get_close_matches(r, roles_list, n=1, cutoff=0.3)
            if match:
                chosen_roles[r] = match[0]

        server_config[str(guild.id)] = {
            "roles": chosen_roles,
            "news_channel": None,
            "features": { "approvals": True, "logging": True }  # default features
        }
        save_config()

        # Ask about news channel
        await owner.send("Do you want a news channel? (reply with name or `no`)")
        reply2 = await bot.wait_for("message", check=check_role, timeout=60)
        if reply2.content.lower() != "no":
            match = get_close_matches(reply2.content, channels_list, n=1, cutoff=0.3)
            if match:
                server_config[str(guild.id)]["news_channel"] = match[0]
                save_config()

    except Exception as e:
        await owner.send(f"⚠️ Setup failed: {e}")

# ---------------- ADAPTIVE SETTINGS -------------- #
@bot.event
async def on_message(message):
    """Bot adapts when owner says 'don’t do this'"""
    if message.author == bot.user:
        return
    
    guild_id = str(message.guild.id) if message.guild else None

    if message.mentions and bot.user in message.mentions:
        if BOT_OWNER_NAME.lower() in message.author.name.lower():
            content = message.content.lower()
            if "don’t do" in content or "don't do" in content:
                # Example: "@DesFN Bot I don't want you to do approvals"
                for feature in server_config.get(guild_id, {}).get("features", {}):
                    if feature in content:
                        server_config[guild_id]["features"][feature] = False
                        save_config()
                        await message.channel.send(f"✅ Disabled `{feature}` feature as requested.")
                        return
            elif "do" in content:
                # Example: "@DesFN Bot do logging"
                for feature in server_config.get(guild_id, {}).get("features", {}):
                    if feature in content:
                        server_config[guild_id]["features"][feature] = True
                        save_config()
                        await message.channel.send(f"✅ Enabled `{feature}` feature as requested.")
                        return
    
    await bot.process_commands(message)

# ---------------- RUN -------------------- #
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")  # put your key in .env or env var
    if not TOKEN:
        print("❌ No DISCORD_TOKEN found. Set it as an environment variable.")
    else:
        bot.run(TOKEN)
