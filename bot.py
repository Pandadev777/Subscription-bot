# FIX FOR PYTHON 3.13 RENDER ERROR
try:
    import audioop
except ModuleNotFoundError:
    import audioop_lts as audioop
    import sys
    sys.modules['audioop'] = audioop

import os
import discord
from discord.ext import commands, tasks
from discord import ui
import sqlite3
from datetime import datetime, timedelta, timezone
from flask import Flask
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))

# --- DATABASE ---
conn = sqlite3.connect("subs.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS config (guild_id INTEGER PRIMARY KEY, role_id INTEGER, perm_role_id INTEGER)")
cur.execute("""CREATE TABLE IF NOT EXISTS subs (
    user_id INTEGER,
    guild_id INTEGER,
    start_date TEXT,
    end_date TEXT,
    total_seconds INTEGER DEFAULT 0,
    is_perm INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, guild_id)
)""")
conn.commit()

# --- FLASK FOR RENDER ---
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Bot Online - All Commands Loaded"
@flask_app.route('/ping')
def ping(): return "OK"
def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# --- BOT SETUP ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

def owner_only():
    async def predicate(ctx):
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)

# --- TICKET VIEW WITH 3 BUTTONS ---
class SubscriptionPanel(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def create_ticket(self, interaction: discord.Interaction, name_prefix: str, label: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None

        if not category or not isinstance(category, discord.CategoryChannel):
            return await interaction.followup.send(f"❌ TICKET_CATEGORY_ID not set! Add it in Render ENV. Current: {TICKET_CATEGORY_ID}", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        owner = guild.get_member(OWNER_ID)
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)

        channel_name = f"{name_prefix}-{interaction.user.name}"[:90].lower().replace(" ", "-")
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"UserID:{interaction.user.id}|Type:{label}"
        )

        embed = discord.Embed(title=f"{label} Ticket Opened", color=0x00ff99, description=f"Hello {interaction.user.mention}\n\nYou requested **{label}**.\nStaff will be here soon.")
        embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})")
        embed.add_field(name="Type", value=label)
        await ticket_channel.send(content=f"{interaction.user.mention} <@{OWNER_ID}>", embed=embed)
        await interaction.followup.send(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)

    @ui.button(label="Permanent Role", style=discord.ButtonStyle.success, custom_id="perm_sub_ticket_btn", emoji="♾️")
    async def perm_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.create_ticket(interaction, "perm-sub-ticket", "Permanent Role")

    @ui.button(label="7 Days Role", style=discord.ButtonStyle.primary, custom_id="7d_sub_ticket_btn", emoji="📅")
    async def days7_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.create_ticket(interaction, "7d-sub-ticket", "7 Days Role")

    @ui.button(label="30 Days Role", style=discord.ButtonStyle.secondary, custom_id="30d_sub_ticket_btn", emoji="💎")
    async def days30_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.create_ticket(interaction, "30d-sub-ticket", "30 Days Role")

async def send_dm(user, role_name, start, end, duration_text):
    try:
        embed = discord.Embed(title="✅ Subscription Activated!", color=0x00ff99)
        embed.add_field(name="Role Given", value=f"**{role_name}**", inline=False)
        embed.add_field(name="Start", value=start.strftime("%d %b %Y %I:%M:%S UTC"), inline=True)
        embed.add_field(name="End", value=end.strftime("%d %b %Y %I:%M:%S UTC") if end else "Never (Permanent)", inline=True)
        embed.add_field(name="Duration", value=f"**{duration_text}**", inline=False)
        embed.add_field(name="Time Left", value=f"<t:{int(end.timestamp())}:R>" if end else "♾️ Permanent", inline=False)
        embed.set_footer(text="Role will be removed automatically after expiry.")
        await user.send(embed=embed)
    except: pass

async def add_subscription(guild, member, seconds, label, is_perm=False):
    cur.execute("SELECT role_id FROM config WHERE guild_id=?", (guild.id,))
    row = cur.fetchone()
    if not row: return False, "Use $config @role first."
    role = guild.get_role(row[0])
    if not role: return False, "Configured role not found."
    if role >= guild.me.top_role:
        return False, "My bot role is below sub role! Move bot role higher in Server Settings > Roles."

    now = datetime.now(timezone.utc)
    end = None if is_perm else now + timedelta(seconds=seconds)
    total = 0 if is_perm else seconds

    cur.execute("SELECT total_seconds FROM subs WHERE user_id=? AND guild_id=?", (member.id, guild.id))
    existing = cur.fetchone()
    if existing and not is_perm:
        total = existing[0] + seconds

    cur.execute("INSERT OR REPLACE INTO subs VALUES (?,?,?,?,?,?)",
                (member.id, guild.id, now.isoformat(), end.isoformat() if end else "PERM", total, 1 if is_perm else 0))
    conn.commit()

    try: await member.add_roles(role, reason=label)
    except Exception as e: return False, f"Failed to add role: {e}"

    await send_dm(member, role.name, now, end, label)
    return True, total

