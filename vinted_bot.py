import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
import os
import random
import json

BOT_TOKEN    = os.environ["BOT_TOKEN"]
CHANNEL_ID   = int(os.environ["CHANNEL_ID"])
PING_ROLE_ID = int(os.environ["PING_ROLE_ID"]) if os.environ.get("PING_ROLE_ID") else None

CHECK_INTERVAL = 20   # Toutes les 20 secondes on traite un petit lot
BATCH_SIZE     = 3    # Nombre de mots-clés par cycle (3 requêtes max)
PRIX_MAX_PLN   = 210
MAX_AGE_HOURS  = 2

# ════════════════════════════════════════════════════════════════
# Les 26 mots-clés sont divisés en lots de 3.
# Chaque cycle de 20s traite 3 mots-clés.
# Tous les mots-clés sont couverts toutes les ~3 minutes.
# Vinted ne voit jamais plus de 3 requêtes à la fois → pas de blocage.
# ════════════════════════════════════════════════════════════════

KEYWORDS = [
    "nike running brs",
    "nike trail",
    "nike running division",
    "nike windrunner",
    "nike wind runner",
    "nike tech",
    "nike tech fleece",
    "nike tech hoodie",
    "nike trail jacket",
    "nike running jacket",
    "nike running pants",
    "nike running half zip",
    "nike trail pants",
    "demi zip nike running",
    "demi zip under armour",
    "under armour running",
    "kurtka nike trail",
    "kurtka nike running",
    "kurtka nike running division",
    "spodnie nike running",
    "spodnie nike running division",
    "nike running division jacket",
    "nike running division pants",
    "kurtka under armour",
    "jacket under armour",
    "spodnie under armour",
]

VINTED_API  = "https://www.vinted.pl/api/v2/catalog/items"
VINTED_ITEM = "https://www.vinted.pl/items/{id}"
VINTED_HOME = "https://www.vinted.pl"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
]

COLOR_DEAL   = 0x00FF88
COLOR_NORMAL = 0x5865F2
COLOR_WARN   = 0xFF6B35

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

seen_ids: set[str] = set()
vinted_cookie: str | None = None
cookie_last_refresh: datetime = datetime.now(timezone.utc) - timedelta(hours=1)

# Index du lot courant (on avance de BATCH_SIZE à chaque cycle)
current_batch_index: int = 0


def get_headers() -> dict:
    return {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer":         "https://www.vinted.pl/",
        "Origin":          "https://www.vinted.pl",
        "DNT":             "1",
    }


async def refresh_cookie(session: aiohttp.ClientSession) -> str | None:
    global cookie_last_refresh
    try:
        hdrs = get_headers()
        hdrs["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        async with session.get(VINTED_HOME, headers=hdrs, allow_redirects=True) as r:
            cookies = session.cookie_jar.filter_cookies(VINTED_HOME)
            result = "; ".join(f"{k}={v.value}" for k, v in cookies.items())
            if result:
                cookie_last_refresh = datetime.now(timezone.utc)
                print(f"[COOKIE] ✅ Nouveau cookie récupéré")
            return result or None
    except Exception as e:
        print(f"[COOKIE] Erreur : {e}")
        return None


async def fetch_one_keyword(session: aiohttp.ClientSession, keyword: str) -> list[dict]:
    """Récupère les annonces pour UN seul mot-clé avec délai aléatoire."""
    global vinted_cookie

    params = {
        "search_text": keyword,
        "order":       "newest_first",
        "per_page":    96,
        "price_to":    PRIX_MAX_PLN,
        "currency":    "PLN",
    }
    hdrs = get_headers()
    if vinted_cookie:
        hdrs["Cookie"] = vinted_cookie

    timeout = aiohttp.ClientTimeout(total=20)

    # Délai humain aléatoire entre 1 et 4 secondes
    await asyncio.sleep(random.uniform(1.0, 4.0))

    try:
        async with session.get(VINTED_API, params=params, headers=hdrs, timeout=timeout) as resp:

            if resp.status in (401, 403):
                print(f"[FETCH] '{keyword}' → Bloqué ({resp.status}), renouvellement cookie...")
                vinted_cookie = await refresh_cookie(session)
                hdrs["Cookie"] = vinted_cookie or ""
                await asyncio.sleep(random.uniform(3.0, 6.0))
                # Réessaie une fois
                async with session.get(VINTED_API, params=params, headers=hdrs, timeout=timeout) as r2:
                    text = await r2.text()
                    if not text or text.strip() == "":
                        return []
                    try:
                        data = json.loads(text)
                        return data.get("items", [])
                    except Exception:
                        return []

            elif resp.status == 200:
                text = await resp.text()
                if not text or text.strip() == "":
                    print(f"[FETCH] '{keyword}' → Réponse vide")
                    return []
                try:
                    data = json.loads(text)
                    items = data.get("items", [])
                    print(f"[FETCH] '{keyword}' → {len(items)} annonces ✅")
                    return items
                except Exception:
                    print(f"[FETCH] '{keyword}' → JSON invalide")
                    return []
            else:
                print(f"[FETCH] '{keyword}' → Status {resp.status}")
                return []

    except asyncio.TimeoutError:
        print(f"[FETCH] '{keyword}' → Timeout")
        return []
    except Exception as e:
        print(f"[FETCH] '{keyword}' → {e}")
        return []


def keyword_matches(title: str, keyword: str) -> bool:
    title_lower = title.lower()
    return all(word in title_lower for word in keyword.lower().split())


def is_recent(item: dict) -> bool:
    ts = item.get("created_at_ts") or item.get("created_at")
    if ts is None:
        return True
    try:
        if isinstance(ts, (int, float)):
            published = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            published = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - published) <= timedelta(hours=MAX_AGE_HOURS)
    except Exception:
        return True


