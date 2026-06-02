"""Shim loadout: expose basic_open_agent_tools JSON file tools to the OAT bridge.

Add to oat.json: {"boat-json": {"package": "tools.bridge._oat_json", "loadout": "json_file_tools"}}
"""


def load_json_file_tools_loadout():
    """Return JSON file manipulation tools from basic_open_agent_tools.

    Includes read/write/update/delete operations. Risk accepted —
    these tools can corrupt config files if misused.
    """
    from basic_open_agent_tools.data import json_tools

    return [
        # Read
        json_tools.read_json_file,
        json_tools.get_json_value_at_path,
        json_tools.get_json_keys,
        json_tools.get_json_structure,
        json_tools.count_json_items,
        json_tools.search_json_keys,
        # Write (risk accepted — no pydantic validation)
        json_tools.write_json_file,
        json_tools.update_json_value_at_path,
        json_tools.delete_json_key_at_path,
        json_tools.append_to_json_array,
    ]
