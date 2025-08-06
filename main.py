import discord
from discord.ext import commands
import aiosqlite
import requests
from bs4 import BeautifulSoup
import re

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

# Command to link profile

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

# Command to show only rank and update nickname/role
@bot.command()
async def rank(ctx):
    try:
        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not u.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = fetch_profile(platform, player_id)

        # Update role only
        try:
            role_name = profile_data['rank']
            role = discord.utils.get(ctx.guild.roles, name=role_name)
            if not role:
                role = await ctx.guild.create_role(name=role_name)
            await ctx.author.add_roles(role)
        except discord.Forbidden:
            await ctx.send("⚠️ Missing permissions to change nickname or roles.")

        await ctx.send(f"✅ Rank: **{profile_data['rank']}**")

    except Exception as e:
        await ctx.send(f"❌ Error fetching rank: {e}")

# Command to display full stats
@bot.command()
async def stats(ctx):
    try:
        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not u.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = fetch_profile(platform, player_id)

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

# Updated fetch function for rematchtracker.com

def fetch_profile(platform: str, player_id: str) -> dict:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    import time

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--single-process")
    options.add_argument("--log-level=3")  # Reduces logging noise


    driver = webdriver.Chrome(options=options)
    url = f"https://www.rematchtracker.com/player/{platform}/{player_id}"
    driver.get(url)

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.text-lg.font-bold.text-green-400"))
        )
        html = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(html, 'html.parser')
    with open("rematch_debug_output.html", "w", encoding="utf-8") as f:
        f.write(soup.prettify())
    print("✅ HTML written to rematch_debug_output.html")

    name_section = soup.select_one("section.relative.overflow-hidden")
    name = "Unknown"
    rank = "N/A"
    if name_section:
        name_tag = soup.select_one(r"body > div > main > section.relative.overflow-hidden.svelte-kej2cd > div.relative.z-10.max-w-6xl.mx-auto.px-4.pt-24.svelte-kej2cd > div > div.lg\:col-span-8.svelte-kej2cd > div > div.flex.flex-col.justify-between.py-1.flex-1.svelte-kej2cd > div.flex.items-center.gap-3.mb-3.svelte-kej2cd > h1")
        if name_tag:
            name = name_tag.get_text(strip=True)
        rank_tag = soup.select_one(r"body > div > main > section.relative.overflow-hidden.svelte-kej2cd > div.relative.z-10.max-w-6xl.mx-auto.px-4.pt-24.svelte-kej2cd > div > div.lg\:col-span-4.svelte-kej2cd > div > div.text-lg.font-bold.text-white.mb-1.svelte-kej2cd")
        if rank_tag:
            rank = rank_tag.get_text(strip=True).replace("Rank:", "").strip()

    def get_stat(label):
        print(f"🔍 Searching for label: {label}")
        section = soup.select_one(r"section.py-8.px-4.bg-gray-800\/50")
        if not section:
            return "N/A"
        stat_blocks = section.find_all("div", class_=re.compile("flex flex-col"))
        print(f"📦 Found {len(stat_blocks)} stat blocks")
        for block in stat_blocks:
            print("-- BLOCK START --")
            print(block.get_text(strip=True))
            print("-- BLOCK END --")
        for block in stat_blocks:
            label_div = block.find("div", class_=re.compile("text-white/60 text-sm"))
            value_div = block.find("div", class_=re.compile("text-lg.*font-bold"))
            if label_div and value_div:
                found_label = label_div.get_text(strip=True)
                found_value = value_div.get_text(strip=True)
                print(f"➡️ Found label: '{found_label}' with value: '{found_value}'")
                label_cleaned = re.sub(r'[^a-zA-Z]', '', found_label).lower()
                if label.lower() == label_cleaned:
                    return found_value
        return "N/A"

    return {
        "name": name,
        "rank": rank,
        "wins": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(1) > div > div.text-center.p-2.bg-gray-900\/30.rounded.border.border-green-400\/20.svelte-kej2cd > div.text-lg.font-bold.text-green-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(1) > div > div.text-center.p-2.bg-gray-900\/30.rounded.border.border-green-400\/20.svelte-kej2cd > div.text-lg.font-bold.text-green-400.svelte-kej2cd") else "N/A",
        "losses": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(1) > div > div.text-center.p-2.bg-gray-900\/30.rounded.border.border-red-400\/20.svelte-kej2cd > div.text-lg.font-bold.text-red-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(1) > div > div.text-center.p-2.bg-gray-900\/30.rounded.border.border-red-400\/20.svelte-kej2cd > div.text-lg.font-bold.text-red-400.svelte-kej2cd") else "N/A",
        "goals": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(2) > div > div:nth-child(1) > span.font-bold.text-purple-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(2) > div > div:nth-child(1) > span.font-bold.text-purple-400.svelte-kej2cd") else "N/A",
        "passes": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(3) > div > div:nth-child(1) > span.font-bold.text-blue-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(3) > div > div:nth-child(1) > span.font-bold.text-blue-400.svelte-kej2cd") else "N/A",
        "steals": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(4) > div > div:nth-child(3) > span.font-bold.text-pink-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(4) > div > div:nth-child(3) > span.font-bold.text-pink-400.svelte-kej2cd") else "N/A",
        "saves": soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(4) > div > div:nth-child(4) > span.font-bold.text-red-400.svelte-kej2cd").get_text(strip=True) if soup.select_one(r"body > div > main > section.py-8.px-4.bg-gray-800\/50.svelte-kej2cd > div > div > div:nth-child(1) > div.h-fit.bg-gray-800\/60.border.border-gray-700\/50.p-6.svelte-kej2cd > div.space-y-4.svelte-kej2cd > div:nth-child(4) > div > div:nth-child(4) > span.font-bold.text-red-400.svelte-kej2cd") else "N/A"
    }

# Run the bot
import os
bot.run(os.getenv("DISCORD_BOT_TOKEN"))





