import asyncio
import csv
from datetime import datetime
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Any, Optional

import httpx
from httpx import AsyncClient
from periodic import Periodic

Log = logging.getLogger(__name__)

http_client = httpx.AsyncClient()


@dataclass
class GroupInventory:
    group_id: str
    group_name: str
    api_key: str
    last_updated: Optional[datetime] = None
    inventory: dict[str, dict[str, list[tuple[str, int]]]] = field(default_factory=dict)

    def __post_init__(self):
        self._initialized: bool = False
        self._fio_url = f"https://rest.fnar.net/csv/inventory?group={self.group_id}&apikey={self.api_key}"

    def is_initialized(self):
        return self._initialized

    async def update(self, client: AsyncClient):
        response = await client.get(self._fio_url)
        if response.status_code != 200:
            raise Exception(
                f"Failed to update inventory for group {self.group_name} (id:{self.group_id})"
            )

        csvData = csv.DictReader(response.text.split("\r\n"))

        new_inventory = {}
        for row in csvData:
            if row["Username"] not in new_inventory:
                new_inventory[row["Username"]] = {}
            if row["Ticker"] not in new_inventory[row["Username"]]:
                new_inventory[row["Username"]][row["Ticker"]] = []
            new_inventory[row["Username"]][row["Ticker"]].append(
                (row["NaturalId"], int(row["Amount"]))
            )

        self.inventory = new_inventory
        self.last_updated = datetime.now()
        self._initialized = True


class SellerData:
    last_updated: Optional[datetime] = None
    data: list[dict[str, str]]
    _seller_sheet_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTU0PDYV0CYk5LObZAFcxIXZNshT27WHvy1CZNmm8paC7eMVmTlCk3rxIFyEY6Tbiz0uiIDG8CxGuCm/pub?gid=0&single=true&output=csv"

    def __init__(self):
        self.data = []

    async def update(self, client):
        response = await client.get(self._seller_sheet_url)
        if response.status_code == 200:
            self.data = list(csv.DictReader(response.text.split("\r\n")))
            self.last_updated = datetime.now()

    def get_sellers_for_ticker(self, ticker: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in self.data:
            pos_list = [x.strip() for x in row.get("POS", "").split(",") if x != ""]
            if row["MAT"] == ticker:
                result[row["Seller"].upper()] = pos_list

        return result


CachedSellersData: SellerData = SellerData()

CachedShipyardInventories = GroupInventory(
    group_id="41707164", group_name="Shipyard Group", api_key=os.getenv("FIO_API_KEY")
)
CachedEv1lInventories = GroupInventory(
    group_id="83373923",
    group_name="Ev1l Group",
    api_key=os.getenv("FIO_API_KEY"),
)
ShipPartTickers = (
    "BR1",
    "BR2",  # bridges
    "CQT",
    "CQS",
    "CQM",
    "CQL",  # crew q
    "FFC",
    "SFE",
    "MFE",
    "LFE",  # FFC, emitters
    "GEN",
    "ENG",
    "FSE",
    "AEN",
    "HTE",  # STL engines
    "RCT",
    "QCR",
    "HPR",
    "HYR",  # FTL engines
    "SSL",
    "MSL",
    "LSL",  # STL fuel tanks
    "SFL",
    "MFL",
    "LFL",  # FTL fuel tanks
    "TCB",
    "VSC",
    "SCB",
    "MCB",
    "LCB",
    "WCB",
    "VCB",  # cargo bays
    "SSC",
    "LHB",
    "BHP",
    "RHP",
    "HHP",
    "AHP",  # hull plates, SSC
    "BGS",
    "AGS",
    "STS",  # misc
    "BPT",
    "APT",
    "BWH",
    "AWH",  # whipple shields and thermal protection
    "RDS",
    "RDL",  # repair drones
    "BRP",
    "ARP",
    "SRP",  # anti-radiation plates
)


async def updateInventories():
    global CachedShipyardInventories
    global CachedEv1lInventories
    global CachedSellersData

    try:
        async with AsyncClient() as client:
            await asyncio.gather(
                CachedShipyardInventories.update(client),
                CachedEv1lInventories.update(client),
                CachedSellersData.update(client),
            )
    except Exception:
        pass


async def findInInventory(
    ticker: str,
    inventory: GroupInventory,
    sellerData: SellerData,
    shouldReturnAll: bool = False,
) -> list[tuple[str, int]]:
    result: list[tuple[str, list[tuple[str, int]]]] = []
    # filter for only ticker we want
    for user, inv in inventory.inventory.items():
        if ticker in inv:
            result.append((user, inv[ticker]))

    if not shouldReturnAll:
        sellersData = sellerData.get_sellers_for_ticker(ticker)
        sellers = [s for s in sellersData.keys()]
        print("Sellers:", str(sellers))
        seller_filtered_result = [x for x in result if x[0] in sellers]

        pos_filtered_result: list[tuple[str, list[tuple[str, int]]]] = []

        for user, inv_rows in seller_filtered_result:
            filtered_inv_rows = [
                x
                for x in inv_rows
                if x[0] in sellersData[user] or len(sellersData[user]) == 0
            ]
            if len(filtered_inv_rows) > 0:
                pos_filtered_result.append((user, filtered_inv_rows))

        result = pos_filtered_result

    summed_inventories: list[tuple[str, int]] = []
    # sum up amounts from all remaining locations
    for user, inv_rows in result:
        amount = sum([x[1] for x in inv_rows])
        if amount > 0:
            summed_inventories.append((user, amount))

    return sorted(summed_inventories, key=lambda x: x[1])[::-1]


async def whohas(
    ctx: Any, ticker: str, shouldReturnAll: bool = False, forceUpdate: bool = False
) -> list[tuple[str, int]]:
    Log.info("whohas", ticker)

    # update relevant group inventory
    global CachedShipyardInventories
    global CachedEv1lInventories
    global CachedSellersData
    isShipPartTicker = ticker in ShipPartTickers

    inventory = CachedShipyardInventories if isShipPartTicker else CachedEv1lInventories
    if forceUpdate or not inventory.is_initialized():
        try:
            async with AsyncClient() as client:
                await inventory.update(client)
        except Exception as e:
            await ctx.reply(
                "Error updating inventory from FIO. Falling back to cached data"
            )

    if forceUpdate:
        async with AsyncClient() as client:
            await CachedSellersData.update(client)

    result = await findInInventory(
        ticker=ticker,
        inventory=inventory,
        sellerData=CachedSellersData,
        shouldReturnAll=shouldReturnAll
    )
    # print(str(result))
    print("Full:", str(result))

    return result


async def fetch_inventory_data_periodically():
    p = Periodic(300, updateInventories)
    await p.start()
