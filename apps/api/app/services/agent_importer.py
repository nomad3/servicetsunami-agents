import json


def detect_format(content) -> str:
    if not isinstance(content, dict):
        return "unknown"
    if "agents" in content and isinstance(content.get("agents"), list):
        agents = content["agents"]
        if agents and isinstance(agents[0], dict) and "role" in agents[0]:
            return "crewai"
    if "role" in content and "goal" in content:
        return "crewai"
    if "agent_type" in content:
        return "langchain"
    if "_type" in content and "agent" in str(content["_type"]).lower():
        return "langchain"
    if "name" in content and "system_message" in content:
        return "autogen"
    return "unknown"


def import_crewai(data: dict) -> dict:
    if "agents" in data and isinstance(data.get("agents"), list) and data["agents"]:
        source = data["agents"][0]
    else:
        source = data

    tools = source.get("tools", [])
    if tools and isinstance(tools[0], dict):
        tools = [t.get("name", str(t)) for t in tools]

    return {
        "name": source.get("role", "CrewAI Agent"),
        "description": source.get("goal", ""),
        "persona_prompt": source.get("backstory", ""),
        "capabilities": tools,
        "config": {"metadata": {"source": "crewai", "original": data}},
    }


def import_langchain(data: dict) -> dict:
    agent_type = data.get("agent_type") or data.get("_type", "")
    tools = data.get("tools", [])
    cap = [t.get("name", str(t)) if isinstance(t, dict) else str(t) for t in tools]

    return {
        "name": data.get("name", "LangChain Agent"),
        "description": data.get("description", f"Imported LangChain {agent_type} agent"),
        "persona_prompt": data.get("prefix", ""),
        "capabilities": cap,
        "config": {"metadata": {"source": "langchain", "agent_type": agent_type}},
    }


def import_autogen(data: dict) -> dict:
    name = data.get("name", "AutoGen Agent")
    system_message = data.get("system_message", "")
    code_execution_config = data.get("code_execution_config")
    function_map = data.get("function_map") or {}

    return {
        "name": name,
        "description": f"AutoGen agent: {name}",
        "persona_prompt": system_message,
        "capabilities": list(function_map.keys()),
        "config": {"metadata": {"source": "autogen", "code_execution": bool(code_execution_config)}},
    }


def parse_agent_definition(content: str, filename: str = "") -> dict:
    parsed = None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    if parsed is None:
        try:
            import yaml
            try:
                parsed = yaml.safe_load(content)
            except Exception:
                pass
        except ImportError:
            pass

    if not isinstance(parsed, dict):
        return {
            "name": filename or "Imported Agent",
            "description": "Imported agent (unknown format)",
            "capabilities": [],
            "config": {"metadata": {"source": "unknown", "raw": str(content)[:500]}},
        }

    fmt = detect_format(parsed)

    if fmt == "crewai":
        return import_crewai(parsed)
    if fmt == "langchain":
        return import_langchain(parsed)
    if fmt == "autogen":
        return import_autogen(parsed)

    return {
        "name": filename or "Imported Agent",
        "description": "Imported agent (unknown format)",
        "capabilities": [],
        "config": {"metadata": {"source": "unknown", "raw": str(content)[:500]}},
    }
