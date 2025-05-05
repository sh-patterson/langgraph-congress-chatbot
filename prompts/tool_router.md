# Tool Routing Guidance (Internal Note)

**This file is for documentation purposes. Tool routing in this LangGraph agent is primarily implicit, driven by the LLM's understanding of the tool descriptions and parameters derived from the Python function docstrings and type hints when `model.bind_tools(tools)` is called in `graph.py`.**

The LLM should select tools based on the following general principles (reinforced by instructions in the System Prompt):

1.  **Specificity:** If the user provides specific identifiers (e.g., `congress`, `bill_type`, `number` for a bill; `year`/`congress`, `session`, `roll`/`number` for a vote; `bioguide_id` for a member), prioritize using the corresponding "getter" tools:
    *   `get_bill_info`, `get_bill_summaries`, `get_bill_actions`, `get_bill_cosponsors`, `get_bill_text_versions`
    *   `get_house_vote_details`, `get_senate_vote_details`
    *   `list_member_vote_details` (requires `bioguide_id` AND specific `roll_numbers`)

2.  **Search as Fallback:**
    *   Use `search_bills` when the user asks about bills using keywords or common names where the specific identifier isn't known/provided. Follow the "Special Name Handling" procedure for common names.
    *   Use `find_member` when the user asks about a member by name and their `bioguideId` is needed for another tool.

3.  **Vote Detail Level:**
    *   Use `get_house_vote_details` or `get_senate_vote_details` when asked about a *single, specific vote's* overall result, tally (including party breakdown via `party_tally`), question, or how *all* members voted.
    *   Use `list_member_vote_details` *only* when asked how a *single, specific member* voted on one or more *specific, known roll call numbers*. The LLM must ensure it has the `bioguide_id` (potentially via `find_member` first) and the list of `roll_numbers` before calling this tool.

4.  **Clarification:** If the required arguments for a prioritized tool are missing (e.g., asking "How did my Senator vote?" without providing the Senator's ID or the specific vote number), the LLM should *not* call a tool but instead ask the user for the missing information. Do not guess arguments unless explicitly permitted (like proposing a bill number for common names, followed by verification).

5.  **Multi-Tool Use:** The LLM can and should call multiple tools sequentially if needed to answer a complex query (e.g., `find_member` -> `list_member_vote_details`, or `get_bill_actions` -> extract roll numbers -> `get_house_vote_details`). The LangGraph loop facilitates this.

**Refining Routing:** To improve routing, focus on enhancing the clarity and detail of:
    *   Python function docstrings for each tool (which become the tool `description`).
    *   Parameter names and type hints in the tool function signatures.
    *   Adding more specific instructions or priorities to the main System Prompt if needed.