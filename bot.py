#!/usr/bin/env python3

import asyncio
import atexit
import os
import time
import random

import discord
from discord.ext import commands
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont
from strictyaml import load, Bool, Int, Map, Seq, Str, YAMLError
import requests
from io import BytesIO


SCRIPT_NAME = "NT Pug Bot"
SCRIPT_VERSION = "0.3.0"
SCRIPT_URL = "https://github.com/Rainyan/discord-bot-ntpug"

CFG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                        "config.yml")
assert os.path.isfile(CFG_PATH)
with open(CFG_PATH, "r") as f:
    YAML_CFG_SCHEMA = Map({
        "bot_secret_token": Str(),
        "avatar_download_url": Str(),
        "command_prefix": Str(),
        "pug_channel_name": Str(),
        "num_players_required_total": Int(),
        "debug_allow_requeue": Bool(),
        "queue_polling_interval_secs": Int(),
        "discord_avatar_change_rate_limit_secs": Int(),
    })
    CFG = load(f.read(), YAML_CFG_SCHEMA)
assert CFG is not None

DEFAULT_AVATAR_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                   "static", "avatars", "default.png")
if not os.path.isfile(DEFAULT_AVATAR_PATH):
    r = requests.get(CFG["avatar_download_url"].value)
    r.raise_for_status()
    with Image.open(BytesIO(r.content)).convert("RGBA") as image:
        image.save(DEFAULT_AVATAR_PATH)
    assert os.path.isfile(DEFAULT_AVATAR_PATH)

bot = commands.Bot(command_prefix=CFG["command_prefix"].value)
NUM_PLAYERS_REQUIRED = CFG["num_players_required_total"].value
assert NUM_PLAYERS_REQUIRED % 2 == 0, "Need even number of players"
DEBUG_ALLOW_REQUEUE = CFG["debug_allow_requeue"].value
PUG_CHANNEL_NAME = CFG["pug_channel_name"].value
BOT_SECRET_TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or \
    CFG["bot_secret_token"].value

print(f"Now running {SCRIPT_NAME} v.{SCRIPT_VERSION} -- {SCRIPT_URL}",
      flush=True)


