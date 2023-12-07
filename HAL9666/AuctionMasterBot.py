#!/usr/bin/env python3

import asyncio
import logging
import os
import traceback
from datetime import datetime
from datetime import timedelta
from typing import Any

import discord
from discord.ext import commands

from lib.inventory import fetch_inventory_data_periodically, whohas, UpdateInterval

# from keep_alive_flask import keep_alive
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.typing = False
intents.presences = False

bot = commands.Bot(command_prefix="$", intents=intents)
bot.remove_command("help")

ValidChannels = ("auction", "auction-bot-sandbox")
# ValidChannels = ("auction-bot-sandbox")
# set to True for debugging
ShortenHoursToMinutes = False


currentAuction = None
Log = logging.getLogger(__name__)


def numberToMilSuffixed(number):
    mils = number / 1000000
    if int(mils) == mils:
        mils = int(mils)
    return "{value}mil".format(value=mils)


class Auction:
    def __init__(
        self,
        ctx,
        creator,
        name,
        initialPrice,
        increments,
        duration,
        extension,
        shipCount=1,
    ):
        self.ctx = ctx
        self.creator = creator
        self.name = name
        self.shipCount = shipCount
        self.initialPrice = initialPrice
        self.increments = increments
        self.duration = duration
        self.extension = extension
        delta = (
            timedelta(minutes=self.duration)
            if ShortenHoursToMinutes
            else timedelta(hours=self.duration)
        )
        self.endTime = datetime.now() + delta
        self.timerStopped = False
        self.endTimer = asyncio.create_task(Auction.endTimerTick(self))
        # bid is the following tuple: (bidValue, bidder)
        self.bidHistory = []

    def currentBid(self):
        if not self.bidHistory:
            return None
        return self.bidHistory[-1]

    def prevBid(self):
        if not self.bidHistory or len(self.bidHistory) < self.shipCount + 1:
            return None
        return self.bidHistory[-1 - self.shipCount]

    def tryBid(self, ctx, bidValue):
        minBid = self.getMinBid()
        if bidValue < minBid:
            print(
                "Bid failed: {bidValue} < {minBid}".format(
                    bidValue=bidValue, minBid=minBid
                )
            )
            raise Exception("Minimum bid is {minBid}".format(minBid=minBid))
        delta = (
            timedelta(minutes=self.extension)
            if ShortenHoursToMinutes
            else timedelta(hours=self.extension)
        )
        newEndTime = datetime.now() + delta
        if newEndTime > self.endTime:
            self.endTime = newEndTime
        newBid = (bidValue, ctx.author)
        self.bidHistory.append(newBid)
        self.bidHistory = sorted(self.bidHistory, key=lambda b: b[0])
        print(newBid)
        return newBid

    def getMinBid(self):
        minBid = self.initialPrice
        if len(self.bidHistory) >= self.shipCount:
            minBid = self.bidHistory[-self.shipCount][0] + self.increments
        return minBid

    async def finishAuction(self):
        print("Auction finishing...")
        if self.currentBid():
            for bid in self.bidHistory[
                : -1 - self.shipCount : -1
            ]:  # list slicing magic - last shipCount bids, may be less
                await self.ctx.send(
                    "{name} sold to {mentionBidder} for {finalPrice}! Congratulations!".format(
                        name=self.name,
                        mentionBidder=bid[1].mention,
                        finalPrice=numberToMilSuffixed(bid[0]),
                    )
                )
        else:
            await self.ctx.send(
                "Auction for {name} has ended without any bids...".format(
                    name=self.name
                )
            )
        await self.ctx.send(
            "{mentionCreator}".format(mentionCreator=self.creator.mention)
        )
        global currentAuction
        currentAuction = None

    def stopAuction(self):
        self.timerStopped = True
        global currentAuction
        currentAuction = None

    async def endTimerTick(self):
        # print("endTimerTick", self)
        if self.timerStopped:
            print("Auction timer has stopped!")
            return
        if datetime.now() < self.endTime:
            await asyncio.sleep(60)
            self.endTimer = asyncio.create_task(Auction.endTimerTick(self))
        else:
            self.timerStopped = True
            await self.finishAuction()


def isPriviledgedRole(member):
    return any(role.name == "ev1lc0rp member" for role in member.roles)


def isModeratorRole(member):
    return any(role.name == "moderator" for role in member.roles)


def parseBid(bid):
    result = 0
    multiplier = 1
    if bid.endswith("mil"):
        bid = bid.rstrip("mil")
        multiplier = 1000000
    elif bid.lower().endswith("k"):
        bid = bid.lower().rstrip("k")
        multiplier = 1000
    try:
        result = int(round(float(bid) * multiplier, -4))
    except:
        return None
    return result


