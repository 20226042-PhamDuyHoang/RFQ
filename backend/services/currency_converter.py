import logging
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# Cache don gian de khong goi API qua nhieu lan
_rate_cache: dict = {}


def get_exchange_rate(from_currency: str, to_currency: str = "USD") -> Optional[float]:
    """
    Lay ty gia quy doi tu from_currency sang to_currency.
    Su dung exchangerate-api.com.
    Cache ket qua trong session.
    """
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()

    if from_currency == to_currency:
        return 1.0

    cache_key = f"{from_currency}_{to_currency}"
    if cache_key in _rate_cache:
        return _rate_cache[cache_key]

    try:
        url = f"{settings.exchange_rate_base_url}/{settings.exchange_rate_api_key}/pair/{from_currency}/{to_currency}"
        response = httpx.get(url, timeout=10)
        data = response.json()

        if data.get("result") == "success":
            rate = data["conversion_rate"]
            _rate_cache[cache_key] = rate
            logger.info("Exchange rate %s -> %s: %s", from_currency, to_currency, rate)
            return rate
        else:
            logger.warning("Exchange rate API error: %s", data)
            return None

    except Exception as exc:
        logger.error("Error fetching exchange rate %s->%s: %s", from_currency, to_currency, exc)
        return None


def convert_to_usd(amount: float, currency: str) -> Optional[float]:
    """Quy doi so tien ve USD."""
    if currency.upper() == "USD":
        return amount

    rate = get_exchange_rate(currency, "USD")
    if rate is not None:
        return round(amount * rate, 2)

    # Fallback: dung ty gia co dinh gan dung (phong truong hop API khong kha dung)
    fallback_rates = {
        "EUR": 1.08,
        "GBP": 1.26,
        "JPY": 0.0067,
        "CNY": 0.14,
        "KRW": 0.00075,
        "VND": 0.000041,
    }
    fallback = fallback_rates.get(currency.upper())
    if fallback:
        logger.warning("Using fallback rate for %s: %s", currency, fallback)
        return round(amount * fallback, 2)

    logger.error("Cannot convert %s to USD, no rate available", currency)
    return None
