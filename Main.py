import os
import discord
import requests
import openai
from discord.ext import commands, tasks
from collections import deque

# ---- Setup ----
TOKEN = os.getenv("1406335185376510074")
OPENAI_KEY = os.getenv("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")
NEWS_API_KEY = os.getenv("5e467efe7e0a4705aecddea9f8aecb0b")  # optional, use https://newsapi.org
NEWS_GUILD = os.getenv("Devlonix Studios", "My News Server")
NEWS_CHANNEL = os.getenv("roblox-news🌍", "news")

openai.api_key = OPENAI_KEY

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---- Memory per guild ----
guild_memory = {}  # {guild_id: deque([...])}
news_seen = set()  # track seen news titles

# ---- ChatGPT function ----
def chatgpt_response(messages, user_msg):
    conversation = [{"role": "system", "content": "You are a helpful Discord assistant."}]
    for msg in messages:
        conversation.append({"role": "user", "content": msg})
    conversation.append({"role": "user", "content": user_msg})

    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=conversation,
        max_tokens=300,
    )
    return resp["choices"][0]["message"]["content"].strip()

# ---- News Fetch ----
def fetch_news():
    url = f"https://newsapi.org/v2/top-headlines?country=us&apiKey={NEWS_API_KEY}"
    r = requests.get(url).json()
    articles = r.get("articles", [])
    fresh_news = []
    for a in articles:
        title = a["title"]
        if title not in news_seen:
            news_seen.add(title)
            fresh_news.append(f"📰 **{title}**\n{a['url']}")
    return fresh_news if fresh_news else ["No new news right now."]

# ---- Bot Events ----
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    post_news.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild_id = message.guild.id
    if guild_id not in guild_memory:
        guild_memory[guild_id] = deque(maxlen=100)

    guild_memory[guild_id].append(message.content)

    # If this is the news guild, ignore normal chat
    if message.guild.name == NEWS_GUILD:
        return

    # ChatGPT reply
    if bot.user.mentioned_in(message):
        history = list(guild_memory[guild_id])
        reply = chatgpt_response(history, message.content)
        await message.channel.send(reply)

    await bot.process_commands(message)

# ---- Tasks ----
@tasks.loop(minutes=30)
async def post_news():
    for guild in bot.guilds:
        if guild.name == NEWS_GUILD:
            channel = discord.utils.get(guild.text_channels, name=NEWS_CHANNEL)
            if channel:
                news_list = fetch_news()
                for n in news_list:
                    await channel.send(n)

# ---- Run ----
bot.run(TOKEN)
