# FIX FOR PYTHON 3.13
try:
    import audioop
except ModuleNotFoundError:
    import audioop_lts as audioop
    import sys
    sys.modules['audioop'] = audioop

import os
import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# --- DATABASE ---
conn = sqlite3.connect("subs.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, role_id INTEGER)")
cur.execute("""CREATE TABLE IF NOT EXISTS subs (
    user_id INTEGER,
    guild_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    total_seconds INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, guild_id)
)""")
conn.commit()

# --- FLASK FOR RENDER ---
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Bot Running - $ prefix"
@flask_app.route('/ping')
def ping(): return "OK"
def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# --- BOT WITH $ PREFIX ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

def owner_only():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

async def send_dm(user, role_name, start, end, duration_text):
    try:
        embed = discord.Embed(title="✅ Subscription Activated!", color=0x00ff99)
        embed.add_field(name="Role Given", value=f"**{role_name}**", inline=False)
        embed.add_field(name="Start", value=start.strftime("%d %b %Y %I:%M:%S %p UTC"), inline=True)
        embed.add_field(name="End", value=end.strftime("%d %b %Y %I:%M:%S %p UTC"), inline=True)
        embed.add_field(name="Duration", value=f"**{duration_text}**", inline=False)
        embed.set_footer(text="Role will be removed automatically after expiry.")
        await user.send(embed=embed)
        return True
    except:
        return False

async def add_subscription(guild, member, seconds: int, label: str):
    cur.execute("SELECT role_id FROM config WHERE guild_id=?", (guild.id,))
    row = cur.fetchone()
    if not row: return False, "Use `$config @role` first."
    role = guild.get_role(row[0])
    if not role: return False, "Configured role not found."

    if role >= guild.me.top_role:
        return False, "My role is below subscription role. Move my bot role higher!"

    now = datetime.now(timezone.utc)
    end = now + timedelta(seconds=seconds)

    # Continuity check
    cur.execute("SELECT end_date, total_seconds FROM subs WHERE user_id=? AND guild_id=?", (member.id, guild.id))
    existing = cur.fetchone()
    total = seconds
    if existing:
        prev_end = datetime.fromisoformat(existing[0])
        if now <= prev_end + timedelta(days=2):
            total = existing[1] + seconds

    cur.execute("INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?)",
                (member.id, guild.id, now.isoformat(), end.isoformat(), total))
    conn.commit()

    try:
        await member.add_roles(role, reason=f"Sub {label}")
    except Exception as e:
        return False, f"Failed to add role: {e}"

    await send_dm(member, role.name, now, end, label)
    return True, total

# --- COMMANDS ---

@bot.command(name="config")
@owner_only()
async def config_cmd(ctx, role: discord.Role):
    cur.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (ctx.guild.id, role.id))
    conn.commit()
    await ctx.send(f"✅ Subscription role set to {role.mention}")

@bot.command(name="sub_1")
@owner_only()
async def sub1_cmd(ctx, member: discord.Member):
    # 7 days in seconds
    seconds = 7 * 24 * 3600
    ok, res = await add_subscription(ctx.guild, member, seconds, "7 Days")
    if ok: await ctx.send(f"✅ {member.mention} subscribed for 7 days. Total: {res//3600} hours")
    else: await ctx.send(f"❌ {res}")

@bot.command(name="sub_2")
@owner_only()
async def sub2_cmd(ctx, member: discord.Member):
    # 30 days in seconds
    seconds = 30 * 24 * 3600
    ok, res = await add_subscription(ctx.guild, member, seconds, "30 Days")
    if ok: await ctx.send(f"✅ {member.mention} subscribed for 30 days. Total: {res//3600} hours")
    else: await ctx.send(f"❌ {res}")

@bot.command(name="set_sub")
@owner_only()
async def set_sub_cmd(ctx, member: discord.Member, seconds: int):
    """ $set_sub @user 60 -> 60 seconds """
    if seconds <= 0 or seconds > 31536000: # max 1 year
        return await ctx.send("❌ Seconds must be 1 to 31536000 (1 year)")
    ok, res = await add_subscription(ctx.guild, member, seconds, f"{seconds} Seconds")
    if ok:
        await ctx.send(f"✅ {member.mention} subscribed for **{seconds} seconds**. Ends <t:{int((datetime.now(timezone.utc)+timedelta(seconds=seconds)).timestamp())}:R>")
    else:
        await ctx.send(f"❌ {res}")

@bot.command(name="leaderboard")
@owner_only()
async def leaderboard_cmd(ctx):
    cur.execute("SELECT user_id, total_seconds, end_date FROM subs WHERE guild_id=? ORDER BY total_seconds DESC LIMIT 10", (ctx.guild.id,))
    rows = cur.fetchall()
    if not rows: return await ctx.send("No subscribers yet.")
    embed = discord.Embed(title="🏆 Subscription Leaderboard", color=0xffd700)
    desc=""
    for i,(uid,total_sec,end_iso) in enumerate(rows,1):
        end=datetime.fromisoformat(end_iso)
        left = (end - datetime.now(timezone.utc)).total_seconds()
        left = max(0,int(left))
        status = f"🟢 {left}s left" if left>0 else "🔴 Expired"
        # Show continuous time nicely
        if total_sec >= 86400:
            total_str = f"{total_sec//86400}d {total_sec%86400//3600}h"
        elif total_sec >= 3600:
            total_str = f"{total_sec//3600}h"
        else:
            total_str = f"{total_sec}s"
        desc+=f"**{i}.** <@{uid}> — **{total_str}** total | {status}\n"
    embed.description=desc
    await ctx.send(embed=embed)

@bot.command(name="sync")
@owner_only()
async def sync_cmd(ctx):
    # For $ prefix, sync is not needed, but this will sync any old slash commands and clear them
    try:
        # Clear old slash commands if any
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        await ctx.send("✅ Cleared old slash commands. Now using $ prefix only.")
    except Exception as e:
        await ctx.send(f"Sync error: {e}")

@bot.command(name="help")
@owner_only()
async def help_cmd(ctx):
    embed=discord.Embed(title="Subscription Bot - $ Commands", color=0x00ff99)
    embed.add_field(name="$config @role", value="Set subscription role", inline=False)
    embed.add_field(name="$sub_1 @user", value="Give 7 days sub", inline=False)
    embed.add_field(name="$sub_2 @user", value="Give 30 days sub", inline=False)
    embed.add_field(name="$set_sub @user <seconds>", value="Ex: `$set_sub @user 60` = 60 seconds", inline=False)
    embed.add_field(name="$leaderboard", value="Show leaderboard", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} | Prefix: $")
    check_expiry.start()

@tasks.loop(seconds=10) # Check every 10 sec because you use seconds
async def check_expiry():
    now=datetime.now(timezone.utc)
    cur.execute("SELECT user_id, guild_id, end_date FROM subs")
    for uid,gid,end_iso in cur.fetchall():
        end=datetime.fromisoformat(end_iso)
        if now >= end:
            guild=bot.get_guild(gid)
            if not guild: continue
            cur.execute("SELECT role_id FROM config WHERE guild_id=?", (gid,))
            conf=cur.fetchone()
            if not conf: continue
            role=guild.get_role(conf[0])
            member=guild.get_member(uid)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Sub expired")
                    try: await member.send(f"❌ Your **{role.name}** expired in **{guild.name}**.")
                    except: pass
                    print(f"Removed role from {uid}")
                except Exception as e:
                    print(e)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Only owner can use this bot.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing args. Use `$help`")
    else:
        print(error)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
