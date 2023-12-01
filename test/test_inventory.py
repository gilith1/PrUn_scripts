import csv
import io
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from HAL9666.lib.inventory import getSellerData, whohas


@pytest.mark.asyncio
@patch("HAL9666.lib.inventory.http_client.get")
@patch("HAL9666.lib.inventory.getSellerData")
async def test_inventory(mock_getSellerData, http_client_get):
    general_csv_stream = create_csv(
        [
            {
                "Username": "Kindling",
                "Ticker": "C",
                "Amount": "200",
                "NaturalId": "UV-351a",
            }
        ]
    )

    shipyard_csv_stream = create_csv(
        [{"Username": "Felmer", "Ticker": "WCB", "Amount": "3", "NaturalId": "UV-351a"}]
    )

    fake_fio_response = MagicMock()
    fake_fio_response.status_code = 200
    fake_fio_response.text = general_csv_stream

    http_client_get.return_value = fake_fio_response

    mock_getSellerData.return_value = {"Kindling": [], "Felmer": [], "Gilith": []}

    inv = await whohas(AsyncMock(), "C", forceUpdate=True)
    assert inv[0] == ("Kindling", 200)

    general_csv_stream = create_csv(
        [
            {
                "Username": "Kindling",
                "Ticker": "C",
                "Amount": "200",
                "NaturalId": "UV-351a",
            },
            {
                "Username": "Felmer",
                "Ticker": "C",
                "Amount": "250",
                "NaturalId": "UV-351a",
            },
        ]
    )
    fake_fio_response.text = general_csv_stream

    inv = await whohas(AsyncMock(), "C", forceUpdate=True)

    assert inv[0] == ("Felmer", 250)
    assert inv[1] == ("Kindling", 200)

    fake_fio_response.status_code = 500

    ctx = MagicMock()
    ctx.reply = AsyncMock()
    inv = await whohas(ctx, "C", forceUpdate=True)

    assert inv[0] == ("Felmer", 250)
    assert inv[1] == ("Kindling", 200)

    fake_fio_response.status_code = 200
    fake_fio_response.text = shipyard_csv_stream
    inv = await whohas(ctx, "WCB", forceUpdate=True)

    assert len(inv) == 1
    assert inv[0] == ("Felmer", 3)

    fake_fio_response.status_code = 500
    inv = await whohas(ctx, "C", forceUpdate=True)

    assert inv[0] == ("Felmer", 250)
    assert inv[1] == ("Kindling", 200)


@pytest.mark.asyncio
@patch("HAL9666.lib.inventory.http_client.get")
@patch("HAL9666.lib.inventory.getSellerData")
async def test_pos_filter(mock_getSellerData, http_client_get):
    # when someone has set POS filter, we should only count the amounts from those locations
    mock_getSellerData.return_value = {
        "Kindling": ["UV-351a"],
        "Felmer": ["XG-521b"],
        "Gilith": [],
    }

    csv_stream = create_csv(
        [
            {
                "Username": "Kindling",
                "Ticker": "C",
                "Amount": "200",
                "NaturalId": "UV-351a",
            },
            {
                "Username": "Kindling",
                "Ticker": "C",
                "Amount": "100",
                "NaturalId": "KW-688c",
            },
            {
                "Username": "Gilith",
                "Ticker": "C",
                "Amount": "100",
                "NaturalId": "UV-351a",
            },
            {
                "Username": "Felmer",
                "Ticker": "C",
                "Amount": "250",
                "NaturalId": "UV-351a",
            },
        ]
    )

    fio_response = MagicMock()
    fio_response.status_code = 200
    fio_response.text = csv_stream

    http_client_get.return_value = fio_response

    inv = await whohas(MagicMock(), "C", False, forceUpdate=True)

    assert len(inv) == 2
    assert ("Kindling", 200) in inv
    assert ("Gilith", 100) in inv


# TODO: test shouldReturnAll = False

@pytest.mark.asyncio
@patch("HAL9666.lib.inventory.http_client.get")
async def test_getSellersData(http_client_get):
    csv_data = [
        {"MAT": "C", "Seller": "Kindling", "POS": "KW-688c", "Price/u": "300"},
        {"MAT": "C", "Seller": "Felmer", "POS": "", "Price/u": "300"},
        {"MAT": "WCB", "Seller": "Felmer", "POS": "UV-351a", "Price/u": "300000"},
    ]
    sheets_csv_stream = create_csv(csv_data)

    fake_fio_response = MagicMock()
    fake_fio_response.status_code = 200
    fake_fio_response.text = sheets_csv_stream
    http_client_get.return_value = fake_fio_response

    sellers = await getSellerData("C")

    assert isinstance(sellers, dict)
    assert len(sellers) == 2

    sellers = await getSellerData("WCB")
    assert len(sellers) == 1


def create_csv(csv_data: list[dict[str, str]]) -> str:
    if len(csv_data) < 1:
        raise ValueError("List must have atleast 1 entry")

    csv_stream = io.StringIO()
    general_csv_writer = csv.DictWriter(csv_stream, list(csv_data[0].keys()))
    general_csv_writer.writeheader()
    for row in csv_data:
        general_csv_writer.writerow(row)

    return csv_stream.getvalue()
