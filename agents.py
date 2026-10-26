"""
Convenience helpers for agent lookups.

The actual data (UUIDs, roles, newly released agents) comes from the
`valorant_content` module, which keeps it in sync with valorant-api.com and
caches it locally. This module is a thin, stable wrapper around it.
"""

from typing import List, Optional

from valorant_content import content

# {name: uuid}, always aligned with the content provider.
AGENTS = content.agents
AGENTS_BY_UUID = {v: k for k, v in AGENTS.items()}


def get_agent_uuid(agent_name: str) -> Optional[str]:
    """Return the UUID of an agent given its name."""
    return content.get_agent_uuid(agent_name)


def get_agent_name(uuid: str) -> Optional[str]:
    """Return the name of an agent given its UUID."""
    name = content.get_agent_name(uuid)
    return name if name and name != "Unknown" else None


def get_agent_role(agent_name: str) -> str:
    """Return the role of an agent (Duelist, Sentinel, ...)."""
    return content.get_agent_role(agent_name)


def list_agents() -> List[str]:
    """Return the list of all available agents."""
    return content.list_agents()


def search_agent(query: str) -> List[str]:
    """Search agents by name (case-insensitive)."""
    query_lower = query.lower()
    return [agent for agent in AGENTS.keys() if query_lower in agent.lower()]