def parseDuration(duration):
    try:
        return int(duration)
    except:
        return 0


async def createAuction(
    ctx, creator, name, initialPrice, increments, duration, extension, shipCount=1
):
    print(ctx, name, initialPrice, increments, duration, extension)
    initialPrice = parseBid(initialPrice)
    if initialPrice is None or initialPrice <= 0:
        await ctx.send(
            "{initialPrice} is not a valid value!".format(initialPrice=initialPrice)
        )
        return
    increments = parseBid(increments)
    if increments is None or increments <= 0:
        await ctx.send(
            "{initialPrice} is not a valid value!".format(initialPrice=initialPrice)
        )
        return

    duration = parseDuration(duration)
    extension = parseDuration(extension)
    return Auction(
        ctx, creator, name, initialPrice, increments, duration, extension, shipCount
    )


async def printEndTime(ctx):
    global currentAuction
    if not currentAuction:
        return
    await ctx.send(
        "The auction for {name} ends on <t:{endTime}:f>".format(
            name=currentAuction.name, endTime=int(currentAuction.endTime.timestamp())
        )
    )


@bot.event
async def on_ready():
    print("We have logged in as {0.user}".format(bot))


@bot.command()
async def auctionstart(ctx, name, initialPrice, increments, duration=48, extension=24):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not isPriviledgedRole(ctx.author):
        await ctx.reply("You don't have permissions to create an auction!")
        return

    if currentAuction:
        await ctx.reply(
            "Auction {name} is already running! Stop it first with $auctionstop".format(
                name=currentAuction.name
            )
        )
        return

    print("Starting auction", name)
    currentAuction = await createAuction(
        ctx, ctx.author, name, initialPrice, increments, duration, extension
    )
    if not currentAuction:
        return
    print("currentAuction:", currentAuction)
    await ctx.reply(
        "Starting auction: {name}, min. bid is {initialPrice}. Min. bid increments: {increments}. Auction will last for {duration}h, or {extension}h after last bid".format(
            name=currentAuction.name,
            initialPrice=currentAuction.initialPrice,
            increments=currentAuction.increments,
            duration=currentAuction.duration,
            extension=currentAuction.extension,
        )
    )


@bot.command()
async def auctionmultistart(
    ctx, name, shipCount, initialPrice, increments, duration=48, extension=24
):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not isPriviledgedRole(ctx.author):
        await ctx.reply("You don't have permissions to create an auction!")
        return
    if currentAuction:
        await ctx.reply(
            "Auction {name} is already running! Stop it first with $auctionstop".format(
                name=currentAuction.name
            )
        )
        return
    try:
        shipCount = int(shipCount)
        if shipCount <= 1 or shipCount > 10:
            await ctx.reply("Ship count must be between 1 and 10")
            return
    except:
        await ctx.reply("Invalid format!")
        return

    print("Starting multi auction", name)
    currentAuction = await createAuction(
        ctx,
        ctx.author,
        name,
        initialPrice,
        increments,
        duration,
        extension,
        shipCount=shipCount,
    )
    if not currentAuction:
        return
    print("currentAuction:", currentAuction)
    await ctx.reply(
        "Starting auction: {name}. {shipCount} ships are available, and **{shipCount} highest bids win!**\nMin. bid is {initialPrice}. Min. bid increments: {increments}. Auction will last for {duration}h, or {extension}h after last bid.\nYou **can** buy multiple ships!".format(
            name=currentAuction.name,
            shipCount=shipCount,
            initialPrice=currentAuction.initialPrice,
            increments=currentAuction.increments,
            duration=currentAuction.duration,
            extension=currentAuction.extension,
        )
    )


@bot.command()
async def bid(ctx, bid):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not currentAuction:
        await ctx.reply("There's no auction running currently!")
        return
    try:
        bid = parseBid(bid)
        if bid is None:
            await ctx.reply("Invalid bid!")
            return
        newBid = currentAuction.tryBid(ctx, bid)
        previousBid = currentAuction.prevBid()
        await ctx.message.add_reaction("\N{THUMBS UP SIGN}")
        if previousBid:
            await ctx.send(
                "{newBidder} bids {bid} for {name}! Min. valid bid is now:\n$bid {amount}\n{mentionPrevBidder}, you've been outbid!".format(
                    newBidder=newBid[1].mention,
                    name=currentAuction.name,
                    bid=numberToMilSuffixed(newBid[0]),
                    amount=numberToMilSuffixed(currentAuction.getMinBid()),
                    mentionPrevBidder=previousBid[1].mention,
                )
            )
        else:  # first bid (or multiauction with additional ships are still available)
            print("1st bid")
            await ctx.send(
                "{newBidder} bids {bid} for {name}! Min. valid bid is now:\n$bid {amount}".format(
                    newBidder=newBid[1].mention,
                    name=currentAuction.name,
                    bid=numberToMilSuffixed(newBid[0]),
                    amount=numberToMilSuffixed(currentAuction.getMinBid()),
                )
            )
        await printEndTime(ctx)
    except Exception as ex:
        await ctx.reply(ex)
        print(traceback.format_exc())
        return