def time_ago(item: dict) -> str:
    ts = item.get("created_at_ts") or item.get("created_at")
    if ts is None:
        return "?"
    try:
        if isinstance(ts, (int, float)):
            published = datetime.fromtimestamp(ts, tz=timezone.utc)
        else:
            published = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        minutes = int((datetime.now(timezone.utc) - published).total_seconds() // 60)
        return f"{minutes} min" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}"
    except Exception:
        return "?"


def build_embed(item: dict, matched_keyword: str) -> discord.Embed:
    title     = item.get("title", "Sans titre")
    price_obj = item.get("price", {})
    price_val = float(price_obj.get("amount", 0)) if isinstance(price_obj, dict) else float(price_obj or 0)
    currency  = price_obj.get("currency_code", "PLN") if isinstance(price_obj, dict) else "PLN"
    item_url  = VINTED_ITEM.format(id=item.get("id", ""))
    brand     = item.get("brand_title") or "—"
    size      = item.get("size_title") or "—"
    condition = item.get("status") or "—"
    seller    = item.get("user", {}).get("login", "?")
    seller_id = item.get("user", {}).get("id", "")
    photos    = item.get("photos", [])
    image_url = photos[0].get("url", "") if photos else ""
    ratio     = price_val / PRIX_MAX_PLN
    badge     = "🔥 SUPER DEAL" if ratio < 0.5 else ("✅ BON PRIX" if ratio < 0.8 else "📦 NOUVEAU")
    color     = COLOR_DEAL if ratio < 0.5 else (COLOR_NORMAL if ratio < 0.8 else COLOR_WARN)

    embed = discord.Embed(
        title=f"{badge} — {title}",
        url=item_url,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="💰 Prix",     value=f"**{price_val:.0f} {currency}**",                      inline=True)
    embed.add_field(name="⏱️ Posté",   value=f"il y a {time_ago(item)}",                              inline=True)
    embed.add_field(name="📐 Taille",  value=size,                                                     inline=True)
    embed.add_field(name="✨ État",     value=condition,                                                inline=True)
    embed.add_field(name="👕 Marque",  value=brand,                                                    inline=True)
    embed.add_field(name="👤 Vendeur", value=f"[{seller}](https://www.vinted.pl/member/{seller_id})",  inline=True)
    embed.add_field(name="🔍 Mot-clé", value=f"`{matched_keyword}`",                                  inline=False)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text=f"Vinted Bot Pro  •  vinted.pl 🇵🇱  •  max {PRIX_MAX_PLN} PLN  •  < {MAX_AGE_HOURS}h")
    return embed


@tasks.loop(seconds=CHECK_INTERVAL)
async def check_vinted():
    global vinted_cookie, current_batch_index, cookie_last_refresh

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"[BOT] Canal {CHANNEL_ID} introuvable !")
        return

    # Renouvelle le cookie toutes les 8 minutes
    age_cookie = (datetime.now(timezone.utc) - cookie_last_refresh).total_seconds()
    if age_cookie > 480 or not vinted_cookie:
        async with aiohttp.ClientSession() as s:
            vinted_cookie = await refresh_cookie(s)

    # Sélectionne le lot courant
    batch = KEYWORDS[current_batch_index: current_batch_index + BATCH_SIZE]

    # Avance l'index (boucle sur toute la liste)
    current_batch_index = (current_batch_index + BATCH_SIZE) % len(KEYWORDS)

    ts_str = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts_str}] 🔍 Lot : {batch} (index {current_batch_index}/{len(KEYWORDS)})")

    async with aiohttp.ClientSession() as session:
        if not vinted_cookie:
            vinted_cookie = await refresh_cookie(session)

        # Traite les mots-clés du lot UN PAR UN avec délai humain
        new_count = 0
        for keyword in batch:
            items = await fetch_one_keyword(session, keyword)

            for item in items:
                item_id = str(item.get("id", ""))
                if not item_id or item_id in seen_ids:
                    continue
                if not keyword_matches(item.get("title", ""), keyword):
                    continue
                if not is_recent(item):
                    continue
                price_obj = item.get("price", {})
                price_val = float(price_obj.get("amount", 0)) if isinstance(price_obj, dict) else float(price_obj or 0)
                if price_val > PRIX_MAX_PLN:
                    continue

                seen_ids.add(item_id)
                ping_content = None
                if PING_ROLE_ID and channel.guild:
                    role = channel.guild.get_role(PING_ROLE_ID)
                    if role:
                        ping_content = role.mention
                await channel.send(content=ping_content, embed=build_embed(item, keyword))
                await asyncio.sleep(0.5)
                new_count += 1

        if new_count:
            print(f"[{ts_str}] ✅ {new_count} nouvelle(s) annonce(s) envoyée(s)")
        else:
            print(f"[{ts_str}] 🔄 Rien de nouveau ({len(seen_ids)} IDs mémorisés)")


