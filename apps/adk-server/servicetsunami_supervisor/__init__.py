"""Agent definitions for ServiceTsunami ADK server."""
# Leaf agents
from .data_analyst import data_analyst
from .report_generator import report_generator
from .knowledge_manager import knowledge_manager
from .web_researcher import web_researcher
from .customer_support import customer_support
from .sales_agent import sales_agent
from .architect import architect
from .coder import coder
from .tester import tester
from .dev_ops import dev_ops
from .user_agent import user_agent
from .personal_assistant import personal_assistant

# Team supervisors
from .dev_team import dev_team
from .data_team import data_team
from .sales_team import sales_team
from .marketing_team import marketing_team

# Root supervisor (must be imported LAST since it imports team supervisors)
from .agent import root_agent

__all__ = [
    "root_agent",
    # Teams
    "dev_team",
    "data_team",
    "sales_team",
    "marketing_team",
    # Personal assistant
    "personal_assistant",
    # Leaf agents
    "data_analyst",
    "report_generator",
    "knowledge_manager",
    "web_researcher",
    "customer_support",
    "sales_agent",
    "architect",
    "coder",
    "tester",
    "dev_ops",
    "user_agent",
]
