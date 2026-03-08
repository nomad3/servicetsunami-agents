"""Agent definitions for ServiceTsunami ADK server."""
# Leaf agents
from .data_analyst import data_analyst
from .report_generator import report_generator
from .knowledge_manager import knowledge_manager
from .web_researcher import web_researcher
from .customer_support import customer_support
from .sales_agent import sales_agent
from .dev_agent import dev_agent
from .personal_assistant import personal_assistant
from .cardiac_analyst import cardiac_analyst
from .billing_agent import billing_agent
from .vet_report_generator import vet_report_generator
from .deal_analyst import deal_analyst
from .deal_researcher import deal_researcher
from .outreach_specialist import outreach_specialist
from .prospect_researcher import prospect_researcher
from .prospect_scorer import prospect_scorer
from .prospect_outreach import prospect_outreach

# Team supervisors
from .data_team import data_team
from .sales_team import sales_team
from .marketing_team import marketing_team
from .prospecting_team import prospecting_team
from .vet_supervisor import vet_supervisor
from .deal_team import deal_team

# Root supervisor (must be imported LAST since it imports team supervisors)
from .agent import root_agent

__all__ = [
    "root_agent",
    # Teams
    "dev_agent",
    "data_team",
    "sales_team",
    "marketing_team",
    "prospecting_team",
    "vet_supervisor",
    "deal_team",
    "deal_analyst",
    "deal_researcher",
    "outreach_specialist",
    "prospect_researcher",
    "prospect_scorer",
    "prospect_outreach",
    # Personal assistant
    "personal_assistant",
    # Leaf agents
    "data_analyst",
    "report_generator",
    "knowledge_manager",
    "web_researcher",
    "customer_support",
    "sales_agent",
    "cardiac_analyst",
    "billing_agent",
]
