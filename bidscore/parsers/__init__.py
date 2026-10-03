from .base import MailMessage, ParsedOpportunity, Parser
from .bidspeed import BidSpeedParser
from .ebuy import EBuyParser
from .govtribe import GovTribeParser
from .idiq import IdiqPortalParser

PARSERS: list[Parser] = [GovTribeParser(), BidSpeedParser(), EBuyParser(), IdiqPortalParser()]


def parser_for(msg: MailMessage) -> Parser | None:
    return next((p for p in PARSERS if p.matches(msg)), None)


__all__ = ["MailMessage", "ParsedOpportunity", "Parser", "PARSERS", "parser_for"]