# --- ALL COMMANDS ---

@bot.command(name="config")
@owner_only()
async def config_cmd(ctx, role: discord.Role):
    cur.execute("INSERT OR REPLACE INTO config VALUES (?,?,?)", (ctx.guild.id, role.id, role.id))
    conn.commit()
    await ctx.send(f"✅ Subscription role set to {role.mention} | Bot ready!")

@bot.command(name="sub_1")
@owner_only()
async def sub1_cmd(ctx, member: discord.Member):
    ok, res = await add_subscription(ctx.guild, member, 7*24*3600, "7 Days")
    await ctx.send(f"✅ {member.mention} subscribed for **7 Days**" if ok else f"❌ {res}")

@bot.command(name="sub_2")
@owner_only()
async def sub2_cmd(ctx, member: discord.Member):
    ok, res = await add_subscription(ctx.guild, member, 30*24*3600, "30 Days")
    await ctx.send(f"✅ {member.mention} subscribed for **30 Days**" if ok else f"❌ {res}")

@bot.command(name="perm_sub")
@owner_only()
async def perm_sub_cmd(ctx, member: discord.Member):
    ok, res = await add_subscription(ctx.guild, member, 0, "Permanent Role", is_perm=True)
    await ctx.send(f"✅ {member.mention} got **Permanent Role** ♾️" if ok else f"❌ {res}")

@bot.command(name="set_sub")
@owner_only()
async def set_sub_cmd(ctx, member: discord.Member, seconds: int):
    """ Only seconds - Ex: $set_sub @user 60 """
    if not 1 <= seconds <= 31536000:
        return await ctx.send("❌ Seconds must be between 1 and 31536000 (1 year max)")
    ok, _ = await add_subscription(ctx.guild, member, seconds, f"{seconds} Seconds")
    if ok:
        end_ts = int((datetime.now(timezone.utc)+timedelta(seconds=seconds)).timestamp())
        await ctx.send(f"✅ {member.mention} subscribed for **{seconds} seconds** - Ends <t:{end_ts}:R>")
    else:
        await ctx.send(f"❌ {_}")

@bot.command(name="remove_sub")
@owner_only()
async def remove_sub_cmd(ctx, member: discord.Member):
    cur.execute("SELECT role_id FROM config WHERE guild_id=?", (ctx.guild.id,))
    row = cur.fetchone()
    if not row: return await ctx.send("❌ Config not set. Use $config first")
    role = ctx.guild.get_role(row[0])
    cur.execute("DELETE FROM subs WHERE user_id=? AND guild_id=?", (member.id, ctx.guild.id))
    conn.commit()
    try:
        if role and role in member.roles:
            await member.remove_roles(role, reason="Subscription removed manually")
        try: await member.send(f"❌ Your subscription **{role.name if role else 'Receiver'}** was removed in **{ctx.guild.name}**")
        except: pass
        await ctx.send(f"✅ Removed subscription from {member.mention} and role taken")
    except Exception as e:
        await ctx.send(f"Removed from DB but role failed: {e}")

@bot.command(name="panel")
@owner_only()
async def panel_cmd(ctx):
    embed = discord.Embed(title="🎫 Subscription Ticket Panel", description="Click a button below to open a ticket for your desired subscription!", color=0x2f3136)
    embed.add_field(name="♾️ Permanent", value="Lifetime access", inline=True)
    embed.add_field(name="📅 7 Days", value="Weekly access", inline=True)
    embed.add_field(name="💎 30 Days", value="Monthly access", inline=True)
    embed.set_footer(text="Tickets: perm-sub-ticket, 7d-sub-ticket, 30d-sub-ticket")
    await ctx.send(embed=embed, view=SubscriptionPanel())

