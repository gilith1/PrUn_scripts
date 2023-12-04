import asyncio
import csv
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from httpx import AsyncClient
from periodic import Periodic

logging.basicConfig(
    stream=sys.stdout, level=logging.INFO, format="%(asctime)s (%(levelname)s) : %(message)s"
)
Log = logging.getLogger(__name__)

http_client = AsyncClient()


@dataclass
class UserInventory:
    user: str
    ticker: str
    inventory: list[tuple[str, int]] = field(default_factory=list) # e.g [("UV-351a", 500), ("BEN", 1000)]

    def __post_init__(self) -> None:
        self.hasBeenFiltered: bool = False

    def filterLocations(self, locations):
        self.inventory = [x for x in self.inventory if x[0] in locations]
        self.hasBeenFiltered = True

    def getTotal(self):
        result = 0
        for x in self.inventory:
            result += x[1]
        return result

    def toStrDetailed(self):
        locationDetailsList = [f"{amount} {self.ticker} at {location}" for location, amount in self.inventory]
        details = ", ".join(locationDetailsList)
        return f"{self.user} has " + details

    def toStrSummed(self):
        return "{user} has {total} {ticker}".format(user=self.user, total=self.getTotal(), ticker=self.ticker)

    def toStr(self):
        return self.toStrDetailed() if self.hasBeenFiltered else self.toStrSummed()


@dataclass
class GroupInventory:
    group_id: str
    group_name: str
    api_key: str
    last_updated: Optional[datetime] = None
    inventory: dict[str, dict[str, list[tuple[str, int]]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._initialized: bool = False
        self._fio_url = f"https://rest.fnar.net/csv/inventory?group={self.group_id}&apikey={self.api_key}"

    def is_initialized(self) -> bool:
        return self._initialized

    async def update(self, client: AsyncClient, retries: int = 0):
        response = await client.get(self._fio_url, timeout=5)
        if retries < 1:
            Log.info(f"Updating FIO inventory for group {self.group_name}")
        elif retries > 10:
            Log.error(f"Unable to update FIO data after {retries} retries.")
            raise Exception(f"Retries exhausted. Failed to update inventory for group {self.group_name} (id:{self.group_id})")
        else:
            Log.info(f"Retrying fetch for group {self.group_name}")

        if response.status_code == 200:
            Log.info(f"Updated FIO inventory for group {self.group_name}")
        elif response.status_code == 429:
            Log.info(f"HTTP 429, Retrying in 200ms")
            await asyncio.sleep(0.2)
            await self.update(client, retries=retries+1)
            #  if this call was a 429, we don't want to fall through to the actual update after this
            #  That is handled by whatever retry call gets a 200 response
            return
        else:
            raise Exception(
                f"Failed to update inventory for group {self.group_name} (id:{self.group_id})"
            )

        csvData = csv.DictReader(response.text.split("\r\n"))

        new_inventory: dict[str, dict[str, list[tuple[str, int]]]] = {}
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

    def findInInventory(
        self,
        ticker: str,
        sellerData: "SellerData",
        shouldReturnAll: bool = False,
    ) -> list[UserInventory]:
        result: list[tuple[str, list[tuple[str, int]]]] = []
        # filter for only ticker we want
        for user, inv in self.inventory.items():
            if ticker in inv:
                result.append(UserInventory(user, ticker, inv[ticker]))

        if not shouldReturnAll:
            sellersData = sellerData.get_sellers_for_ticker(ticker)
            sellers = list(sellersData.keys())
            print("Sellers:", str(sellers))
            seller_filtered_result = [x for x in result if x.user in sellers]

            #filter by POS, remove UserInventory if empty after filtering
            for userInv in seller_filtered_result.copy():
                if len(sellersData[userInv.user]) > 0:
                    userInv.filterLocations(sellersData[userInv.user])
                    if userInv.getTotal() <= 0:
                        seller_filtered_result.remove(userInv)
            result = seller_filtered_result

        return sorted(result, key=lambda x: x.getTotal())[::-1]


class SellerData:
    last_updated: Optional[datetime] = None
    data: list[dict[str, str]]
    _seller_sheet_url = "https://docs.google.com/spreadsheets/d/e/2PACX-1vTU0PDYV0CYk5LObZAFcxIXZNshT27WHvy1CZNmm8paC7eMVmTlCk3rxIFyEY6Tbiz0uiIDG8CxGuCm/pub?gid=0&single=true&output=csv"

    def __init__(self):
        self.data = []

    async def update(self, client: AsyncClient):
        response = await client.get(
            self._seller_sheet_url, follow_redirects=True, timeout=5
        )
        if response.status_code == 307:
            Log.info(f"Got a temporary redirect")
        elif response.status_code == 200:
            self.data = list(csv.DictReader(response.text.split("\r\n")))
            self.last_updated = datetime.now()
            Log.info(
                f"Updated seller data from Google Sheet, got response code {response.status_code}"
            )
            if len(self.data) < 1:
                Log.warning(f"It appears that we got an empty response from the sheet")

    def get_sellers_for_ticker(self, ticker: str) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for row in self.data:
            if row["MAT"] == ticker:
                pos_list = [x.strip() for x in row.get("POS", "").split(",") if x != ""]
                result[row["Seller"].upper()] = pos_list

        return result


UpdateInterval = 300
CachedSellersData: SellerData = SellerData()


CachedShipyardInventories = GroupInventory(
    group_id="41707164", group_name="Shipyard Group", api_key=os.getenv("FIO_API_KEY", "")
)
CachedEv1lInventories = GroupInventory(
    group_id="83373923",
    group_name="Ev1l Group",
    api_key=os.getenv("FIO_API_KEY", ""),
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

    Log.info("Updating inventories")

    try:
        async with AsyncClient() as client:
            await asyncio.gather(
                CachedShipyardInventories.update(client),
                CachedEv1lInventories.update(client),
                CachedSellersData.update(client),
                return_exceptions=True,
            )
    except Exception:
        pass


async def whohas(
    ctx: Any, ticker: str, shouldReturnAll: bool = False, forceUpdate: bool = False
) -> tuple[list[UserInventory], datetime | None]:
    Log.info(f"whohas {ticker}")

    # update relevant group inventory
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

    result = (
        inventory.findInInventory(
            ticker=ticker,
            sellerData = CachedSellersData,
            shouldReturnAll=shouldReturnAll,
        ),
        inventory.last_updated,
    )
    # print(str(result))
    print("Full:", str(result))

    return result


async def fetch_inventory_data_periodically():
    p = Periodic(UpdateInterval, updateInventories)
    await p.start(delay=0)
