from eventsourcing.infrastructure.event_sourced_repo import EventSourcedRepository
from quantdsl.domain.model.market_calibration import MarketCalibration, Repository


class MarketCalibrationRepo(Repository, EventSourcedRepository):

    domain_class = MarketCalibration
