# CongressLens: A LangGraph Chatbot for Congressional Research

CongressLens is a command-line research assistant powered by LangGraph and Large Language Models (LLMs) designed to answer natural language questions about U.S. Congress legislation and roll-call votes. It retrieves data directly from official sources like the Congress.gov API and the official House Clerk / Senate LIS XML vote feeds.

This project is under active development, with this commit representing a stable foundation with core functionality.

## Features (v0.1.0-alpha)

*   **Interactive Chat:** Engage in multi-turn conversations or ask single questions.
*   **Natural Language Queries:** Ask questions about bills and votes in plain English.
*   **Bill Information:** Retrieve bill details, official summaries, chronological actions, cosponsor lists, and links to text versions via the Congress.gov API (`get_bill_info`, `get_bill_summaries`, `get_bill_actions`, `get_bill_cosponsors`, `get_bill_text_versions`).
*   **Bill Search:** Search for legislation using keywords and common names (`search_bills`). Includes special handling for well-known acts (e.g., "Inflation Reduction Act", "Affordable Care Act").
*   **Detailed Vote Data:** Access roll-call vote details (question, result, overall tally, **partisan breakdown**) for both House and Senate using official XML feeds (`get_house_vote_details`, `get_senate_vote_details`).
*   **Member Identification:** Find members of Congress by name with optional filters (`find_member`).
*   **Individual Member Vote Positions:** Retrieves a **specific member's vote position(s)** for one or more **specific roll call numbers** (`list_member_vote_details`). Requires the member's BioGuide ID (obtained via `find_member`) and the exact roll call number(s), year (House) or session (Senate).
*   **Robust Data Parsing:** Includes refined logic to accurately parse complex XML structures from House/Senate vote feeds and handle variations in data representation (e.g., session formats, tally counts, member IDs).
*   **Intelligent Member Name Handling:** Agent attempts to identify the correct member from `find_member` results based on context, and asks for clarification if genuine ambiguity between multiple distinct individuals exists.
*   **Configurable LLM:** Choose the OpenAI model and temperature via command-line options (`--model`, `--temp`) or environment variables (`OPENAI_MODEL`, `OPENAI_TEMP`).
*   **Official Sources:** Relies exclusively on Congress.gov API and official House/Senate XML vote feeds.
*   **Structured Output:** Provides answers in Markdown and offers a `--json` flag (for single queries) for detailed, structured output (useful for debugging or integration).
*   **Tool Introspection:** List available agent tools and their expected parameters using the `tools` command (`python cli.py tools`).
*   **Session History:** Automatically saves conversation history to `~/.congresslens/sessions/` upon graceful exit.
*   **Robust & Asynchronous:** Built with `asyncio`, rate limiting, error handling, Pydantic data validation, and graceful shutdown handling. Includes non-verbose status indicators during processing.

## Prerequisites

