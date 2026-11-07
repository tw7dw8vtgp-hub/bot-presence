import json
import os
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = os.getenv("DATA_FILE", "data.json")
MAX_HISTORY = 200  # nombre max d'actions gardées par liste
TZ = ZoneInfo("Europe/Paris")

CHOICES = {
    "present": "✅ Présents",
    "maybe": "❓ Peut-être",
    "absent": "❌ Absents",
}

ACTION_LABEL = {
    "present": "a répondu ✅ Présent",
    "maybe": "a répondu ❓ Peut-être",
    "absent": "a répondu ❌ Absent",
    "removed": "a retiré sa réponse",
}


# ---------- Sauvegarde ----------
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()


def normalize(poll: dict) -> dict:
    """Ajoute les champs manquants (pour les anciennes listes)."""
    poll.setdefault("present", [])
    poll.setdefault("absent", [])
    poll.setdefault("maybe", [])
    poll.setdefault("history", [])
    poll.setdefault("channel_id", None)
    poll.setdefault("role_id", None)
    poll.setdefault("event_at", None)
    poll.setdefault("remind_at", None)
    poll.setdefault("reminded", False)
    return poll


def find_poll(channel_id: int, message_id: Optional[str] = None):
    """Retourne (clé, liste) : celle demandée, sinon la dernière du salon."""
    if message_id:
        key = message_id.strip()
        return (key, data[key]) if key in data else (None, None)
    ids = [int(k) for k, v in data.items() if v.get("channel_id") == channel_id]
    if not ids:
        return None, None
    key = str(max(ids))
    return key, data[key]


# ---------- Embed ----------
def build_embed(poll: dict) -> discord.Embed:
    normalize(poll)
    lines = ["Clique sur un bouton pour indiquer ta présence."]
    if poll["event_at"]:
        lines.append(f"📅 <t:{poll['event_at']}:F> (<t:{poll['event_at']}:R>)")
    if poll["role_id"]:
        lines.append(f"👥 Concerne : <@&{poll['role_id']}>")
    embed = discord.Embed(
        title=f"📋 {poll['title']}",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )
    for key, label in CHOICES.items():
        users = poll[key]
        embed.add_field(
            name=f"{label} ({len(users)})",
            value="\n".join(f"<@{uid}>" for uid in users) or "*Personne*",
            inline=True,
        )
    return embed


# ---------- Boutons (persistants) ----------
class PresenceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _handle(self, interaction: discord.Interaction, choice: str):
        poll = data.get(str(interaction.message.id))
        if poll is None:
            await interaction.response.send_message(
                "Ce sondage n'existe plus dans ma mémoire.", ephemeral=True
            )
            return
        normalize(poll)

        uid = interaction.user.id
        if uid in poll[choice]:
            poll[choice].remove(uid)
            action = "removed"
        else:
            for key in CHOICES:
                if uid in poll[key]:
                    poll[key].remove(uid)
            poll[choice].append(uid)
            action = choice

        poll["history"].append({"user": uid, "action": action, "time": int(time.time())})
        poll["history"] = poll["history"][-MAX_HISTORY:]

        save_data(data)
        await interaction.response.edit_message(embed=build_embed(poll), view=self)

    @discord.ui.button(label="Présent", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id="presence:present")
    async def present_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "present")

    @discord.ui.button(label="Peut-être", emoji="❓", style=discord.ButtonStyle.secondary,
                       custom_id="presence:maybe")
    async def maybe_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "maybe")

    @discord.ui.button(label="Absent", emoji="❌", style=discord.ButtonStyle.danger,
                       custom_id="presence:absent")
    async def absent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "absent")


# ---------- Bot ----------
class PresenceBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # nécessaire pour connaître les membres d'un rôle
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(PresenceView())
        await self.tree.sync()
        check_reminders.start()


bot = PresenceBot()


# ---------- Rappels ----------
async def send_reminder(message_id: str, poll: dict) -> int:
    """Relance ceux qui n'ont pas répondu (+ les 'peut-être'). Retourne le nombre de personnes."""
    normalize(poll)
    channel_id = poll.get("channel_id")
    if not channel_id:
        return 0
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return 0

    answered = set(poll["present"]) | set(poll["absent"]) | set(poll["maybe"])
    targets = []
    role = channel.guild.get_role(poll["role_id"]) if poll.get("role_id") else None
    if role:
        targets += [m.id for m in role.members if not m.bot and m.id not in answered]
    targets += poll["maybe"]
    if not targets:
        return 0

    link = f"https://discord.com/channels/{channel.guild.id}/{channel.id}/{message_id}"
    mentions = [f"<@{uid}>" for uid in targets]
    try:
        for i in range(0, len(mentions), 80):
            head = f"⏰ **Rappel : {poll['title']}**\n" if i == 0 else ""
            await channel.send(
                f"{head}{' '.join(mentions[i:i + 80])}\nMerci de répondre à la liste : {link}"
            )
    except discord.Forbidden:
        return 0
    return len(targets)


