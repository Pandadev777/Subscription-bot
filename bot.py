# FIX FOR PYTHON 3.13 - audioop removed
try:
    import audioop
except ModuleNotFoundError:
    import audioop_lts as audioop
    import sys
    sys.modules['audioop'] = audioop

import os
import discord
from discord import app_commands
from discord.ext import tasks
import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
GUILD_ID = int(os.getenv("GUILD_ID", "0")) if os.getenv("GUILD_ID") else None

# --- DATABASE ---
conn = sqlite3.connect("subs.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, role_id INTEGER)")
cur.execute("""CREATE TABLE IF NOT EXISTS subs (
    user_id INTEGER,
    guild_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    total_days INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, guild_id)
)""")
conn.commit()

# --- FLASK FOR RENDER ---
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Subscription Bot is Online ✅"
@flask_app.route('/ping')
def ping(): return "OK"
def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

def is_owner(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

async def send_dm(user, role_name, start, end, days_left):
    try:
        embed = discord.Embed(title="✅ Subscription Activated!", color=0x00ff99)
        embed.add_field(name="Role Given", value=f"**{role_name}**", inline=False)
        embed.add_field(name="Start", value=start.strftime("%d %b %Y %I:%M %p UTC"), inline=True)
        embed.add_field(name="End", value=end.strftime("%d %b %Y %I:%M %p UTC"), inline=True)
        embed.add_field(name="Duration", value=f"**{days_left} Days**", inline=False)
        embed.set_footer(text="Role will be removed automatically after expiry.")
        await user.send(embed=embed)
        return True
    except Exception as e:
        print(f"DM failed for {user}: {e}")
        return False

async def add_subscription(guild, member, days):
    cur.execute("SELECT role_id FROM config WHERE guild_id=?", (guild.id,))
    row = cur.fetchone()
    if not row: return False, "Use /config first to set role."
    role = guild.get_role(row[0])
    if not role: return False, "Configured role not found. Set again."

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    cur.execute("SELECT end_date, total_days FROM subs WHERE user_id=? AND guild_id=?", (member.id, guild.id))
    existing = cur.fetchone()
    total_days = days
    if existing:
        prev_end = datetime.fromisoformat(existing[0])
        if now <= prev_end + timedelta(days=2): # continuous streak
            total_days = existing[1] + days

    cur.execute("INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?)",
                (member.id, guild.id, now.isoformat(), end.isoformat(), total_days))
    conn.commit()
    try:
        await member.add_roles(role, reason=f"Sub {days}d")
    except Exception as e:
        return False, f"Failed to add role: {e} - Move bot role higher!"

    await send_dm(member, role.name, now, end, days)
    return True, total_days

# --- COMMANDS ---

@tree.command(name="sync", description="Sync commands (Owner only)")
async def sync_cmd(interaction: discord.Interaction):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner can use this.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            await interaction.followup.send(f"✅ Synced {len(synced)} commands to guild {GUILD_ID} (Instant)", ephemeral=True)
        else:
            synced = await tree.sync()
            await interaction.followup.send(f"✅ Synced {len(synced)} commands globally. Global takes 1 hour to update.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)

@tree.command(name="config", description="Set the subscription role")
@app_commands.describe(role="Role to give")
async def config_cmd(interaction: discord.Interaction, role: discord.Role):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ My role is below that role. Move my role higher in Server Settings > Roles.", ephemeral=True)
    cur.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (interaction.guild.id, role.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Subscription role set to {role.mention}", ephemeral=True)

@tree.command(name="sub_1", description="Give 7 days subscription")
@app_commands.describe(user="User to subscribe")
async def sub1(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, 7)
    if ok: await interaction.followup.send(f"✅ {user.mention} got 7 days. Streak: {res} days", ephemeral=True)
    else: await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="sub_2", description="Give 30 days subscription")
@app_commands.describe(user="User to subscribe")
async def sub2(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, 30)
    if ok: await interaction.followup.send(f"✅ {user.mention} got 30 days. Streak: {res} days", ephemeral=True)
    else: await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="set_sub", description="Give custom days subscription")
@app_commands.describe(user="User", days="Days 1-365")
async def set_sub(interaction: discord.Interaction, user: discord.Member, days: int):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    if not 1 <= days <= 365:
        return await interaction.response.send_message("❌ Days must be 1-365", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, days)
    if ok: await interaction.followup.send(f"✅ {user.mention} got {days} days. Streak: {res} days", ephemeral=True)
    else: await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="leaderboard", description="Show subscription leaderboard")
async def leaderboard(interaction: discord.Interaction):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    cur.execute("SELECT user_id, total_days, end_date FROM subs WHERE guild_id=? ORDER BY total_days DESC LIMIT 10", (interaction.guild.id,))
    rows = cur.fetchall()
    if not rows: return await interaction.response.send_message("No subs yet.", ephemeral=True)
    embed = discord.Embed(title="🏆 Subscription Leaderboard", color=0xffd700)
    desc=""
    for i,(uid,total,end_iso) in enumerate(rows,1):
        end=datetime.fromisoformat(end_iso)
        left=max(0,(end-datetime.now(timezone.utc)).days)
        status=f"🟢 {left}d left" if left>0 else "🔴 Expired"
        desc+=f"**{i}.** <@{uid}> — **{total}d** total | {status}\n"
    embed.description=desc
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in {bot.user}")
    # Auto sync on startup
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
            print(f"Synced to guild {GUILD_ID}")
        else:
            await tree.sync()
            print("Synced globally")
    except Exception as e:
        print(f"Sync error: {e}")
    check_expiry.start()

@tasks.loop(minutes=1)
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
                except Exception as e: print(e)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