*   Python 3.10+
*   `pip` (Python package installer)
*   `venv` (Recommended for virtual environments)
*   **Congress.gov API Key:** Obtain from [api.congress.gov](https://api.congress.gov/sign-up/)
*   **OpenAI API Key:** Obtain from [platform.openai.com](https://platform.openai.com/api-keys)

## Setup & Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/sh-patterson/langgraph-congress-chatbot.git
    cd congress_lens
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv .venv
    # On macOS/Linux:
    source .venv/bin/activate
    # On Windows:
    # .venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    # Optional: Install development dependencies (for testing/linting - see requirements.txt comments)
    # pip install -r requirements.txt -r requirements-dev.txt # If you create a separate dev file
    ```

4.  **Configure API Keys & System Prompt:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   **Edit the `.env` file** with your text editor and paste your actual API keys obtained in the prerequisites step:
        ```dotenv
        CONGRESS_API_KEY=YOUR_CONGRESS_GOV_API_KEY_HERE
        OPENAI_API_KEY=YOUR_OPENAI_API_KEY_HERE
        # Optional overrides below
        # MAX_CONCURRENCY=5
        # CONGRESS_RATE=1.3
        # OPENAI_MODEL=o4-mini # Default model if --model not used
        # OPENAI_TEMP=0.0        # Default temp if --temp not used
        ```
    *   **(Optional) Edit `prompts/system.md`:** Modify the system prompt file to customize the agent's persona, rules, or behavior. (Note: Significant changes may require updating the LLM's reasoning instructions).

## Configuration (`.env` file)

The `.env` file stores necessary configuration:

*   `CONGRESS_API_KEY`: **Required**. Your API key for Congress.gov.
*   `OPENAI_API_KEY`: **Required**. Your API key for OpenAI models.
*   `MAX_CONCURRENCY` (Optional, Default: 5): Controls simultaneous requests to House/Senate XML feeds.
*   `CONGRESS_RATE` (Optional, Default: 1.3): Target rate limit (requests/second) for Congress.gov API.
*   `OPENAI_MODEL` (Optional, Default: `o4-mini`): Default model if `--model` option isn't specified.
*   `OPENAI_TEMP` (Optional, Default: `0.0`): Default temperature if `--temp` option isn't specified.

## Usage

There are two main ways to use CongressLens:

**1. Interactive Mode:**

Start an interactive chat session. Type `exit` or `quit` to end.

```bash
python cli.py chat
```

During interactive sessions, you will see non-verbose status indicators like `CongressLens is processing...` and `Used X tool call(s) to fetch data.`

**2. Single Query Mode:**

Provide your question directly as an argument.

```bash
python cli.py chat "Your question about Congress here"
```

**Command-Line Options (for `chat` command):**

*   `query`: Optional argument for a single query (omit for interactive).
*   `--model TEXT`: Specify the OpenAI model name to use (e.g., `o4-mini`). Overrides `.env` default.
*   `--temp FLOAT`: Set the LLM sampling temperature (e.g., `0.7`). Overrides `.env` default.
*   `--debug`: Enable debug logging for more verbose output, showing detailed tool calls, responses, and tracebacks.
*   `--json` (`-j`): (Single Query Mode Only) Output raw JSON containing the final answer and tool results. Useful for programmatic use or detailed debugging.
*   `--help`: Show help message and exit.

**List Available Tools:**

See the tools the agent can use and their expected parameters.

```bash
python cli.py tools
```

## Example Queries

```bash
# Start interactive mode
python cli.py chat

# Ask a single question about a bill by ID
python cli.py chat "What is the latest action on S. 456 in the 118th Congress?"

# Ask about a bill by common name and its summary
python cli.py chat "Tell me about the Inflation Reduction Act summary."

# Get the partisan breakdown of a specific vote
python cli.py chat "On Roll call vote 325 on the IRA, what was the partisan breakdown of that vote?"

# Ask how a specific member voted on multiple specific roll calls (requires member name/context and vote numbers/context)
python cli.py chat "How did Representative Ruben Gallego vote on House roll calls 420, 421, and 422 in 2022?"

# Ask about a member using an ambiguous name (should prompt for clarification)
python cli.py chat "Tell me about Senator Johnson."

# Ask about an entity that likely doesn't exist (should report "no info found")
python cli.py chat "Show me the summary for a bill called the 'Unicorn Act'."

# Use a different model for a single query
python cli.py chat --model gpt-4o "Show me the summaries for H.R. 815 (118th Congress)."

# Get JSON output for a single query
python cli.py chat "How did Rep Ocasio-Cortez (A000370) vote on House roll 6 2023?" --json

# List tools
python cli.py tools
```

*(Note: Replace example bill numbers, member IDs, roll numbers, congresses, sessions, and years with relevant current data for testing.)*

## Architecture Overview

CongressLens uses a **Single Agent Reason+Act (ReAct)** architecture built with **LangGraph**. An LLM (configured via `.env` or `--model`) reasons about user queries, decides on necessary steps, and orchestrates calls to specialized Python tools. These tools interact with:

1.  **Congress.gov API v3:** For bill details, summaries, actions, cosponsors, text links, bill searching, and member lookup (JSON format).
2.  **Official House Clerk EVS & Senate LIS XML Feeds:** For detailed roll-call vote data, including overall tallies, partisan breakdowns, and individual member positions.

The system is built asynchronously using `asyncio` and includes utilities for rate limiting, error handling, and Pydantic data validation. The agent's behavior is guided by a system prompt loaded from `prompts/system.md`.

### Interaction Between Files

-   `cli.py` imports and uses `build_agent_graph()` from `graph.py` to create the agent instance.
-   The interactive loop in `cli.py` manages the `messages` list (part of the `AgentState`). Each turn, `cli.py` appends the `HumanMessage` and passes the updated `messages` state to `agent.ainvoke()`.
-   `graph.py` runs the messages through its nodes (LLM -> Tools -> LLM). The `ToolNode` calls the actual functions defined in `congress_api.py`, `votes_api/*.py`, and `tool_orchestrators.py`.
-   `graph.py` returns the updated `AgentState` (containing the original messages, plus the new `AIMessage` outputs and `ToolMessage` results) back to `cli.py`.
-   `cli.py` then processes the returned state to extract the final answer, update its local `messages` list for the next turn, record history, and display output.
-   `models.py` defines the shared data structures (`AgentState`, `BillInfo`, `RollCallVote`, `MemberInfo`, etc.) used for communication between the tools, the graph, and the CLI's processing logic.
-   `utils.py` provides shared utility functions (rate limiting, error handling, XML fetching/parsing helpers) used by the API and vote parsing modules.
-   Environment variables (`.env`) are loaded by both `cli.py` and `congress_api.py` for configuration.

This structure means `cli.py` is responsible for the user interface and managing the conversation turns, while `graph.py` encapsulates the core agent logic and tool orchestration based on the message history. Modifications often involve coordinating changes between the prompt (`system.md`), the agent's logic (`graph.py`), tool definitions/parsers (`congress_api.py`, `votes_api/*.py`, `tool_orchestrators.py`), data models (`models.py`), and how the CLI presents results (`cli.py`).

## Session History

Interaction history (user input, agent's final text answer, and summaries of tools used) is automatically saved in JSON format to `~/.congresslens/sessions/<YYYYMMDD>/<HHMMSS>.json` upon exiting the application gracefully (via typing `exit`/`quit` or pressing Ctrl+C/SIGTERM).

## Testing: TO DO

## Contributing

Contributions are welcome! Please see `docs/contribute.md` for guidelines on setting up your development environment and contributing. (Note: This file does not exist yet, a placeholder.)

## License: See MIT License
