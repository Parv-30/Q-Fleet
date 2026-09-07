"""Real major-port coordinate lookup table.

``searoute`` (see ``routing.py``) computes distances between lat/lon points,
not city names, so anything that wants to route "Mumbai to Rotterdam" needs a
name -> coordinate table first. This module is that table: a hand-curated
set of major world shipping hubs with real coordinates (harbor-area
approximations, not exact berth locations -- plenty precise for route-shape
and distance estimation), covering the major global trade regions so the
synthetic voyage generator and any future routing UI have a reasonable
spread of real origin/destination choices.

Coordinates are plain (lat, lon) in degrees; ``routing.py`` is responsible
for flipping to searoute's expected [lon, lat] order.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Port:
    name: str
    country: str
    lat: float
    lon: float


# Real major ports/port cities, real approximate coordinates, grouped by
# region purely for readability -- lookup is by name only.
_PORTS: list[Port] = [
    # South Asia
    Port("Mumbai", "India", 18.9750, 72.8258),
    Port("Chennai", "India", 13.0827, 80.2707),
    Port("Colombo", "Sri Lanka", 6.9271, 79.8612),
    # East Asia
    Port("Shanghai", "China", 31.2304, 121.4737),
    Port("Shenzhen", "China", 22.5431, 114.0579),
    Port("Hong Kong", "China", 22.3193, 114.1694),
    Port("Busan", "South Korea", 35.1796, 129.0756),
    Port("Tokyo", "Japan", 35.6762, 139.6503),
    Port("Yokohama", "Japan", 35.4437, 139.6380),
    Port("Qingdao", "China", 36.0671, 120.3826),
    # Southeast Asia
    Port("Singapore", "Singapore", 1.2644, 103.8200),
    Port("Port Klang", "Malaysia", 3.0044, 101.3934),
    Port("Ho Chi Minh City", "Vietnam", 10.7626, 106.6602),
    Port("Manila", "Philippines", 14.5995, 120.9842),
    Port("Jakarta", "Indonesia", -6.1045, 106.8862),
    # Middle East
    Port("Dubai", "UAE", 25.2697, 55.2962),
    Port("Jebel Ali", "UAE", 25.0118, 55.0617),
    Port("Jeddah", "Saudi Arabia", 21.4858, 39.1925),
    # Europe
    Port("Rotterdam", "Netherlands", 51.9244, 4.4777),
    Port("Amsterdam", "Netherlands", 52.3676, 4.9041),
    Port("Antwerp", "Belgium", 51.2194, 4.4025),
    Port("Hamburg", "Germany", 53.5511, 9.9937),
    Port("Felixstowe", "United Kingdom", 51.9539, 1.3510),
    Port("Piraeus", "Greece", 37.9475, 23.6367),
    Port("Valencia", "Spain", 39.4699, -0.3763),
    Port("Genoa", "Italy", 44.4056, 8.9463),
    # North America - East Coast
    Port("New York", "United States", 40.6643, -74.0454),
    Port("Savannah", "United States", 32.0809, -81.0912),
    Port("Norfolk", "United States", 36.8508, -76.2859),
    Port("Halifax", "Canada", 44.6488, -63.5752),
    # North America - West Coast
    Port("Los Angeles", "United States", 33.7361, -118.2437),
    Port("Long Beach", "United States", 33.7701, -118.1937),
    Port("Oakland", "United States", 37.8044, -122.2712),
    Port("Seattle", "United States", 47.6062, -122.3493),
    Port("Vancouver", "Canada", 49.2827, -123.1207),
    # South America
    Port("Santos", "Brazil", -23.9608, -46.3336),
    Port("Buenos Aires", "Argentina", -34.6037, -58.3816),
    Port("Callao", "Peru", -12.0566, -77.1181),
    # Africa
    Port("Durban", "South Africa", -29.8587, 31.0218),
    Port("Cape Town", "South Africa", -33.9249, 18.4241),
    Port("Lagos", "Nigeria", 6.4531, 3.3958),
    # Oceania
    Port("Sydney", "Australia", -33.8688, 151.2093),
    Port("Melbourne", "Australia", -37.8136, 144.9631),
]

PORT_CATALOG: dict[str, Port] = {port.name: port for port in _PORTS}


class UnknownPortError(KeyError):
    """Raised when a port name isn't in ``PORT_CATALOG``."""

    def __init__(self, name: str):
        super().__init__(
            f"Unknown port {name!r}. See port_catalog.PORT_CATALOG for the "
            "list of known port names."
        )
        self.name = name


def get_port(name: str) -> Port:
    """Look up a port by exact name, raising ``UnknownPortError`` if absent."""
    try:
        return PORT_CATALOG[name]
    except KeyError:
        raise UnknownPortError(name) from None


def list_ports() -> list[Port]:
    """All known ports, for populating a dropdown or similar UI."""
    return list(_PORTS)
