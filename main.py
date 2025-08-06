import discord
from discord.ext import commands
import aiosqlite
from bs4 import BeautifulSoup
import re
from playwright.async_api import async_playwright
import asyncio

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database setup
async def init_db():
    async with aiosqlite.connect("linked_profiles.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS linked_profiles (
                discord_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                player_id TEXT NOT NULL
            )
        """)
        await db.commit()

@bot.event
async def on_ready():
    await init_db()
    print(f"Logged in as {bot.user}")

# Admin-only command to link a profile for another user
@bot.command()
@commands.has_permissions(administrator=True)
async def forcelink(ctx, member: discord.Member, profile_url: str):
    try:
        parts = profile_url.strip('/').split('/')
        profile_index = parts.index("player")
        platform = parts[profile_index + 1]
        user_id = parts[profile_index + 2]

        discord_id = str(member.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            await db.execute(
                "REPLACE INTO linked_profiles (discord_id, platform, player_id) VALUES (?, ?, ?)",
                (discord_id, platform, user_id)
            )
            await db.commit()

        await ctx.send(f"✅ Linked `{member.display_name}` to `{platform}/{user_id}`.")
    except Exception as e:
        await ctx.send(f"❌ Error force-linking profile: {e}")

@bot.command()
async def link(ctx, profile_url: str):
    try:
        parts = profile_url.strip('/').split('/')
        profile_index = parts.index("player")
        platform = parts[profile_index + 1]
        user_id = parts[profile_index + 2]

        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            await db.execute(
                "REPLACE INTO linked_profiles (discord_id, platform, player_id) VALUES (?, ?, ?)",
                (discord_id, platform, user_id)
            )
            await db.commit()

        await ctx.send(f"✅ Linked to `{platform}/{user_id}`.")
    except Exception as e:
        await ctx.send(f"❌ Error linking profile: {e}")

# Admin-only command to unlink a profile
@bot.command()
@commands.has_permissions(administrator=True)
async def unlink(ctx, member: discord.Member):
    try:
        discord_id = str(member.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            await db.execute("DELETE FROM linked_profiles WHERE discord_id = ?", (discord_id,))
            await db.commit()

        await ctx.send(f"🗑️ Unlinked profile for {member.display_name}.")
    except Exception as e:
        await ctx.send(f"❌ Error unlinking profile: {e}")

@bot.command()
@commands.has_permissions(administrator=True)
async def listlinks(ctx):
    try:
        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT discord_id, platform, player_id FROM linked_profiles") as cursor:
                rows = await cursor.fetchall()

        if not rows:
            await ctx.send("❌ No linked profiles found.")
            return

        lines = []
        for discord_id, platform, player_id in rows:
            try:
                member = ctx.guild.get_member(int(discord_id))
                if member:
                    name = member.nick if member.nick else member.name
                else:
                    user = await bot.fetch_user(int(discord_id))
                    name = user.name
            except:
                name = f"UnknownUser ({discord_id})"

            lines.append(f"**{name}** → `{platform}/{player_id}`")

        description = "\n".join(lines)
        embed = discord.Embed(title="🔗 Linked Accounts", description=description, color=discord.Color.blue())
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"⚠️ Error listing links: {e}")

@bot.command()
async def rank(ctx):
    try:
        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile(platform, player_id)

        if ctx.guild:
            try:
                role_name = profile_data['rank']
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if not role:
                    role = await ctx.guild.create_role(name=role_name)
                await ctx.author.add_roles(role)
            except discord.Forbidden:
                await ctx.send("⚠️ Missing permissions to change roles.")

        await ctx.send(f"✅ Rank: **{profile_data['rank']}**")

    except Exception as e:
        await ctx.send(f"❌ Error fetching rank: {e}")

@bot.command()
async def stats(ctx):
    try:
        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile(platform, player_id)

        embed = discord.Embed(title=f"{profile_data['name']}'s Stats", color=0x00ffcc)
        embed.add_field(name="Rank", value=profile_data['rank'], inline=True)
        embed.add_field(name="Wins", value=profile_data['wins'], inline=True)
        embed.add_field(name="Losses", value=profile_data['losses'], inline=True)
        embed.add_field(name="Goals", value=profile_data['goals'], inline=True)
        embed.add_field(name="Passes", value=profile_data['passes'], inline=True)
        embed.add_field(name="Steals", value=profile_data['steals'], inline=True)
        embed.add_field(name="Saves", value=profile_data['saves'], inline=True)

        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(f"❌ Error fetching stats: {e}")

async def fetch_profile(platform: str, player_id: str) -> dict:
    from playwright.async_api import async_playwright

    url = f"https://www.rematchtracker.com/player/{platform}/{player_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector("h1")
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, 'html.parser')

    name = "Unknown"
    rank = "N/A"

    name_tag = soup.select_one("h1")
    if name_tag:
        name = name_tag.get_text(strip=True)

    rank_tag = soup.select_one("div.text-lg.font-bold.text-white")
    if rank_tag:
        rank = rank_tag.get_text(strip=True)

    def get_stat_by_selector(selector):
        el = soup.select_one(selector)
        return el.get_text(strip=True) if el else "N/A"

    return {
        "name": name,
        "rank": rank,
        "wins": get_stat_by_selector("div.text-lg.font-bold.text-green-400.svelte-kej2cd"),
        "losses": get_stat_by_selector("div.text-lg.font-bold.text-red-400.svelte-kej2cd"),
        "goals": get_stat_by_selector("span.font-bold.text-purple-400.svelte-kej2cd"),
        "passes": get_stat_by_selector("span.font-bold.text-blue-400.svelte-kej2cd"),
        "steals": get_stat_by_selector("span.font-bold.text-pink-400.svelte-kej2cd"),
        "saves": get_stat_by_selector("span.font-bold.text-red-400.svelte-kej2cd")
    }

# Run the bot
import os
bot.run(os.getenv("DISCORD_BOT_TOKEN"))








