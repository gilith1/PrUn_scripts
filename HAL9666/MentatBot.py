#!/usr/bin/env python3

# Discord bot for Dune Awakening guild

import asyncio
import logging
import math
import os
import traceback
from datetime import datetime
from datetime import timedelta

import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True
intents.reactions = True
intents.message_content = True
intents.typing = False
intents.presences = False

bot = commands.Bot(command_prefix="$", intents=intents)
bot.remove_command("help")

#TODO configure
ValidChannels = ("【🤖】da-bot",)
# ValidChannels = ("auction-bot-sandbox")
# set to True for debugging
ShortenHoursToMinutes = False

Jobs = []

matSynonyms = {
        "TO": ("T", "TI", "TIO", "TO", "TORE", "TYTAN", "TYTANIUM", "TITANIUM", "TIT", "Titanium Ore"),
        "SVO": ("SVO", "SV", "STRAVI", "STRAV", "Stravidium Ore"),
        "SSAND": ("SSAND", "SS", "SPICE", "Spice Sand"),
        "SVF": ("SVF", "SF", "Stravidium Fiber"),
        "PLAST": ("PLAST", "PL", "Plastanium Ingot"),
        "SM": ("SM", "Spice Melange"),
        "SRES": ("SR", "SRES", "Spice Residue"),
        "ALU": {"ALU", "AL", "Aluminium"},
        "ALO": {"ALO", "Aluminium Ore"},
        "DUR": {"DUR", "D", "Duraluminium Ingot"},
        "JAS": {"JAS", "J", "Jasmium"},
        "COB": {"COB", "Cobalt Paste"},
        "ERY": {"ERY", "ER", "E", "EC", "Erythrite Crystal"},
        "H2O": {"H2O", "WATER", "woda"}
}

def parseAmount(amount):
    if isinstance(amount, int):
        return amount
    result = 0
    multiplier = 1
    if amount.lower().endswith("k"):
        amount = amount.lower().rstrip("k")
        multiplier = 1000
    try:
        result = int(amount) * multiplier
    except:
        return None
    return result

class Material:
    def __init__(
        self,
        amount,
        matName
    ):
        self.amount = parseAmount(amount)
        self.mat = ""
        
        matName = matName.upper();
        for k, v in matSynonyms.items():
            if matName in v:
                self.mat = k
                break
    def __str__(self):
        return "{a} {m}".format(a=self.amount, m=matSynonyms[self.mat][-1])

class Recipe:
    def __init__(self, ingredients, products, batchTime, h2o):
        self.ingredients = ingredients
        self.products = products
        self.batchTime = batchTime
        self.h2o = h2o
        
    def produces(self, matSyn):
        for prod in self.products:
            if prod.mat == matSyn:
                return True
        return False
        
    def tryProcess(self, materials):
        totalBatches = 1000000
        print(self.ingredients)
        for i in self.ingredients:
            batches = 0
            for m in materials:
                if i.mat == m.mat and m.amount >= i.amount:
                    batches = int(m.amount / i.amount)
                    break
            if batches == 0:
                return None
            totalBatches = min(totalBatches, batches)
            print(str(m), totalBatches, self.products[0].amount)
        
        result = {"products": (Material(p.amount * totalBatches, p.mat) for p in self.products),
                  "duration": self.batchTime * totalBatches,
                  "H2O": self.h2o * totalBatches}
        return result
        
Recipes = (
    Recipe((Material(4, "TO"), Material(1, "SVF")),
        (Material(1, "PLAST"),), 20, 1250
    ),
    Recipe((Material(3, "SVO"),),
        (Material(1, "SVF"),), 20, 100
    ),
    Recipe((Material(10000, "SSAND"),),
        (Material(200, "SM"), Material(1000, "SRES")), 2700, 75000
    ),
    Recipe((Material(1, "ALU"), Material(3, "JAS")),
        (Material(1, "DUR"),), 4, 500
    ),
    Recipe((Material(4, "ALO"),),
        (Material(1, "ALU"),), 20, 200
    ),
    Recipe((Material(2, "ERY"),),
        (Material(1, "COB"),), 10, 75
    )
)

