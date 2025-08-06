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
        discord_id = str(ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT * FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    await ctx.send("⚠️ You have already linked a profile. Contact an admin to relink.")
                    return

        parts = profile_url.strip('/').split('/')
        profile_index = parts.index("player")
        platform = parts[profile_index + 1]
        user_id = parts[profile_index + 2]

        async with aiosqlite.connect("linked_profiles.db") as db:
            await db.execute(
                "INSERT INTO linked_profiles (discord_id, platform, player_id) VALUES (?, ?, ?)",
                (discord_id, platform, user_id)
            )
            await db.commit()

        await ctx.send(f"✅ Linked to `{platform}/{user_id}`.")
    except Exception as e:
        await ctx.send(f"❌ Error linking profile: {e}")

# Admin-only command to clear the entire database
@bot.command()
@commands.has_permissions(administrator=True)
async def cleardb(ctx):
    try:
        async with aiosqlite.connect("linked_profiles.db") as db:
            await db.execute("DELETE FROM linked_profiles")
            await db.commit()
        await ctx.send("🧨 All linked profiles have been cleared from the database.")
    except Exception as e:
        await ctx.send(f"❌ Error clearing the database: {e}")

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
async def rank(ctx, member: discord.Member = None):
    try:
        discord_id = str(member.id if member else ctx.author.id)

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
                target = member if member else ctx.author
                await target.add_roles(role)
            except discord.Forbidden:
                await ctx.send("⚠️ Missing permissions to change roles.")

        target = member.display_name if member else ctx.author.display_name
        avatar_url = (member or ctx.author).avatar.url if (member or ctx.author).avatar else None
        image_path = await generate_rank_card(target, profile_data['rank'], avatar_url)
        file = discord.File(image_path, filename="rank.png")
        await ctx.send(file=file)
        os.remove(image_path)

    except Exception as e:
        await ctx.send(f"❌ Error fetching rank: {e}")

@bot.command()
async def stats(ctx, member: discord.Member = None):
    try:
        discord_id = str(member.id if member else ctx.author.id)

        async with aiosqlite.connect("linked_profiles.db") as db:
            async with db.execute("SELECT platform, player_id FROM linked_profiles WHERE discord_id = ?", (discord_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            await ctx.send("❌ You haven't linked a profile yet. Use `!link <REMATCH TRACKER (not U.gg) profile URL>` first.")
            return

        platform, player_id = row
        profile_data = await fetch_profile(platform, player_id)

        target_name = member.display_name if member else ctx.author.display_name
        avatar_url = (member or ctx.author).avatar.url if (member or ctx.author).avatar else None
        image_path = await generate_stats_card(target_name, profile_data, avatar_url)
        file = discord.File(image_path, filename="stats.png")
        await ctx.send(file=file)
        os.remove(image_path)

    except Exception as e:
        await ctx.send(f"❌ Error fetching stats: {e}")

async def generate_stats_card(user_name, profile_data, avatar_url=None):
    bg = Image.new("RGBA", (800, 300), (30, 30, 30, 255))
    draw = ImageDraw.Draw(bg)

    if avatar_url:
        try:
            import requests
            from io import BytesIO
            response = requests.get(avatar_url)
            avatar_img = Image.open(BytesIO(response.content)).convert("RGBA")
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

            avatar_x = 660
            avatar_y = (300 - 128) // 2
            bg.paste(avatar_final, (avatar_x, avatar_y), avatar_final)
        except Exception as e:
            print(f"Error loading avatar: {e}")

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/msttcorefonts/arial.ttf", 36)
        stat_font = ImageFont.truetype("/usr/share/fonts/truetype/msttcorefonts/arial.ttf", 32)
    except:
        title_font = stat_font = ImageFont.load_default()

    title_text = f"{user_name}'s Stats"
    title_width = draw.textlength(title_text, font=title_font)
    draw.text((30, 30), title_text, font=title_font, fill=(255, 255, 255))

    try:
        from io import BytesIO
        import requests
        rank_icons = {
            "Bronze": "assets/ranks/bronze.png",
            "Silver": "assets/ranks/silver.png",
            "Gold": "assets/ranks/gold.png",
            "Platinum": "assets/ranks/platinum.png",
            "Diamond": "assets/ranks/diamond.png",
            "Champion": "assets/ranks/champion.png",
            "Grand Champion": "assets/ranks/grand_champion.png",
            "Legend": "assets/ranks/legend.png"
        }
        rank_name = profile_data.get('rank', '')
        for key in rank_icons:
            if key.lower() in rank_name.lower():
                icon_path = rank_icons[key]
                if os.path.exists(icon_path):
                    rank_icon = Image.open(icon_path).convert("RGBA").resize((36, 36), Image.LANCZOS)
                    bg.paste(rank_icon, (40 + int(title_width), 30), rank_icon)
                    draw.text((80 + int(title_width), 30), rank_name, font=title_font, fill=(255, 215, 0))
                    break
    except Exception as e:
        print(f"Error loading rank icon: {e}")

    # Draw Wins and Losses
    y_stats = 110
    wins = int(profile_data['wins'])
    losses = int(profile_data['losses'])
    total_games = wins + losses
    win_percent = round((wins / total_games) * 100, 1) if total_games > 0 else 0.0

    draw.text((30, y_stats), "Wins:", font=stat_font, fill=(0, 255, 0))
    draw.text((120, y_stats), str(wins), font=stat_font, fill=(255, 255, 255))
    draw.text((250, y_stats), "Losses:", font=stat_font, fill=(255, 0, 0))
    draw.text((370, y_stats), str(losses), font=stat_font, fill=(255, 255, 255))

    # Draw remaining stats in two evenly spaced columns
    left_column = [
        ("Goals", profile_data['goals']),
        ("Passes", profile_data['passes']),
        ("Win%", f"{win_percent}%")
    ]
    right_column = [
        ("Saves", profile_data['saves']),
        ("Steals", profile_data['steals'])
    ]

    y_base = y_stats + 50
    row_spacing = 36
    for i, (label, value) in enumerate(left_column):
        if label == "Win%":
            draw.text((30, y_base + i * row_spacing), f"{label}:", font=stat_font, fill=(100, 200, 255))
            draw.text((130, y_base + i * row_spacing), str(value), font=stat_font, fill=(255, 255, 255))
        else:
            draw.text((30, y_base + i * row_spacing), f"{label}: {value}", font=stat_font, fill=(200, 200, 200))

    for i, (label, value) in enumerate(right_column):
        draw.text((250, y_base + i * row_spacing), f"{label}: {value}", font=stat_font, fill=(200, 200, 200))

    if not os.path.exists("stat_cards"):
        os.makedirs("stat_cards")
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    path = f"stat_cards/{safe_name}_stats.png"
    bg.save(path)
    return path

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

from PIL import Image, ImageDraw, ImageFont
import os

async def generate_rank_card(user_name, rank, avatar_url=None):
    # Create blank canvas
    bg = Image.new("RGBA", (600, 200), (30, 30, 30, 255))
    draw = ImageDraw.Draw(bg)

    # Load rank icon
    try:
        icon_path = f"assets/ranks/{rank.lower().replace(' ', '_')}.png"
        rank_icon = Image.open(icon_path).resize((128, 128))
        bg.paste(rank_icon, (25, 36), rank_icon)
    except FileNotFoundError:
        print(f"Rank icon for {rank} not found.")

    # Draw avatar if provided
    if avatar_url:
        try:
            import requests
            from io import BytesIO
            response = requests.get(avatar_url)
            avatar_img = Image.open(BytesIO(response.content)).convert("RGBA")
            w, h = avatar_img.size
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            avatar_img = avatar_img.crop((left, top, left + min_dim, top + min_dim)).resize((96, 96), Image.LANCZOS)

            # Create circular mask
            mask = Image.new("L", (96, 96), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 96, 96), fill=255)

            # Create border layer
            border_layer = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            border_draw = ImageDraw.Draw(border_layer)
            border_draw.ellipse((0, 0, 96, 96), outline=(255, 255, 255, 255), width=4)

            # Apply circular mask to avatar
            avatar_circular = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            avatar_circular.paste(avatar_img, (0, 0), mask)

            # Composite border on top
            avatar_final = Image.alpha_composite(avatar_circular, border_layer)

            # Paste on card
            avatar_x = 600 - 96 - 30
            avatar_y = (200 - 96) // 2
            bg.paste(avatar_final, (avatar_x, avatar_y), avatar_final)
        except Exception as e:
            print(f"Error loading avatar: {e}")
        except Exception as e:
            print(f"Error loading avatar: {e}")

    # Draw text
    try:
        base_font_size = 36
        font_path = "/usr/share/fonts/truetype/msttcorefonts/arial.ttf"
        font = ImageFont.truetype(font_path, base_font_size)
    except:
        font = ImageFont.load_default()

    max_width = 304  # prevent text from overlapping avatar
    text = f"{user_name}'s Rank"

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    while text_width > max_width and base_font_size > 12:
        base_font_size -= 1
        try:
            font = ImageFont.truetype(font_path, base_font_size)
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]

    draw.text((170, 70), text, font=font, fill=(255, 255, 255))
    try:
        rank_font = ImageFont.truetype(font_path, 32)
    except:
        try:
            rank_font = ImageFont.truetype("DejaVuSans.ttf", 32)
        except:
            rank_font = ImageFont.load_default()
    draw.text((170, 110), rank, font=rank_font, fill=(255, 215, 0))

    # Save to file
    if not os.path.exists("rank_cards"):
        os.makedirs("rank_cards")
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', user_name)
    path = f"rank_cards/{safe_name}_rank.png"
    bg.save(path)
    return path

# Run the bot
import os
bot.run(os.getenv("DISCORD_BOT_TOKEN"))












