import os
import discord
from discord import app_commands
from discord.ext import tasks
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID")) # YOUR Discord User ID
GUILD_ID = int(os.getenv("GUILD_ID", "0")) # Optional: for faster command sync

# --- DATABASE ---
conn = sqlite3.connect("subs.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, role_id INTEGER)""")
cur.execute("""CREATE TABLE IF NOT EXISTS subs (
    user_id INTEGER,
    guild_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    total_days INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, guild_id)
)""")
conn.commit()

# --- FLASK (For Render) ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is Running - Subscription Bot Pro"
@app.route('/ping')
def ping(): return "OK"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

def is_owner(interaction: discord.Interaction):
    return interaction.user.id == OWNER_ID

async def send_dm(user: discord.User, role_name, start, end, days_left):
    try:
        embed = discord.Embed(title="✅ Subscription Activated!", color=0x00ff99)
        embed.add_field(name="Role Given", value=f"**{role_name}**", inline=False)
        embed.add_field(name="Start Date", value=start.strftime("%d %b %Y %I:%M %p"), inline=True)
        embed.add_field(name="End Date", value=end.strftime("%d %b %Y %I:%M %p"), inline=True)
        embed.add_field(name="Time Left", value=f"**{days_left} Days**", inline=False)
        embed.set_footer(text="You will lose the role automatically after expiry unless renewed.")
        await user.send(embed=embed)
    except:
        pass # DM closed

async def add_subscription(guild: discord.Guild, user: discord.Member, days: int):
    cur.execute("SELECT role_id FROM config WHERE guild_id=?", (guild.id,))
    row = cur.fetchone()
    if not row:
        return False, "Role not configured. Use /config first."

    role_id = row[0]
    role = guild.get_role(role_id)
    if not role:
        return False, "Configured role not found."

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)

    # Check existing sub for continuity
    cur.execute("SELECT end_date, total_days FROM subs WHERE user_id=? AND guild_id=?", (user.id, guild.id))
    existing = cur.fetchone()
    total_days = days
    if existing:
        prev_end = datetime.fromisoformat(existing[0])
        prev_total = existing[1]
        # If resubscribing within 2 days of expiry, continue streak
        if now <= prev_end + timedelta(days=2):
            total_days = prev_total + days
        # else reset handled

    cur.execute("INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?)",
                (user.id, guild.id, now.isoformat(), end.isoformat(), total_days))
    conn.commit()

    await user.add_roles(role, reason=f"Subscription for {days} days")
    await send_dm(user, role.name, now, end, days)
    return True, total_days

@tree.command(name="config", description="Set subscription role")
@app_commands.describe(role="Role to give on subscription")
async def config_cmd(interaction: discord.Interaction, role: discord.Role):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner can use this bot.", ephemeral=True)
    cur.execute("INSERT OR REPLACE INTO config VALUES (?,?)", (interaction.guild.id, role.id))
    conn.commit()
    await interaction.response.send_message(f"✅ Subscription role set to {role.mention}", ephemeral=True)

@tree.command(name="sub_1", description="Give 7 days subscription")
@app_commands.describe(user="User to subscribe")
async def sub1_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, 7)
    if ok:
        await interaction.followup.send(f"✅ {user.mention} subscribed for 7 days. Total streak: {res} days", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="sub_2", description="Give 30 days subscription")
@app_commands.describe(user="User to subscribe")
async def sub2_cmd(interaction: discord.Interaction, user: discord.Member):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, 30)
    if ok:
        await interaction.followup.send(f"✅ {user.mention} subscribed for 30 days. Total streak: {res} days", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="set_sub", description="Give custom days subscription")
@app_commands.describe(user="User to subscribe", days="Number of days")
async def set_sub_cmd(interaction: discord.Interaction, user: discord.Member, days: int):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)
    if days <=0 or days > 365:
        return await interaction.response.send_message("❌ Days must be 1-365", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    ok, res = await add_subscription(interaction.guild, user, days)
    if ok:
        await interaction.followup.send(f"✅ {user.mention} subscribed for {days} days. Total streak: {res} days", ephemeral=True)
    else:
        await interaction.followup.send(f"❌ {res}", ephemeral=True)

@tree.command(name="leaderboard", description="Show subscription leaderboard")
async def leaderboard_cmd(interaction: discord.Interaction):
    if not is_owner(interaction):
        return await interaction.response.send_message("❌ Only owner.", ephemeral=True)

    cur.execute("SELECT user_id, total_days, end_date FROM subs WHERE guild_id=? ORDER BY total_days DESC LIMIT 15", (interaction.guild.id,))
    rows = cur.fetchall()
    if not rows:
        return await interaction.response.send_message("No subscribers yet.", ephemeral=True)

    embed = discord.Embed(title="🏆 Subscription Leaderboard", color=0xffd700)
    desc = ""
    for i, (uid, total, end_iso) in enumerate(rows, 1):
        try:
            end = datetime.fromisoformat(end_iso)
            left = (end - datetime.now(timezone.utc)).days
            left = max(0, left)
            status = f"{left} days left" if left>0 else "Expired"
            desc += f"**{i}.** <@{uid}> — **{total} days** total | {status}\n"
        except:
            desc += f"**{i}.** <@{uid}> — **{total} days**\n"

    embed.description = desc
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if GUILD_ID!= 0:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print("Commands synced")
    check_expiry.start()

@tasks.loop(minutes=1)
async def check_expiry():
    now = datetime.now(timezone.utc)
    cur.execute("SELECT user_id, guild_id, end_date FROM subs")
    rows = cur.fetchall()
    for user_id, guild_id, end_iso in rows:
        end = datetime.fromisoformat(end_iso)
        if now >= end:
            guild = bot.get_guild(guild_id)
            if not guild: continue
            cur.execute("SELECT role_id FROM config WHERE guild_id=?", (guild_id,))
            conf = cur.fetchone()
            if not conf: continue
            role = guild.get_role(conf[0])
            member = guild.get_member(user_id)
            try:
                if member and role in member.roles:
                    await member.remove_roles(role, reason="Subscription expired")
                    try:
                        await member.send(f"❌ Your **{role.name}** subscription has expired. Contact owner to renew.")
                    except: pass
                # Keep total_days for leaderboard but remove active sub? We keep record, but you can delete if you want
                # cur.execute("DELETE FROM subs WHERE user_id=? AND guild_id=?", (user_id, guild_id))
                # conn.commit()
            except Exception as e:
                print(f"Expiry error: {e}")

# --- START BOTH ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