@tasks.loop(seconds=60)
async def check_reminders():
    now = int(time.time())
    for message_id, poll in list(data.items()):
        normalize(poll)
        if poll["remind_at"] and not poll["reminded"] and poll["remind_at"] <= now:
            poll["reminded"] = True
            save_data(data)
            await send_reminder(message_id, poll)


@check_reminders.before_loop
async def before_reminders():
    await bot.wait_until_ready()


# ---------- Commandes ----------
@bot.tree.command(name="presence", description="Crée une liste de présence avec des boutons")
@app_commands.describe(
    titre="Titre (ex : Réunion de vendredi)",
    role="Rôle des personnes attendues (pour pouvoir les relancer)",
    date="Date et heure de l'événement : JJ/MM/AAAA HH:MM (heure de Paris)",
    rappel_heures="Rappel automatique X heures avant l'événement (24 par défaut)",
)
async def presence(
    interaction: discord.Interaction,
    titre: str,
    role: Optional[discord.Role] = None,
    date: Optional[str] = None,
    rappel_heures: app_commands.Range[int, 1, 720] = 24,
):
    poll = normalize({"title": titre, "channel_id": interaction.channel_id})
    if role:
        poll["role_id"] = role.id

    if date:
        try:
            dt = datetime.strptime(date.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
        except ValueError:
            await interaction.response.send_message(
                "Date invalide. Format attendu : JJ/MM/AAAA HH:MM, par exemple 14/11/2026 20:00.",
                ephemeral=True,
            )
            return
        event_at = int(dt.timestamp())
        remind_at = event_at - rappel_heures * 3600
        now = int(time.time())
        if event_at <= now:
            await interaction.response.send_message(
                "Cette date est déjà passée.", ephemeral=True
            )
            return
        if remind_at <= now:
            await interaction.response.send_message(
                f"Le rappel serait déjà passé. Choisis un `rappel_heures` plus petit "
                f"(l'événement est dans moins de {rappel_heures} h).",
                ephemeral=True,
            )
            return
        poll["event_at"] = event_at
        poll["remind_at"] = remind_at

    await interaction.response.send_message(embed=build_embed(poll), view=PresenceView())
    message = await interaction.original_response()
    data[str(message.id)] = poll
    save_data(data)


@bot.tree.command(name="rappel", description="Relance maintenant ceux qui n'ont pas répondu")
@app_commands.describe(message_id="ID du message de présence (par défaut : le dernier de ce salon)")
async def rappel(interaction: discord.Interaction, message_id: Optional[str] = None):
    key, poll = find_poll(interaction.channel_id, message_id)
    if poll is None:
        await interaction.response.send_message(
            "Je ne trouve pas de liste de présence ici.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    count = await send_reminder(key, poll)
    if count:
        await interaction.followup.send(f"Rappel envoyé à {count} personne(s).", ephemeral=True)
    else:
        await interaction.followup.send(
            "Personne à relancer : tout le monde a répondu, ou aucun rôle n'a été choisi "
            "à la création de la liste.",
            ephemeral=True,
        )


@bot.tree.command(name="historique", description="Affiche qui a répondu quoi, et quand")
@app_commands.describe(message_id="ID du message de présence (par défaut : le dernier de ce salon)")
async def historique(interaction: discord.Interaction, message_id: Optional[str] = None):
    key, poll = find_poll(interaction.channel_id, message_id)
    if poll is None:
        await interaction.response.send_message(
            "Je ne trouve pas de liste de présence ici.", ephemeral=True
        )
        return

    normalize(poll)
    history = poll["history"]
    if not history:
        await interaction.response.send_message(
            "Personne n'a encore répondu à cette liste.", ephemeral=True
        )
        return

    text = ""
    for h in reversed(history):
        line = f"<t:{h['time']}:f> — <@{h['user']}> {ACTION_LABEL.get(h['action'], h['action'])}\n"
        if len(text) + len(line) > 3900:
            text += "…"
            break
        text += line

    embed = discord.Embed(
        title=f"🕘 Historique : {poll['title']}",
        description=text,
        color=discord.Color.dark_grey(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")


if not TOKEN:
    raise SystemExit("Token manquant : crée un fichier .env avec DISCORD_TOKEN=ton_token")

bot.run(TOKEN)
