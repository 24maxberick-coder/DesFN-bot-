import discord
from discord.ext import commands, tasks
import asyncio
import os

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

# In-memory storage
applications = {}  # {user_id: {"answers": str, "status": str, "approved_by": str}}
reviewed = {}  # {message_id: [approvers]}
app_channel_name = "application-review"
news_channel_name = "roblox-news🌍"

OWNER_ROLES = ["Owner", "Co-Owner", "JRowner"]

# -------------------- NEWS --------------------
@bot.command()
@commands.has_any_role(*OWNER_ROLES)
async def news(ctx, *, announcement: str):
    """Post news into the roblox-news🌍 channel"""
    channel = discord.utils.get(ctx.guild.text_channels, name=news_channel_name)
    if channel:
        await channel.send(f"📰 **NEWS UPDATE:**\n{announcement}")
        await ctx.send("✅ News posted!")
    else:
        await ctx.send("❌ News channel not found.")

# -------------------- APPLY --------------------
@bot.command()
async def apply(ctx, *, answers: str):
    """Submit an application"""
    channel = discord.utils.get(ctx.guild.text_channels, name=app_channel_name)
    if not channel:
        await ctx.send("❌ Application review channel not found.")
        return

    embed = discord.Embed(title="New Application", description=answers, color=discord.Color.blue())
    embed.set_footer(text=f"Applicant: {ctx.author} | ID: {ctx.author.id}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")

    applications[ctx.author.id] = {"answers": answers, "status": "Pending", "approved_by": None}
    reviewed[msg.id] = []

    await ctx.send("✅ Application submitted for review!")

    # Auto-deny after 1 week if no full decision
    async def auto_deny():
        await asyncio.sleep(7 * 24 * 60 * 60)  # 7 days
        if applications[ctx.author.id]["status"] == "Pending":
            applications[ctx.author.id]["status"] = "Denied"
            await ctx.author.send("❌ Your application has been automatically denied (no decision in 1 week).")
    bot.loop.create_task(auto_deny())

# -------------------- REACTION & VOTE --------------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot: return
    if str(reaction.emoji) not in ["✅", "❌"]: return
    if reaction.message.id not in reviewed: return

    member = reaction.message.guild.get_member(user.id)
    if not any(role.name in OWNER_ROLES for role in member.roles): return

    if user.id in reviewed[reaction.message.id]: return  # already voted
    reviewed[reaction.message.id].append(user.id)

    applicant_id = int(reaction.message.embeds[0].footer.text.split("ID: ")[1])
    decision = "Approved" if str(reaction.emoji) == "✅" else "Denied"

    if decision == "Approved":
        applications[applicant_id]["status"] = "Approved"
        applications[applicant_id]["approved_by"] = user.name
        await reaction.message.channel.send(f"✅ Application approved by {user.mention}")
        applicant = await bot.fetch_user(applicant_id)
        await applicant.send(f"🎉 Your application has been **approved** by {user.name}!")
    else:
        applications[applicant_id]["status"] = "Denied"
        applications[applicant_id]["approved_by"] = user.name
        await reaction.message.channel.send(f"❌ Application denied by {user.mention}")
        applicant = await bot.fetch_user(applicant_id)
        await applicant.send(f"❌ Your application has been **denied** by {user.name}.")

# -------------------- MANUAL YES/NO REPLY --------------------
@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.channel.name != app_channel_name: 
        await bot.process_commands(message)
        return

    if message.content.lower() in ["yes", "no"]:
        member = message.guild.get_member(message.author.id)
        if not any(role.name in OWNER_ROLES for role in member.roles): 
            return
        if message.reference and message.reference.message_id in reviewed:
            if message.author.id in reviewed[message.reference.message_id]: return
            reviewed[message.reference.message_id].append(message.author.id)

            applicant_id = int(message.reference.resolved.embeds[0].footer.text.split("ID: ")[1])
            decision = "Approved" if message.content.lower() == "yes" else "Denied"

            if decision == "Approved":
                applications[applicant_id]["status"] = "Approved"
                applications[applicant_id]["approved_by"] = message.author.name
                await message.channel.send(f"✅ Application approved by {message.author.mention}")
                applicant = await bot.fetch_user(applicant_id)
                await applicant.send(f"🎉 Your application has been **approved** by {message.author.name}!")
            else:
                applications[applicant_id]["status"] = "Denied"
                applications[applicant_id]["approved_by"] = message.author.name
                await message.channel.send(f"❌ Application denied by {message.author.mention}")
                applicant = await bot.fetch_user(applicant_id)
                await applicant.send(f"❌ Your application has been **denied** by {message.author.name}!")
    await bot.process_commands(message)

# -------------------- APP HISTORY --------------------
@bot.command()
async def apphistory(ctx, member: discord.Member):
    """Show history of a user's application"""
    app = applications.get(member.id)
    if not app:
        await ctx.send("❌ No application history found.")
        return
    status = app['status']
    approved_by = app['approved_by'] if app['approved_by'] else "N/A"
    await ctx.send(f"📋 Application for {member.mention}\nStatus: **{status}**\nApproved by: **{approved_by}**")

# -------------------- START --------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

bot.run(TOKEN)