@check_vinted.before_loop
async def before_check():
    await bot.wait_until_ready()


@bot.command(name="watchlist")
async def show_watchlist(ctx):
    total_cycles = -(-len(KEYWORDS) // BATCH_SIZE)
    temps_tour = total_cycles * CHECK_INTERVAL
    embed = discord.Embed(
        title=f"📋 {len(KEYWORDS)} mots-clés surveillés",
        description="\n".join(f"`{kw}`" for kw in KEYWORDS),
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="💰 Prix max",       value=f"{PRIX_MAX_PLN} PLN",          inline=True)
    embed.add_field(name="⏱️ Âge max",       value=f"{MAX_AGE_HOURS}h",             inline=True)
    embed.add_field(name="🔄 Lot / cycle",   value=f"{BATCH_SIZE} mots-clés",       inline=True)
    embed.add_field(name="⏰ Tour complet",   value=f"~{temps_tour}s",               inline=True)
    embed.add_field(name="📦 IDs mémorisés", value=str(len(seen_ids)),              inline=True)
    embed.add_field(name="🌍 Plateforme",    value="vinted.pl 🇵🇱",                  inline=True)
    await ctx.send(embed=embed)


@bot.command(name="status")
async def status_cmd(ctx):
    embed = discord.Embed(
        title="🤖 Statut du bot",
        color=0x00FF88 if check_vinted.is_running() else 0xFF0000,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🟢 Actif",         value="Oui" if check_vinted.is_running() else "Non", inline=True)
    embed.add_field(name="🎯 Mots-clés",     value=str(len(KEYWORDS)),                            inline=True)
    embed.add_field(name="📦 IDs mémorisés", value=str(len(seen_ids)),                            inline=True)
    embed.add_field(name="💰 Prix max",       value=f"{PRIX_MAX_PLN} PLN",                        inline=True)
    embed.add_field(name="⏱️ Âge max",       value=f"{MAX_AGE_HOURS}h",                          inline=True)
    embed.add_field(name="🌍 Plateforme",    value="vinted.pl 🇵🇱",                               inline=True)
    await ctx.send(embed=embed)


@bot.command(name="stop")
@commands.has_permissions(administrator=True)
async def stop_cmd(ctx):
    check_vinted.stop()
    await ctx.send("🛑 Surveillance stoppée.")


@bot.command(name="start")
@commands.has_permissions(administrator=True)
async def start_cmd(ctx):
    if not check_vinted.is_running():
        check_vinted.start()
        await ctx.send("✅ Surveillance relancée !")
    else:
        await ctx.send("⚠️ Déjà active.")


@bot.command(name="clearmem")
@commands.has_permissions(administrator=True)
async def clearmem_cmd(ctx):
    count = len(seen_ids)
    seen_ids.clear()
    await ctx.send(f"🗑️ {count} IDs supprimés.")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🔒 Commande réservée aux admins.")
    elif not isinstance(error, commands.CommandNotFound):
        print(f"[CMD ERROR] {error}")


@bot.event
async def on_ready():
    global current_batch_index
    print("╔══════════════════════════════════════════════════╗")
    print(f"║  ✅ Connecté : {bot.user.name}")
    print(f"║  🌍 Plateforme : vinted.pl 🇵🇱")
    print(f"║  🎯 {len(KEYWORDS)} mots-clés — {BATCH_SIZE} par cycle de {CHECK_INTERVAL}s")
    print(f"║  💰 Prix max : {PRIX_MAX_PLN} PLN  |  ⏱️ Âge max : {MAX_AGE_HOURS}h")
    print(f"║  🔄 Tour complet : ~{(-(-len(KEYWORDS)//BATCH_SIZE))*CHECK_INTERVAL}s")
    print("╚══════════════════════════════════════════════════╝")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"vinted.pl 🛍️ {len(KEYWORDS)} recherches"
    ))

    print("\n🔄 Pré-chargement des annonces existantes (3 par 3)...")
    async with aiohttp.ClientSession() as session:
        global vinted_cookie
        vinted_cookie = await refresh_cookie(session)

        for i in range(0, len(KEYWORDS), BATCH_SIZE):
            batch = KEYWORDS[i:i + BATCH_SIZE]
            for keyword in batch:
                items = await fetch_one_keyword(session, keyword)
                for item in items:
                    item_id = str(item.get("id", ""))
                    if item_id:
                        seen_ids.add(item_id)
            await asyncio.sleep(2)

    current_batch_index = 0
    print(f"✅ {len(seen_ids)} annonces mémorisées — aucune ne sera renvoyée.")
    print("🚀 Surveillance lancée !\n")
    check_vinted.start()


bot.run(BOT_TOKEN)