class PugStatus():
    def __init__(self, players_required=NUM_PLAYERS_REQUIRED):
        self.jin_players = []
        self.nsf_players = []
        self.prev_puggers = []
        self.players_required_total = players_required
        self.players_per_team = int(self.players_required_total / 2)
        # Subtract the rate limit, so we try avatar update on script start
        self.last_changed = int(time.time()) - \
            CFG["discord_avatar_change_rate_limit_secs"].value
        # <0, so it always updates on script init
        self.last_avatar = -1

    def reset(self):
        self.prev_puggers = self.jin_players + self.nsf_players
        self.jin_players = []
        self.nsf_players = []
        self.last_changed = int(time.time())

    def player_join(self, player, team=None):
        if not DEBUG_ALLOW_REQUEUE and \
                (player in self.jin_players or player in self.nsf_players):
            return False, (f"{player.mention} You are already queued! "
                           "If you wanted to un-PUG, please use **"
                           f"{CFG['command_prefix'].value}unpug** instead.")
        if team is None:
            team = random.randint(0, 1)  # flip a coin between jin/nsf
        if team == 0:
            if len(self.jin_players) < self.players_per_team:
                self.jin_players.append(player)
                self.last_changed = int(time.time())
                return True, ""
        else:
            if len(self.nsf_players) < self.players_per_team:
                self.nsf_players.append(player)
                self.last_changed = int(time.time())
                return True, ""
        return False, f"{player.mention} Sorry, this PUG is currently full!"

    def player_leave(self, player):
        num_before = self.num_queued()
        self.jin_players = [p for p in self.jin_players if p != player]
        self.nsf_players = [p for p in self.nsf_players if p != player]
        num_after = self.num_queued()

        left_queue = (num_after != num_before)
        if left_queue:
            self.last_changed = int(time.time())
            return True, ""
        else:
            return False, (f"{player.mention} You are not currently in the "
                           "PUG queue")

    def num_queued(self):
        return len(self.jin_players) + len(self.nsf_players)

    def num_expected(self):
        return self.players_required_total

    def is_full(self):
        return self.num_queued() >= self.num_expected()

    def start_pug(self):
        if len(self.jin_players) == 0 or len(self.nsf_players) == 0:
            self.reset()
            return False, "Error: team was empty"
        msg = "**PUG is now ready!**\n"
        msg += "_Jinrai players:_\n"
        for player in self.jin_players:
            msg += f"{player.mention}, "
        msg = msg[:-2]  # trailing ", "
        msg += "\n_NSF players:_\n"
        for player in self.nsf_players:
            msg += f"{player.mention}, "
        msg = msg[:-2]  # trailing ", "
        msg += (f"\n\nTeams unbalanced? Use **{CFG['command_prefix'].value}"
                "scramble** to suggest new random teams.")
        self.last_changed = int(time.time())
        return True, msg

    async def update_avatar(self):
        delta_time = int(time.time()) - self.last_changed
        if delta_time < CFG["discord_avatar_change_rate_limit_secs"].value:
            return
        num_puggers = min(10, self.num_queued())
        if num_puggers == self.last_avatar:
            return
        with open(DEFAULT_AVATAR_PATH, "rb") as f_original:
            format = "RGBA"
            with Image.open(f_original).convert(format) as i:
                with Image.new(format, i.size, (255, 255, 255, 0)) as layer:
                    px_to_pt_ratio = 4/3  # 1 pixel = 1.333... font pts
                    text_scale = 0.75  # 1.0 meaning "entire image"
                    font_size = int(text_scale * i.size[0] * px_to_pt_ratio)
                    font = font_manager.FontProperties(family="sans-serif",
                                                       weight="bold")
                    font_file = font_manager.findfont(font)
                    font = ImageFont.truetype(font_file, font_size)
                    xy = (int(i.size[0] / 2), int(i.size[1] / 2))
                    text = f"{num_puggers}"
                    rgba = (255, 0, 186, 128)
                    draw = ImageDraw.Draw(layer)
                    # Just use plain avatar if 0 puggers queued
                    if num_puggers > 0:
                        draw.text(xy=xy, text=text, fill=rgba, font=font,
                                  anchor="mm")
                    save_path = os.path.join(os.path.dirname(
                        os.path.realpath(__file__)), "static", "avatars",
                        "temp.png")
                    final_image = Image.alpha_composite(i, layer)
                    final_image.save(save_path)
                    with open(save_path, "rb") as f:
                        try:
                            await bot.user.edit(avatar=f.read())
                            self.last_avatar = num_puggers
                        except discord.errors.HTTPException as e:
                            # This is (probably) a rate limit.
                            # No detailed enough HTTP status code response,
                            # nor "Retry-After" headers, so I guess we just
                            # try again some other time ¯\_(ツ)_/¯
                            pass
        # Set the last_changed epoch regardless of success to ensure we aren't
        # getting API banned for spamming avatar update requests beyond the
        # rate limit.
        self.last_changed = int(time.time())
        return


pug_guilds = {}


@bot.command(brief="Test if bot is active")
async def ping(ctx):
    await ctx.send("pong")


@bot.command(brief="Join the PUG queue")
async def pug(ctx):
    if ctx.guild not in pug_guilds or not ctx.channel.name == PUG_CHANNEL_NAME:
        return
    response = ""
    join_success, response = pug_guilds[ctx.guild].player_join(
        ctx.message.author)
    if join_success:
        response = (f"{ctx.message.author.name} has joined the PUG queue "
                    f"({pug_guilds[ctx.guild].num_queued()} / "
                    f"{pug_guilds[ctx.guild].num_expected()})")
    await ctx.send(f"{response}")


@bot.command(brief="Leave the PUG queue")
async def unpug(ctx):
    if ctx.guild not in pug_guilds or not ctx.channel.name == PUG_CHANNEL_NAME:
        return

    leave_success, msg = pug_guilds[ctx.guild].player_leave(ctx.message.author)
    if leave_success:
        msg = (f"{ctx.message.author.name} has left the PUG queue "
               f"({pug_guilds[ctx.guild].num_queued()} / "
               f"{pug_guilds[ctx.guild].num_expected()})")
    await ctx.send(msg)