@bot.command()
async def status(ctx):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not currentAuction:
        await ctx.reply("There's no auction running!")
        return
    if not currentAuction.currentBid():
        await ctx.send(
            "There are no bids yet for {name}! To start bidding, use this command:\n$bid {amount}\nThe auction ends on <t:{endTime}:f>".format(
                name=currentAuction.name,
                amount=numberToMilSuffixed(currentAuction.getMinBid()),
                endTime=int(currentAuction.endTime.timestamp()),
            )
        )
        return
    await ctx.send(
        "Current bid is {bid}. Min. valid bid is now:\n$bid {amount}".format(
            bid=numberToMilSuffixed(currentAuction.currentBid()[0]),
            amount=numberToMilSuffixed(currentAuction.getMinBid()),
        )
    )
    if currentAuction.shipCount > 1:
        msgStr = "Current winners:\n"
        winnersMsgs = []
        for bid in currentAuction.bidHistory[
            : -1 - currentAuction.shipCount : -1
        ]:  # list slicing magic - last shipCount bids, may be less
            winnersMsgs.append("{bidder} at {bid}".format(
                bidder=bid[1].display_name, bid=numberToMilSuffixed(bid[0])
            ))
        msgStr += "\n".join(winnersMsgs)
        await ctx.send(msgStr)
    await printEndTime(ctx)


@bot.command()
async def auctionstop(ctx):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not currentAuction:
        await ctx.reply("There's no auction running currently!")
        return
    if not isPriviledgedRole(ctx.author):
        await ctx.reply("You don't have permissions to stop an auction!")
        return

    await ctx.send("Stopping {name} auction".format(name=currentAuction.name))
    currentAuction.stopAuction()
    currentAuction = None


@bot.command()
async def help(ctx):
    global currentAuction
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    await ctx.send(
        '$auctionstart [name] [initial_price] [price_increments] [duration_hours] [extension_hours]\nStarts a new auction. First bid must be at least equal to *initial_price*, each new bid must be bigger by at least *price_increments*. The auction will last for *duration_hours* (default 48), or *extension_hours* (default 24) after a new bid has been placed.\\Example:\n$auctionstart "WCB ship" 2mil 50k'
    )
    await ctx.send("$auctionstop\nStops the current auction.")
    await ctx.send(
        "$bid [price]\nPlaces a new bid. Examples:\n$bid 4mil\nbid 4.25mil\n"
    )
    await ctx.send("$status\nShows current auction status")


@bot.command(name="whohas")
async def whohas_command(ctx: Any, ticker: str, all: str = "", force: str = ""):
    if ctx.author == bot.user or ctx.author.bot:
        return
    if ctx.channel.name not in ValidChannels:
        return
    if not isPriviledgedRole(ctx.author):
        await ctx.reply("You don't have permissions to run this command!")
        return

    shouldReturnAll = all.lower() == "all"
    forceUpdate = (
        force.lower() == "force" or all.lower() == "force"
    )  #  force ends up in "all" if "$whohas FE force"

    result, last_updated = await whohas(
        ctx=ctx,
        ticker=ticker.upper(),
        shouldReturnAll=shouldReturnAll,
        forceUpdate=forceUpdate,
    )

    if last_updated is not None:
        age = int((datetime.now() - last_updated).total_seconds())
        seconds_til_refresh = UpdateInterval - age

    if len(result) == 0:
        await ctx.reply(f"As far as I know, nobody has {ticker}")
        return

    formattedResult = [
        f"{user} has {amount} {ticker.upper()}" for (user, amount) in result
    ]
    print("Filtered:", str(formattedResult))
    last_updated_text = (
        f"(updated {age} seconds ago, refresh in {seconds_til_refresh}s)"
        if last_updated is not None
        else ("There is no data currently. Try adding 'force' to the command")
    )
    await ctx.reply(f"{last_updated_text}\n" + "\n".join(formattedResult))


@bot.command()
async def clearchannel(ctx):
    if ctx.channel.name != "auction":
        return
    if not isModeratorRole(ctx.author):
        await ctx.reply("You don't have permissions to clear the channel!")
        return
    await ctx.channel.purge()


async def main():
    await fetch_inventory_data_periodically()
    await bot.start(os.getenv("DISCORD_TOKEN"))


# keep_alive()
if __name__ == "__main__":
    asyncio.run(main())
