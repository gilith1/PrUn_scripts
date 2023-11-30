import csv
import logging
import os
from typing import Iterable, Any

import requests

Log = logging.getLogger(__name__)

#corp spreadsheet exported as CSV
OfferingsCsvUrl = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTU0PDYV0CYk5LObZAFcxIXZNshT27WHvy1CZNmm8paC7eMVmTlCk3rxIFyEY6Tbiz0uiIDG8CxGuCm/pub?gid=0&single=true&output=csv"
CachedSellersData:  Iterable[dict[str, str]] = {}

FioInventoryUrl = "https://rest.fnar.net/csv/inventory?group={group}&apikey={apikey}"
FioInventoryShipyardGroup = "41707164"
FioInventoryEv1lGroup = "83373923"
CachedShipyardInventories: dict[str, dict[str, list[tuple[str,int]]]] = {}
CachedEv1lInventories: dict[str, dict[str, list[tuple[str,int]]]] = {}
ShipPartTickers = (
    "BR1",
    "BR2",  #bridges
    "CQT",
    "CQS",
    "CQM",
    "CQL",  #crew q
    "FFC",
    "SFE",
    "MFE",
    "LFE",  #FFC, emitters
    "GEN",
    "ENG",
    "FSE",
    "AEN",
    "HTE",  #STL engines
    "RCT",
    "QCR",
    "HPR",
    "HYR",  #FTL engines
    "SSL",
    "MSL",
    "LSL",  #STL fuel tanks
    "SFL",
    "MFL",
    "LFL",  #FTL fuel tanks
    "TCB",
    "VSC",
    "SCB",
    "MCB",
    "LCB",
    "WCB",
    "VCB",  #cargo bays
    "SSC",
    "LHB",
    "BHP",
    "RHP",
    "HHP",
    "AHP",  #hull plates, SSC
    "BGS",
    "AGS",
    "STS",  #misc
    "BPT",
    "APT",
    "BWH",
    "AWH",  #whipple shields and thermal protection
    "RDS",
    "RDL",  #repair drones
    "BRP",
    "ARP",
    "SRP"  #anti-radiation plates
)


def updateInventory(groupId: str, inventory: dict[str, dict[str, list[tuple[str, int]]]]):
    fioUrl = FioInventoryUrl.format(
        apikey=os.getenv("FIO_API_KEY"),
        group=groupId)
    response = requests.get(fioUrl)
    if response.status_code != 200:
        raise Exception(f"Failed to update inventory for groupId {groupId}")

    csvData = csv.DictReader(response.text.split("\r\n"))

    inventory.clear()
    for row in csvData:
        if row["Username"] not in inventory:
            inventory[row["Username"]] = {}
        if row["Ticker"] not in inventory[row["Username"]]:
            inventory[row["Username"]][row["Ticker"]] = []
        inventory[row["Username"]][row["Ticker"]].append((row["NaturalId"], int(row["Amount"])))


def findInInventory(ticker: str, inventory: dict[str, dict[str, list[tuple[str, int]]]],
                    shouldReturnAll: bool = False) -> list[tuple[str, int]]:
    result: list[tuple[str, list[tuple[str, int]]]] = []
    # filter for only ticker we want
    for (user, inv) in inventory.items():
        if ticker in inv:
            result.append((user, inv[ticker]))

    if not shouldReturnAll:
        sellersData = getSellerData(ticker)
        sellers = [s for s in sellersData.keys()]
        print("Sellers:", str(sellers))
        seller_filtered_result = [x for x in result if x[0] in sellers]

        pos_filtered_result: list[tuple[str, list[tuple[str, int]]]] = []

        for (user, inv_rows) in seller_filtered_result:
            filtered_inv_rows = [x for x in inv_rows if x[0] in sellersData[user] or len(sellersData[user]) == 0]
            if len(filtered_inv_rows) > 0:
                pos_filtered_result.append((user, filtered_inv_rows))

        result = pos_filtered_result

    summed_inventories: list[tuple[str, int]] = []
    # sum up amounts from all remaining locations
    for (user, inv_rows) in result:
        amount = sum([x[1] for x in inv_rows])
        if amount > 0:
            summed_inventories.append((user, amount))

    return sorted(summed_inventories, key=lambda x: x[1])[::-1]


def getSellerData(ticker: str) -> dict[str, list[str]]:
    global CachedSellersData
    result = {}
    response = requests.get(OfferingsCsvUrl)
    if response.status_code == 200:
        CachedSellersData = list(csv.DictReader(response.text.split("\r\n")))
    if CachedSellersData:
        result: dict[str, list[str]] = {}
        for row in CachedSellersData:
            pos_list = [x.strip() for x in row.get("POS", "").split(',') if x != ""]
            if row["MAT"] == ticker:
                result[row['Seller'].upper()] = pos_list

    return result


async def whohas(ctx: Any, ticker: str, shouldReturnAll: bool = False) -> list[tuple[str, int]]:
    Log.info("whohas", ticker)

    # update relevant group inventory
    global CachedShipyardInventories
    global CachedEv1lInventories
    isShipPartTicker = ticker in ShipPartTickers

    group = FioInventoryShipyardGroup if isShipPartTicker else FioInventoryEv1lGroup
    inventory = CachedShipyardInventories if isShipPartTicker else CachedEv1lInventories
    try:
        updateInventory(groupId=group, inventory=inventory)
    except Exception as e:
        await ctx.reply(
            "Error updating inventory from FIO. Falling back to cached data"
        )

    result = findInInventory(ticker, inventory, shouldReturnAll)
    # print(str(result))
    print("Full:", str(result))

    return result
