import os
import sys
import json
import re
import asyncio
from datetime import datetime
from typing import Optional, Tuple, List

import discord
from discord.ext import commands
from bs4 import BeautifulSoup

# Pillow imports (optional)
try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

# aiohttp for avatar downloads
try:
    import aiohttp
except Exception:
    aiohttp = None

# Detect sqlite/aiosqlite availability (do NOT crash at import time)
USE_SQLITE = False
aiosqlite = None
try:
    import sqlite3  # type: ignore
    try:
        import aiosqlite  # type: ignore
        USE_SQLITE = True
    except Exception:
        USE_SQLITE = False
except Exception:
    USE_SQLITE = False

# Paths
LINKED_DB_PATH = "linked_profiles.db"
LAST_STATS_PATH = "last_stats.json"

# JSON fallback DB for linked_profiles
class JSONDB:
    def __init__(self, path: str):
        self.path = path
        if not os.path.exists(self.path):
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _sync_load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _sync_save(self, data: dict):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)

    async def _load(self) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_load)

    async def _save(self, data: dict):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_save, data)

    async def replace_link(self, discord_id: str, platform: str, player_id: str):
        data = await self._load()
        data[discord_id] = {"platform": platform, "player_id": player_id}
        await self._save(data)

    async def get_link(self, discord_id: str) -> Optional[Tuple[str, str]]:
        data = await self._load()
        v = data.get(discord_id)
        if v:
            return v.get("platform"), v.get("player_id")
        return None

    async def delete_link(self, discord_id: str) -> bool:
        data = await self._load()
        if discord_id in data:
            data.pop(discord_id)
            await self._save(data)
            return True
        return False

    async def list_links(self) -> List[Tuple[str, str, str]]:
        data = await self._load()
        return [(k, v["platform"], v["player_id"]) for k, v in data.items()]

    async def clear(self):
        await self._save({})


json_db: Optional[JSONDB] = None
if not USE_SQLITE:
    json_db = JSONDB("linked_profiles.json")


