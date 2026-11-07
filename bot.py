import json
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = os.getenv("DATA_FILE", "data.json")


# ---------- Sauvegarde (pour survivre aux redémarrages) ----------
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


# ---------- Embed ----------
def build_embed(poll: dict) -> discord.Embed:
    present = poll["present"]
    absent = poll["absent"]

    embed = discord.Embed(
        title=f"📋 {poll['title']}",
        description="Clique sur un bouton pour indiquer ta présence.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name=f"✅ Présents ({len(present)})",
        value="\n".join(f"<@{uid}>" for uid in present) or "*Personne pour l'instant*",
        inline=True,
    )
    embed.add_field(
        name=f"❌ Absents ({len(absent)})",
        value="\n".join(f"<@{uid}>" for uid in absent) or "*Personne pour l'instant*",
        inline=True,
    )
    return embed


# ---------- Boutons (persistants) ----------
class PresenceView(discord.ui.View):
    def __init__(self):
        # timeout=None + custom_id fixes => les boutons marchent toujours,
        # même après un redémarrage du bot
        super().__init__(timeout=None)

    async def _handle(self, interaction: discord.Interaction, choice: str):
        poll = data.get(str(interaction.message.id))
        if poll is None:
            await interaction.response.send_message(
                "Ce sondage n'existe plus dans ma mémoire.", ephemeral=True
            )
            return

        uid = interaction.user.id
        other = "absent" if choice == "present" else "present"

        if uid in poll[other]:
            poll[other].remove(uid)

        # Re-cliquer sur le même bouton retire la réponse
        if uid in poll[choice]:
            poll[choice].remove(uid)
        else:
            poll[choice].append(uid)

        save_data(data)
        await interaction.response.edit_message(embed=build_embed(poll), view=self)

    @discord.ui.button(label="Présent", emoji="✅", style=discord.ButtonStyle.success,
                       custom_id="presence:present")
    async def present_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "present")

    @discord.ui.button(label="Absent", emoji="❌", style=discord.ButtonStyle.danger,
                       custom_id="presence:absent")
    async def absent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, "absent")


# ---------- Bot ----------
class PresenceBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(PresenceView())  # réactive les boutons des anciens messages
        await self.tree.sync()


bot = PresenceBot()


@bot.tree.command(name="presence", description="Crée une liste de présence avec deux boutons")
@app_commands.describe(titre="Titre (ex : Réunion de vendredi 20h)")
async def presence(interaction: discord.Interaction, titre: str):
    poll = {"title": titre, "present": [], "absent": []}
    await interaction.response.send_message(embed=build_embed(poll), view=PresenceView())
    message = await interaction.original_response()
    data[str(message.id)] = poll
    save_data(data)


@bot.event
async def on_ready():
    print(f"Connecté en tant que {bot.user}")


if not TOKEN:
    raise SystemExit("Token manquant : crée un fichier .env avec DISCORD_TOKEN=ton_token")

bot.run(TOKEN)
