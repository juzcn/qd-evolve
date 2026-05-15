"""Dynamic skill loader tool — loads full SKILL.md content on demand."""


from qd_evolve.tools import get_registry

# Injected by cli.py at startup
_skill_registry = None


def set_skill_registry(registry) -> None:
    global _skill_registry
    _skill_registry = registry


def _load_skill_detail(name: str) -> str:
    if _skill_registry is None:
        return "Error: skill registry not initialized"
    skill = _skill_registry.get_skill(name)
    if skill is None:
        return f"Error: skill '{name}' not found. Available: {', '.join(s.name for s in _skill_registry.get_all_skills())}"
    return skill.content


registry = get_registry()
registry.register(
    name="load_skill_detail",
    description="Load the full instructions for a skill by name. Returns the complete SKILL.md content.",
    handler=_load_skill_detail,
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill name (as shown in the skills list)",
            },
        },
        "required": ["name"],
    },
)