@bot.command(brief="Empty the server's PUG queue")
async def clearpuggers(ctx):
    if ctx.guild not in pug_guilds or not ctx.channel.name == PUG_CHANNEL_NAME:
        return
    pug_guilds[ctx.guild].reset()
    await ctx.send(f"{ctx.message.author.name} has reset the PUG queue")


@bot.command(brief="Get new random teams suggestion for the latest PUG")
async def scramble(ctx):
    msg = ""
    if len(pug_guilds[ctx.guild].prev_puggers) == 0:
        msg = f"{ctx.message.author.mention} No previous PUG found to scramble"
    else:
        random.shuffle(pug_guilds[ctx.guild].prev_puggers)
        msg = f"{ctx.message.author.name} suggests scrambled teams:\n"
        # Adding a random human readable phrase here to work as an identifier
        # for this shuffle, so specific shuffle results are easier to refer to
        # in voice chat, if there are many of them.
        msg += f"_(random shuffle id: {random_human_readable_phrase()})_\n"
        msg += "_Jinrai players:_\n"
        for i in range(int(len(pug_guilds[ctx.guild].prev_puggers) / 2)):
            msg += f"{pug_guilds[ctx.guild].prev_puggers[i].name}, "
        msg = msg[:-2]  # trailing ", "
        msg += "\n_NSF players:_\n"
        for i in range(int(len(pug_guilds[ctx.guild].prev_puggers) / 2),
                       len(pug_guilds[ctx.guild].prev_puggers)):
            msg += f"{pug_guilds[ctx.guild].prev_puggers[i].name}, "
        msg = msg[:-2]  # trailing ", "
        msg += ("\n\nTeams still unbalanced? Use **"
                f"{CFG['command_prefix'].value}"
                "scramble** to suggest new random teams.")
        await ctx.send(msg)


@bot.command(brief="List players currently queueing for PUG")
async def puggers(ctx):
    if ctx.guild not in pug_guilds or not ctx.channel.name == PUG_CHANNEL_NAME:
        return

    msg = (f"{pug_guilds[ctx.guild].num_queued()} / "
           f"{pug_guilds[ctx.guild].num_expected()} player(s) currently "
           "queued")

    if pug_guilds[ctx.guild].num_queued() > 0:
        all_players_queued = pug_guilds[ctx.guild].jin_players + \
            pug_guilds[ctx.guild].nsf_players
        msg += ": "
        for player in all_players_queued:
            msg += f"{player.name}, "
        msg = msg[:-2]  # trailing ", "
    await ctx.send(msg)


async def poll_if_pug_ready():
    while True:
        # Iterating and caching per-guild to support multiple Discord channels
        # simultaneously using the same bot instance with their own independent
        # player pools.
        for guild in bot.guilds:
            for channel in guild.channels:
                if channel.name != PUG_CHANNEL_NAME:
                    continue
                if guild not in pug_guilds:
                    pug_guilds[guild] = PugStatus()

                # Can't set avatar per-guild, so can only support number
                # avatars with single guild.
                if len(bot.guilds) == 1:
                    await pug_guilds[guild].update_avatar()

                if pug_guilds[guild].is_full():
                    pug_start_success, msg = pug_guilds[guild].start_pug()
                    if pug_start_success:
                        await channel.send(msg)
                        pug_guilds[guild].reset()
        await asyncio.sleep(CFG["queue_polling_interval_secs"].value)


def random_human_readable_phrase():
    with open("nouns.txt", "r") as f:
        nouns = f.readlines()
    with open("adjectives.txt", "r") as f:
        adjectives = f.readlines()
    phrase = (f"{adjectives[random.randint(0, len(adjectives))]} "
              f"{nouns[random.randint(0, len(nouns))]}")
    return phrase.replace("\n", "").lower()


asyncio.Task(poll_if_pug_ready())
asyncio.Task(bot.run(BOT_SECRET_TOKEN))
while True:
    continue