def parseMaterials(argList):
    result = []
    matSet = set()
    for i in range(0, len(argList), 2):
        print(i, argList[i])
        amount = parseAmount(argList[i])
        if amount <= 0 or amount >= 1000000:
            return []
        mat = Material(amount, argList[i+1])
        if mat.mat:
            if mat.mat in matSet:
                print("powtórzony materiał "+mat.mat)
                return []
            result.append(mat)
            matSet.add(mat.mat)
        else:
            print("Nieznany materiał "+mat.mat)
            return []
    return result

async def endTimerTick(job):
    try:
        now = datetime.now()
        if now < job["endTime"]:
            await asyncio.sleep((job["endTime"] - now).total_seconds())
            job["endTimer"] = asyncio.create_task(endTimerTick(job))
        else:
            await job["ctx"].reply("Produkcja zakończona!")
            job["in_progress"] = False
            Jobs.remove(job)
    finally:
        pass


@bot.event
async def on_reaction_add(reaction, user):
    if reaction.emoji == '\U000025B6':
        for job in Jobs:
            if job["jobMessage"] == reaction.message and user == reaction.message.reference.cached_message.author:
                job["endTime"] = datetime.now() + timedelta(seconds=job["duration"])
                job["in_progress"] = True
                await job["ctx"].reply("OK, produkcja zakończy się <t:{endTime}:f>".format(endTime=int(job["endTime"].timestamp())))
                job["endTimer"] = asyncio.create_task(endTimerTick(job))
                return

@bot.event
async def on_reaction_remove(reaction, user):
    print("on_reaction_remove")
    if reaction.emoji == '\U000025B6':
        for job in Jobs:
            print(job)
            if job["jobMessage"] == reaction.message and user == reaction.message.reference.cached_message.author:
                job["endTimer"].cancel()
                await job["ctx"].reply("Produkcja anulowana!")
                job["in_progress"] = False
                return

@bot.event
async def on_ready():
    print("We have logged in as {0.user}".format(bot))

def appendJob(job):
    global Jobs
    #TODO add job limit
    Jobs.append(job)

def bodiesPluralForm(bodycount):
    if bodycount <= 1:
        return "ciało"
    elif bodycount < 5:
        return "ciała"
    else:
        return "ciał"

@bot.command()
async def refine(ctx, *args):
    global Jobs
    if ctx.author == bot.user or ctx.author.bot:
        #await ctx.reply("błędny user")
        return
    if ctx.channel.name not in ValidChannels:
        #await ctx.reply("błędny kanał "+ctx.channel.name)
        return
    try:
        mats = parseMaterials(args)
        
        if not mats:
            await ctx.reply("Błędna lista materiałów!")
            return
        for r in Recipes:
            result = r.tryProcess(mats)
            if result:
                response = "Możesz wyprodukować **"
                for m in result["products"]:
                    response += str(m) + " "
                response += "**\nProdukcja zajmie **{duration}**".format(duration=timedelta(seconds=result["duration"]))
                if result["H2O"]:
                    response += "\nZużyjesz **{h2o}ml** wody (przygotuj **{bodies}** {plural} by uzupełnić wode)".format(h2o=result["H2O"], bodies=math.ceil(result["H2O"] / 45000), plural=bodiesPluralForm((math.ceil(result["H2O"] / 45000))))
                response += "\nKliknij reakcję :arrow_forward: kiedy rozpoczniesz produkcje (możesz kliknąć ponownie by anulować)"
                await ctx.message.add_reaction("\N{THUMBS UP SIGN}")
                result["jobMessage"] = await ctx.reply(response)
                result["startR"] = await result["jobMessage"].add_reaction('\U000025B6')
                result["ctx"] = ctx
                appendJob(result)
                
                return
        await ctx.reply("Niestety, nie znam recepty żeby to przetworzyć!")
        return
    except:
        await ctx.reply("Błędne polecenie!")
        #await ctx.reply(traceback.format_exc())
        print(traceback.format_exc())
        return

@bot.command()
async def help(ctx):
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    await ctx.send(
        "TODO :)"
    )

async def main():
    await bot.start(os.getenv("DISCORD_TOKEN"))
    #await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