# Unified DB helpers
async def init_linked_db():
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS linked_profiles (
                    discord_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    player_id TEXT NOT NULL
                )
                """
            )
            await db.commit()
    else:
        assert json_db is not None
        await json_db._load()


async def replace_link(discord_id: str, platform: str, player_id: str):
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            await db.execute(
                "REPLACE INTO linked_profiles (discord_id, platform, player_id) VALUES (?, ?, ?)",
                (discord_id, platform, player_id),
            )
            await db.commit()
    else:
        assert json_db is not None
        await json_db.replace_link(discord_id, platform, player_id)


async def get_link(discord_id: str) -> Optional[Tuple[str, str]]:
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()
                return tuple(row) if row else None
    else:
        assert json_db is not None
        return await json_db.get_link(discord_id)


async def delete_link(discord_id: str) -> bool:
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            await db.execute("DELETE FROM linked_profiles WHERE discord_id = ?", (discord_id,))
            await db.commit()
            return True
    else:
        assert json_db is not None
        return await json_db.delete_link(discord_id)


async def list_links() -> List[Tuple[str, str, str]]:
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            async with db.execute("SELECT discord_id, platform, player_id FROM linked_profiles") as cursor:
                rows = await cursor.fetchall()
                return [(r[0], r[1], r[2]) for r in rows]
    else:
        assert json_db is not None
        return await json_db.list_links()


async def clear_links():
    if USE_SQLITE:
        async with aiosqlite.connect(LINKED_DB_PATH) as db:  # type: ignore
            await db.execute("DELETE FROM linked_profiles")
            await db.commit()
    else:
        assert json_db is not None
        await json_db.clear()


# --- last_stats cache helpers ---

def _load_last_stats_sync() -> dict:
    if not os.path.exists(LAST_STATS_PATH):
        return {}
    try:
        with open(LAST_STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_last_stats_sync(data: dict):
    tmp = LAST_STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, LAST_STATS_PATH)


async def get_all_last_stats() -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _load_last_stats_sync)


async def update_last_stats(discord_id: str, platform: str, player_id: str, profile_data: dict):
    loop = asyncio.get_running_loop()
    def _update():
        data = _load_last_stats_sync()
        entry = {
            "platform": platform,
            "player_id": player_id,
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "rank": profile_data.get("rank", "N/A"),
            "wins": profile_data.get("wins", "N/A"),
            "losses": profile_data.get("losses", "N/A"),
            "goals": profile_data.get("goals", "N/A"),
            "passes": profile_data.get("passes", "N/A"),
            "steals": profile_data.get("steals", "N/A"),
            "saves": profile_data.get("saves", "N/A"),
            "assists": profile_data.get("assists", "N/A"),
        }
        data[discord_id] = entry
        _save_last_stats_sync(data)
    await loop.run_in_executor(None, _update)


# --- Discord bot setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await init_linked_db()
    print(f"Logged in as {bot.user}")


# --- Commands: linking / admin management ---
@bot.command()
@commands.has_permissions(administrator=True)
async def forcelink(ctx, member: discord.Member, profile_url: str):
    try:
        parts = profile_url.strip('/').split('/')
        profile_index = parts.index("player")
        platform = parts[profile_index + 1]
        user_id = parts[profile_index + 2]

        discord_id = str(member.id)
        await replace_link(discord_id, platform, user_id)
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
        await replace_link(discord_id, platform, user_id)
        await ctx.send(f"✅ Linked to `{platform}/{user_id}`.")
    except Exception as e:
        await ctx.send(f"❌ Error linking profile: {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def cleardb(ctx):
    try:
        await clear_links()
        await ctx.send("🧨 All linked profiles have been cleared from the database.")
    except Exception as e:
        await ctx.send(f"❌ Error clearing the database: {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def unlink(ctx, member: discord.Member):
    try:
        discord_id = str(member.id)
        await delete_link(discord_id)
        await ctx.send(f"🗑️ Unlinked profile for {member.display_name}.")
    except Exception as e:
        await ctx.send(f"❌ Error unlinking profile: {e}")


@bot.command()
@commands.has_permissions(administrator=True)
async def listlinks(ctx):
    try:
        rows = await list_links()
        if not rows:
            await ctx.send("❌ No linked profiles found.")
            return

        lines = []
        for discord_id, platform, player_id in rows:
            try:
                member = ctx.guild.get_member(int(discord_id)) if ctx.guild else None
                if member:
                    name = member.nick if member.nick else member.name
                else:
                    user = await bot.fetch_user(int(discord_id))
                    name = user.name
            except Exception:
                name = f"UnknownUser ({discord_id})"

            lines.append(f"**{name}** → `{platform}/{player_id}`")

        description = "\n".join(lines)
        embed = discord.Embed(title="🔗 Linked Accounts", description=description, color=discord.Color.blue())
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"⚠️ Error listing links: {e}")


# --- Playwright-based scraping helpers (import locally inside functions) ---
async def fetch_profile_same_page(platform: str, player_id: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        raise RuntimeError("Playwright is not available in this environment") from e

    url = f"https://www.rematchtracker.com/player/{platform}/{player_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector("h1")

        try:
            await page.click("div.flex.flex-col.sm\\:flex-row.justify-between.items-start.sm\\:items-center.gap-4.mb-6.svelte-kej2cd div")
            for _ in range(4):
                await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, 'html.parser')

    name = soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else "Unknown"
    rank = soup.select_one("div.text-lg.font-bold.text-white").get_text(strip=True) if soup.select_one("div.text-lg.font-bold.text-white") else "N/A"

    def get_stat(selector, index=0):
        els = soup.select(selector)
        if els and len(els) > index:
            return els[index].get_text(strip=True)
        return "N/A"

    return {
        "name": name,
        "rank": rank,
        "wins": get_stat("div.text-lg.font-bold.text-green-400.svelte-kej2cd"),
        "losses": get_stat("div.text-lg.font-bold.text-red-400.svelte-kej2cd"),
        "goals": get_stat("span.font-bold.text-purple-400.svelte-kej2cd"),
        "passes": get_stat("span.font-bold.text-blue-400.svelte-kej2cd", 1),
        "steals": get_stat("span.font-bold.text-pink-400.svelte-kej2cd"),
        "saves": get_stat("span.font-bold.text-red-400.svelte-kej2cd"),
        "assists": get_stat("span.font-bold.text-orange-400.svelte-kej2cd")
    }


async def fetch_profile(platform: str, player_id: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except Exception as e:
        raise RuntimeError("Playwright is not available in this environment") from e

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

    def get_stat_by_selector(selector, index=0):
        els = soup.select(selector)
        if els and len(els) > index:
            return els[index].get_text(strip=True)
        return "N/A"

    return {
        "name": name,
        "rank": rank,
        "wins": get_stat_by_selector("div.text-lg.font-bold.text-green-400.svelte-kej2cd"),
        "losses": get_stat_by_selector("div.text-lg.font-bold.text-red-400.svelte-kej2cd"),
        "goals": get_stat_by_selector("span.font-bold.text-purple-400.svelte-kej2cd"),
        "passes": get_stat_by_selector("span.font-bold.text-blue-400.svelte-kej2cd", 1),
        "steals": get_stat_by_selector("span.font-bold.text-pink-400.svelte-kej2cd"),
        "saves": get_stat_by_selector("span.font-bold.text-red-400.svelte-kej2cd"),
        "assists": get_stat_by_selector("span.font-bold.text-orange-400.svelte-kej2cd")
    }


# --- Image helpers and generators ---

def _require_pil():
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError("Pillow (PIL) is required for image generation commands. Install it or disable these commands.")


async def _fetch_image_from_url(url: str, session: Optional[aiohttp.ClientSession] = None) -> Optional[Image.Image]:
    if aiohttp is None:
        return None
    try:
        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
            else:
                img = None
        if close_session:
            await session.close()
        return img
    except Exception:
        return None


async def generate_stats_card(user_name, profile_data, avatar_url=None):
    _require_pil()
    from io import BytesIO
    import aiohttp as _aiohttp

    bg = Image.new("RGBA", (800, 300), (30, 30, 30, 255))
    draw = ImageDraw.Draw(bg)

    session = None
    try:
        session = _aiohttp.ClientSession()
        if avatar_url:
            try:
                async with session.get(avatar_url, timeout=10) as resp:
                    content = await resp.read()
                    avatar_img = Image.open(BytesIO(content)).convert("RGBA")
                    w, h = avatar_img.size
                    min_dim = min(w, h)
                    left = (w - min_dim) // 2
                    top = (h - min_dim) // 2
                    avatar_img = avatar_img.crop((left, top, left + min_dim, top + min_dim)).resize((128, 128), Image.LANCZOS)

                    mask = Image.new("L", (128, 128), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.ellipse((0, 0, 128, 128), fill=255)

                    border_layer = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                    border_draw = ImageDraw.Draw(border_layer)
                    border_draw.ellipse((0, 0, 128, 128), outline=(255, 255, 255, 255), width=4)

                    avatar_circular = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
                    avatar_circular.paste(avatar_img, (0, 0), mask)
                    avatar_final = Image.alpha_composite(avatar_circular, border_layer)

                    avatar_x = 640
                    avatar_y = (300 - 128) // 2
                    bg.paste(avatar_final, (avatar_x, avatar_y), avatar_final)
            except Exception:
                pass
    finally:
        if session:
            await session.close()

    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        title_font = ImageFont.truetype(font_path, 36)
        stat_font = ImageFont.truetype(font_path, 32)
    except Exception:
        title_font = stat_font = ImageFont.load_default()

    title_text = f"{user_name}'s Stats"
    try:
        title_width = draw.textlength(title_text, font=title_font)
    except Exception:
        title_width = 0
    draw.text((30, 30), title_text, font=title_font, fill=(255, 255, 255))

    try:
        rank_icons = {
            "Bronze": "assets/ranks/bronze.png",
            "Silver": "assets/ranks/silver.png",
            "Gold": "assets/ranks/gold.png",
            "Platinum": "assets/ranks/platinum.png",
            "Diamond": "assets/ranks/diamond.png",
            "Master": "assets/ranks/master.png",
            "Elite": "assets/ranks/elite.png"
        }
        rank_name = profile_data.get('rank', '')
        for key in rank_icons:
            if key.lower() in str(rank_name).lower():
                icon_path = rank_icons[key]
                if os.path.exists(icon_path):
                    rank_icon = Image.open(icon_path).convert("RGBA").resize((36, 36), Image.LANCZOS)
                    bg.paste(rank_icon, (40 + int(title_width), 30), rank_icon)
                    draw.text((80 + int(title_width), 30), rank_name, font=title_font, fill=(255, 215, 0))
                    break
    except Exception:
        pass

    def safe_int(val):
        try:
            return int(re.sub(r"[^0-9]", "", str(val)))
        except Exception:
            return 0

    y_stats = 110
    wins = safe_int(profile_data.get('wins', 0))
    losses = safe_int(profile_data.get('losses', 0))
    total_games = wins + losses
    win_percent = round((wins / total_games) * 100, 1) if total_games > 0 else 0.0

    draw.text((30, y_stats), "Wins:", font=stat_font, fill=(0, 255, 0))
    draw.text((120, y_stats), str(wins), font=stat_font, fill=(255, 255, 255))
    draw.text((250, y_stats), "Losses:", font=stat_font, fill=(255, 0, 0))
    draw.text((370, y_stats), str(losses), font=stat_font, fill=(255, 255, 255))

    left_column = [
        ("Goals", profile_data.get('goals', 'N/A')),
        ("Passes", profile_data.get('passes', 'N/A')),
        ("Assists", profile_data.get('assists', 'N/A')),
    ]
    right_column = [
        ("Saves", profile_data.get('saves', 'N/A')),
        ("Steals", profile_data.get('steals', 'N/A')),
        ("Win%", f"{win_percent}%"),
    ]

    y_base = y_stats + 50
    row_spacing = 36
    for i, (label, value) in enumerate(right_column):
        if label == "Win%":
            draw.text((250, y_base + i * row_spacing), f"{label}:", font=stat_font, fill=(100, 200, 255))
            draw.text((370, y_base + i * row_spacing), str(value), font=stat_font, fill=(255, 255, 255))
        else:
            draw.text((250, y_base + i * row_spacing), f"{label}: {value}", font=stat_font, fill=(200, 200, 200))

    for i, (label, value) in enumerate(left_column):
        draw.text((30, y_base + i * row_spacing), f"{label}: {value}", font=stat_font, fill=(200, 200, 200))

    if not os.path.exists("stat_cards"):
        os.makedirs("stat_cards", exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    path = f"stat_cards/{safe_name}_stats.png"
    bg.save(path)
    return path


async def generate_rank_stats_card(user_name, profile_data, avatar_url=None):
    _require_pil()
    return await generate_stats_card(user_name, profile_data, avatar_url)


async def generate_rank_card(user_name, rank, avatar_url=None):
    _require_pil()
    from io import BytesIO
    import aiohttp as _aiohttp

    bg = Image.new("RGBA", (600, 200), (30, 30, 30, 255))
    draw = ImageDraw.Draw(bg)

    try:
        icon_path = f"assets/ranks/{str(rank).lower().replace(' ', '_')}.png"
        if os.path.exists(icon_path):
            rank_icon = Image.open(icon_path).convert("RGBA").resize((128, 128))
            bg.paste(rank_icon, (25, 36), rank_icon)
    except Exception:
        pass

    avatar_final = None
    session = None
    try:
        session = _aiohttp.ClientSession()
        if avatar_url:
            try:
                async with session.get(avatar_url, timeout=10) as resp:
                    content = await resp.read()
                    avatar_img = Image.open(BytesIO(content)).convert("RGBA")
                    w, h = avatar_img.size
                    min_dim = min(w, h)
                    left = (w - min_dim) // 2
                    top = (h - min_dim) // 2
                    avatar_img = avatar_img.crop((left, top, left + min_dim, top + min_dim)).resize((96, 96), Image.LANCZOS)

                    mask = Image.new("L", (96, 96), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.ellipse((0, 0, 96, 96), fill=255)

                    border_layer = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
                    border_draw = ImageDraw.Draw(border_layer)
                    border_draw.ellipse((0, 0, 96, 96), outline=(255, 255, 255, 255), width=4)

                    avatar_circular = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
                    avatar_circular.paste(avatar_img, (0, 0), mask)
                    avatar_final = Image.alpha_composite(avatar_circular, border_layer)

                    avatar_x = 600 - 96 - 30
                    avatar_y = (200 - 96) // 2
                    bg.paste(avatar_final, (avatar_x, avatar_y), avatar_final)
            except Exception:
                pass
    finally:
        if session:
            await session.close()

    try:
        base_font_size = 36
        font_path = "arial.ttf"
        font = ImageFont.truetype(font_path, base_font_size)
    except Exception:
        font = ImageFont.load_default()

    max_width = 304
    text = f"{user_name}'s Rank"

    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
    except Exception:
        text_width = 0

    while text_width > max_width and base_font_size > 12:
        base_font_size -= 1
        try:
            font = ImageFont.truetype(font_path, base_font_size)
        except Exception:
            font = ImageFont.load_default()
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
        except Exception:
            break

    draw.text((170, 70), text, font=font, fill=(255, 255, 255))
    try:
        rank_font = ImageFont.truetype(font_path, 32)
    except Exception:
        try:
            rank_font = ImageFont.truetype("DejaVuSans.ttf", 32)
        except Exception:
            rank_font = ImageFont.load_default()
    draw.text((170, 110), str(rank), font=rank_font, fill=(255, 215, 0))

    if not os.path.exists("rank_cards"):
        os.makedirs("rank_cards", exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    path = f"rank_cards/{safe_name}_rank.png"
    bg.save(path)
    return path


# --- Commands: rank/stats/rstats that scrape and update last_stats ---
@bot.command()
async def rank(ctx, member: discord.Member = None):
    try:
        discord_id = str(member.id if member else ctx.author.id)

        row = await get_link(discord_id)
        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile(platform, player_id)

        # Update cached last_stats
        await update_last_stats(discord_id, platform, player_id, profile_data)

        if ctx.guild:
            try:
                role_name = profile_data.get('rank', 'N/A')
                if role_name and role_name != 'N/A':
                    role = discord.utils.get(ctx.guild.roles, name=role_name)
                    if not role:
                        role = await ctx.guild.create_role(name=role_name)
                    target = member if member else ctx.author
                    await target.add_roles(role)
            except discord.Forbidden:
                await ctx.send("⚠️ Missing permissions to change roles.")

        target_name = member.display_name if member else ctx.author.display_name
        avatar_url = (member or ctx.author).avatar.url if (member or ctx.author).avatar else None
        image_path = await generate_rank_card(target_name, profile_data.get('rank', 'N/A'), avatar_url)
        if image_path:
            file = discord.File(image_path, filename="rank.png")
            await ctx.send(file=file)
            try:
                os.remove(image_path)
            except Exception:
                pass
    except Exception as e:
        await ctx.send(f"❌ Error fetching rank: {e}")


@bot.command()
async def stats(ctx, member: discord.Member = None):
    try:
        discord_id = str(member.id if member else ctx.author.id)

        row = await get_link(discord_id)
        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile(platform, player_id)

        # Update cached last_stats
        await update_last_stats(discord_id, platform, player_id, profile_data)

        target_name = member.display_name if member else ctx.author.display_name
        avatar_url = (member or ctx.author).avatar.url if (member or ctx.author).avatar else None
        image_path = await generate_stats_card(target_name, profile_data, avatar_url)
        if image_path:
            file = discord.File(image_path, filename="stats.png")
            await ctx.send(file=file)
            try:
                os.remove(image_path)
            except Exception:
                pass
    except Exception as e:
        await ctx.send(f"❌ Error fetching stats: {e}")


@bot.command()
async def rstats(ctx, member: discord.Member = None):
    try:
        discord_id = str(member.id if member else ctx.author.id)

        row = await get_link(discord_id)
        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile_same_page(platform, player_id)

        # Update cached last_stats
        await update_last_stats(discord_id, platform, player_id, profile_data)

        target_name = member.display_name if member else ctx.author.display_name
        avatar_url = (member or ctx.author).avatar.url if (member or ctx.author).avatar else None
        image_path = await generate_rank_stats_card(target_name, profile_data, avatar_url)
        if image_path:
            file = discord.File(image_path, filename="rank_stats.png")
            await ctx.send(file=file)
            try:
                os.remove(image_path)
            except Exception:
                pass
    except Exception as e:
        await ctx.send(f"❌ Error fetching ranked stats: {e}")


# --- Leaderboard generation ---
RANK_PRIORITY = {"Elite": 1, "Master": 2, "Diamond": 3, "Platinum": 4, "Gold": 5, "Silver": 6, "Bronze": 7}

async def _fetch_avatar_image(user: discord.User) -> Image.Image:
    _require_pil()
    if aiohttp is None:
        raise RuntimeError("aiohttp is required for fetching avatars")

    url = None
    if getattr(user, 'avatar', None):
        try:
            url = str(user.avatar.url)
        except Exception:
            url = None
    if not url:
        # fallback to default avatar
        return Image.new("RGBA", (40, 40), (100, 100, 100, 255))

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    from io import BytesIO
                    return Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            pass
    return Image.new("RGBA", (40, 40), (100, 100, 100, 255))


def _normalize_rank_name(rank: str) -> str:
    # Convert to known rank string that matches asset filenames
    if not rank:
        return "bronze"
    return rank.strip().title()

def parse_number(value):
    if value is None:
        return 0
    s = str(value).lower().replace(",", "").strip()
    try:
        if s.endswith("%"):
            return float(s[:-1])  # "52%" -> 52.0
        elif s.endswith("k"):
            return float(s[:-1]) * 1000
        elif s.endswith("m"):
            return float(s[:-1]) * 1_000_000
        elif s.endswith("b"):
            return float(s[:-1]) * 1_000_000_000
        else:
            return float(s)
    except Exception:
        return 0

@bot.command()
async def leaderboard(ctx, stat: str = "wins"):
    stat = stat.lower()
    valid = ["wins", "goals", "saves", "rank", "passes", "steals", "assists", "%"]
    if stat not in valid:
        await ctx.send("Valid leaderboard types: wins, goals, saves, rank, passes, steals, assists, win%")
        return

    data = await get_all_last_stats()
    if not data:
        await ctx.send("No cached stats available. Ask users to run `!stats` or `!rank` to generate cached data.")
        return

    entries = []
    for user_id, entry in data.items():
        try:
            user = await bot.fetch_user(int(user_id))
        except Exception:
            continue
        if stat == "rank":
            rank_name = entry.get('rank', 'Bronze')
            sort_val = RANK_PRIORITY.get(rank_name.title(), 0)
            display_val = rank_name
        else:
            raw_val = entry.get(stat, 0)
            sort_val = parse_number(raw_val)
        
            if stat in ["%", "win%", "winrate", "win_rate"]:  # win% cases
                try:
                    # Always show one decimal place + %
                    display_val = f"{float(sort_val):.1f}%"
                except Exception:
                    display_val = "0.0%"
            else:
                display_val = raw_val


        entries.append({
            "id": user_id,
            "user": user,
            "rank": entry.get('rank', 'Bronze'),
            "value": display_val,
            "sort": sort_val
        })

    reverse = stat != "rank"
    entries.sort(key=lambda x: x['sort'], reverse=reverse)

    # Build image
    _require_pil()
    from io import BytesIO

    rows_per_col = 10
    row_h = 56
    col_w = 360
    cols = (len(entries) + rows_per_col - 1) // rows_per_col
    header_h = 80
    width = max(600, cols * col_w)
    height = header_h + rows_per_col * row_h

    img = Image.new("RGBA", (width, height), (25, 25, 25, 255))
    draw = ImageDraw.Draw(img)

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        entry_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        title_font = entry_font = ImageFont.load_default()

    draw.text((width // 2, 40), f"{stat.capitalize()} Leaderboard", font=title_font, anchor="ms", fill=(255, 255, 255))

    x_base = 20
    y_base = header_h
    col = 0
    row = 0

    async with aiohttp.ClientSession() as session:
        for i, e in enumerate(entries):
            if i > 0 and i % rows_per_col == 0:
                col += 1
                row = 0
            x = x_base + col * col_w
            y = y_base + row * row_h

            # Avatar
            avatar_img = await _fetch_avatar_image(e['user'])
            try:
                avatar_thumb = avatar_img.resize((40, 40))
            except Exception:
                avatar_thumb = Image.new("RGBA", (40, 40), (100, 100, 100, 255))
            img.paste(avatar_thumb, (x, y + 8), avatar_thumb)

            # Name
            name_x = x + 54

            # Rank number (1-based)
            rank_num = f"{i+1}."

            # Rank symbol and color
            if i == 0:
                rank_symbol = "1"
                rank_color = (255, 215, 0)      # Gold
            elif i == 1:
                rank_symbol = "2"
                rank_color = (192, 192, 192)    # Silver
            elif i == 2:
                rank_symbol = "3"
                rank_color = (205, 127, 50)     # Bronze
            else:
                rank_symbol = f"{i+1}."
                rank_color = (173, 216, 230)    # Light Blue
            
            # Draw text with emoji and color
            draw.text((name_x, y + 14), f"{rank_symbol} {e['user'].name}", font=entry_font, fill=rank_color)

            # Rank emblem
            rank_name = _normalize_rank_name(e['rank'])
            emblem_path = f"assets/ranks/{rank_name.lower().replace(' ', '_')}.png"
            try:
                emblem = Image.open(emblem_path).convert("RGBA").resize((28, 28))
            except Exception:
                emblem = None

            if stat == 'rank':
                # Emblem then rank name
                if emblem:
                    img.paste(emblem, (name_x + 180, y + 10), emblem)
                    draw.text((name_x + 220, y + 14), str(e['value']), font=entry_font, fill=(255, 255, 255))
                else:
                    draw.text((name_x + 180, y + 14), str(e['value']), font=entry_font, fill=(255, 255, 255))
            else:
                # Name, emblem, then value
                if emblem:
                    img.paste(emblem, (name_x + 200, y + 10), emblem)
                    draw.text((name_x + 240, y + 14), str(e['value']), font=entry_font, fill=(255, 255, 255))
                else:
                    draw.text((name_x + 200, y + 14), str(e['value']), font=entry_font, fill=(255, 255, 255))

            row += 1

    out = BytesIO()
    img.save(out, format='PNG')
    out.seek(0)

    await ctx.send(file=discord.File(out, filename=f"{stat}_leaderboard.png"))

# Run the bot
import os
bot.run(os.getenv("DISCORD_BOT_TOKEN"))




