@bot.command(name="buy")
async def buy_cmd(ctx):
    embed = discord.Embed(title="🛒 RECEIVER ROLE SHOP | PREMIUM ACCESS", description="**Upgrade your grind with Receiver Roles! Dominate every hit.**", color=0xFFD700)
    embed.add_field(name="♾️ Permanent Receiver Role", value="```Price: 3B+ Value Fruit```\n✓ **Lifetime Access**\n✓ **Max Priority Hits**\n✓ **Best Value - Highest ROI**", inline=False)
    embed.add_field(name="📅 7 Days Receiver Role", value="```Price: 600M+ Value Fruit```\n✓ **7 Days Full Access**\n✓ **Starter Pack**", inline=True)
    embed.add_field(name="💎 30 Days Receiver Role", value="```Price: 1B+ Value Fruit```\n✓ **30 Days Full Access**\n✓ **High Priority**\n✓ **Most Popular**", inline=True)
    embed.add_field(name="\u200b", value="━━━━━━━━━━━━━━ NOTE ━━━━━━━━━━━━━━", inline=False)
    embed.add_field(name=" ", value="> **> Once the fruit is given, Subscription will be enabled ASAP**\n> **> Receiving methods will be taught you personally!**\n> **> We guarantee hits will arrive faster & higher value than you paid!**\n> **> You must follow Receiver role rules**", inline=False)
    embed.add_field(name="\u200b", value="━━━━━━━━━━━━━━ RULES ━━━━━━━━━━━━━━", inline=False)
    embed.add_field(name="📜 Receiver Rules", value="**1.** Claiming hit without announcing = **WARN**\n**2.** Not posting hit screenshot = **WARN**\n**3.** Not giving split to hitter = **DEMOTE** (ur username gets removed from script itself)\n**4.** IF YOU TAKE ANY HIT U MUST SAY **\"ME\"** BEFORE TAKING OR YOU WILL GET **WARN!**\n**5.** IF A RECEIVER HAS NOT TAKEN EVEN A SINGLE HIT FROM A WEEK WILL NOT GET ANY SHARE\n\n> **⚠️ If anyone gets 5 warns, u will get demoted or else kick.**", inline=False)
    embed.set_footer(text="💰 Invest in hits, Earn more | Click below to open ticket")
    await ctx.send(embed=embed, view=SubscriptionPanel())

@bot.command(name="leaderboard")
@owner_only()
async def leaderboard_cmd(ctx):
    cur.execute("SELECT user_id, total_seconds, end_date, is_perm FROM subs WHERE guild_id=? ORDER BY is_perm DESC, total_seconds DESC LIMIT 15", (ctx.guild.id,))
    rows = cur.fetchall()
    if not rows: return await ctx.send("No subscribers yet.")
    embed = discord.Embed(title="🏆 Subscription Leaderboard", description="Top continuous subscribers", color=0xffd700)
    desc=""
    for i,(uid,total,end_iso,is_perm) in enumerate(rows,1):
        if is_perm:
            desc+=f"**{i}.** <@{uid}> — **♾️ PERMANENT**\n"
        else:
            try:
                end=datetime.fromisoformat(end_iso)
                left=max(0,int((end-datetime.now(timezone.utc)).total_seconds()))
                if left >= 86400: left_str = f"{left//86400}d"
                elif left >= 3600: left_str = f"{left//3600}h"
                else: left_str = f"{left}s"
                status=f"🟢 {left_str} left" if left>0 else "🔴 Expired"
                total_str = f"{total//86400}d" if total>=86400 else f"{total//3600}h" if total>=3600 else f"{total}s"
                desc+=f"**{i}.** <@{uid}> — **{total_str}** total | {status}\n"
            except:
                desc+=f"**{i}.** <@{uid}> — **{total}s**\n"
    embed.description=desc
    await ctx.send(embed=embed)

@bot.command(name="help")
async def help_cmd(ctx):
    embed=discord.Embed(title="💎 Subscription Bot - $ Commands", color=0x00ff99)
    embed.add_field(name="OWNER ONLY", value="`$config @role`\n`$sub_1 @user` - 7 days\n`$sub_2 @user` - 30 days\n`$perm_sub @user` - Permanent\n`$set_sub @user <seconds>` - Custom seconds\n`$remove_sub @user`\n`$panel` - Ticket panel\n`$leaderboard`\n`$sync`", inline=False)
    embed.add_field(name="FOR EVERYONE", value="`$buy` - Shop info\nButtons - Open tickets", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="sync")
@owner_only()
async def sync_cmd(ctx):
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        await ctx.send("✅ Cleared old slash commands. Now using $ prefix only.")
    except Exception as e:
        await ctx.send(f"Sync error: {e}")

@bot.event
async def on_ready():
    print(f"Bot {bot.user} Ready!")
    bot.add_view(SubscriptionPanel())
    check_expiry.start()

@tasks.loop(seconds=10)
async def check_expiry():
    now=datetime.now(timezone.utc)
    cur.execute("SELECT user_id, guild_id, end_date FROM subs WHERE is_perm=0")
    for uid,gid,end_iso in cur.fetchall():
        if end_iso=="PERM": continue
        try:
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
                    await member.remove_roles(role, reason="Sub expired")
                    try: await member.send(f"❌ Your **{role.name}** subscription expired in **{guild.name}**")
                    except: pass
                cur.execute("DELETE FROM subs WHERE user_id=? AND guild_id=?", (uid,gid))
                conn.commit()
        except Exception as e:
            print(f"Expiry error: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("❌ Only owner can use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Use `$help` - {error.param}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument. Mention user/role correctly.")
    else:
        print(error)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(TOKEN)
