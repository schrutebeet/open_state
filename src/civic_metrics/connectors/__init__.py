from civic_metrics.connectors.base import Connector
from civic_metrics.connectors.bde import BdeSeriesConnector
from civic_metrics.connectors.datacomex import DataComexConnector
from civic_metrics.connectors.direct_file import DirectFileConnector
from civic_metrics.connectors.html_excel import HtmlExcelConnector
from civic_metrics.connectors.html_regex import HtmlRegexConnector
from civic_metrics.connectors.html_table import HtmlTableConnector
from civic_metrics.connectors.ine import IneTableConnector
from civic_metrics.connectors.igae import (
    IgaeQuarterlyAccountsConnector,
    IgaeStateBudgetExecutionConnector,
)
from civic_metrics.connectors.sepe import SepeRegisteredUnemploymentConnector
from civic_metrics.connectors.social_security_pensions import SocialSecurityPensionsConnector

CONNECTORS: dict[str, type[Connector]] = {
    IneTableConnector.connector_name: IneTableConnector,
    IgaeQuarterlyAccountsConnector.connector_name: IgaeQuarterlyAccountsConnector,
    IgaeStateBudgetExecutionConnector.connector_name: IgaeStateBudgetExecutionConnector,
    BdeSeriesConnector.connector_name: BdeSeriesConnector,
    DataComexConnector.connector_name: DataComexConnector,
    DirectFileConnector.connector_name: DirectFileConnector,
    HtmlExcelConnector.connector_name: HtmlExcelConnector,
    HtmlTableConnector.connector_name: HtmlTableConnector,
    HtmlRegexConnector.connector_name: HtmlRegexConnector,
    SocialSecurityPensionsConnector.connector_name: SocialSecurityPensionsConnector,
    SepeRegisteredUnemploymentConnector.connector_name: SepeRegisteredUnemploymentConnector,
}
