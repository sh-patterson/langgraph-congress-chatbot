#!/usr/bin/env python3
import os
import sys
import signal
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage, SystemMessage
from graph import build_agent_graph, get_tools_list
from models import AgentState

# --- Logging Setup ---
logging.basicConfig(
    # Change default level to WARNING for less verbose output
    level=logging.WARNING,
    format="%(asctime)s %(name)s %(levelname)s › %(message)s",
    datefmt="%H:%M:%S",
)
# Keep specific loggers quiet unless debugging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("langchain_core").setLevel(logging.WARNING)
logging.getLogger("langgraph").setLevel(logging.WARNING)
# Ensure cli logger respects the overall level unless debug is on
logger = logging.getLogger("cli")

# --- Load .env & System Prompt ---
load_dotenv()
PROMPT_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPT_DIR / "system.md"
try:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read().strip()
    logger.info(f"Loaded system prompt from {SYSTEM_PROMPT_PATH}")
except Exception as e:
    logger.warning(f"Could not load system prompt from {SYSTEM_PROMPT_PATH}: {e}")
    SYSTEM_PROMPT = "You are CongressLens, a research assistant focused on US Congress legislation and roll-call votes." # Default prompt

# --- Typer & Console ---
app = typer.Typer()
console = Console()

# --- Session History ---
class HistoryLogger:
    def __init__(self):
        self._history: List[Dict[str,Any]] = []
        self._start = datetime.now(timezone.utc)

    def record(self, user: str, answer: str, tools: List[Dict[str,Any]]):
        self._history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input": user,
            "answer": answer,
            "tools": tools,
        })    
    def flush(self):
        if not self._history: return
        try: # Add try-except around file I/O
            base = Path.home() / ".congresslens" / "sessions" / self._start.strftime("%Y%m%d")
            base.mkdir(parents=True, exist_ok=True)
            out = base / f"{self._start.strftime('%H%M%S')}.json"
            data = {
                "start": self._start.isoformat(),
                "end": datetime.now(timezone.utc).isoformat(),
                "interactions": self._history,
            }
            with open(out, "w", encoding="utf-8") as f: # Add encoding
                json.dump(data, f, indent=2, default=str)
            # --- START MOD: Suppress history save message for non-debug ---
            # Change to debug log
            logger.debug(f"Saved session history → {out}")
            # --- END MOD ---
        except Exception as e:
            logger.error(f"Failed to save session history: {e}", exc_info=True)
            console.print(f"[red]Error saving session history: {e}[/red]")


history = HistoryLogger()

# --- Graceful exit ---
def _shutdown(signum, frame):
    # Use console.print for consistency, ensure flush happens
    console.print("\n[bold yellow]Interrupted. Flushing history and exiting...[/bold yellow]")
    history.flush()
    sys.exit(0)

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# --- Helper to pull ToolMessage results ---
def extract_tool_results(msgs: List[BaseMessage]) -> List[Dict[str,Any]]:
    tools_output = []
    for m in msgs:
        if isinstance(m, ToolMessage):
            content_repr = None # Default representation
            try:
                # Check if content is a Pydantic model with model_dump
                if hasattr(m.content, "model_dump") and callable(m.content.model_dump):
                    # Use exclude_unset=True to avoid showing default values in debug output
                    content_repr = m.content.model_dump(mode="json", exclude_unset=True)
                # Check if content is already a dict or list
                elif isinstance(m.content, (dict, list)):
                    content_repr = m.content
                # Check if content is a string that might be JSON
                elif isinstance(m.content, str):
                    try:
                        # Attempt to load JSON string content
                        content_repr = json.loads(m.content)
                    except json.JSONDecodeError:
                        # If not JSON, keep as string
                        content_repr = m.content
                # Fallback for other types
                else:
                    content_repr = str(m.content)

                tools_output.append({
                    "id": m.tool_call_id,
                    "name": m.name,
                    "result": content_repr
                })
            except Exception as e:
                logger.warning(f"Error processing ToolMessage content for history/debug (ID: {m.tool_call_id}): {e}", exc_info=False)
                tools_output.append({
                    "id": m.tool_call_id,
                    "name": m.name,
                    # Include a simple error message in the output
                    "error": f"Failed to process content for logging/debug: {e}",
                    "raw_content_type": str(type(m.content)) # Keep raw type for diagnosis
                })
    return tools_output

# --- “tools” subcommand ---
@app.command("tools", help="Show available tools and their parameter schemas.")
def list_tools():
    try:
        schemas = get_tools_list()
        console.print("[bold underline]Available Tools[/bold underline]\n")
        for tool_def in schemas:
            fn = tool_def["function"]
            console.print(f"- [cyan]{fn['name']}[/cyan]: {fn.get('description','No description')}")
            console.print(Syntax(json.dumps(fn["parameters"], indent=2), "json", theme="monokai"))
            console.print()
    except Exception as e:
        logger.exception("Failed to get tools list")
        console.print(f"[bold red]Error getting tools list:[/bold red] {e}")


# --- chat command ---
@app.command()
def chat(
    query: Optional[str] = typer.Argument(None, help="Initial question (or omit for interactive)."),
    model: str = typer.Option(os.getenv("OPENAI_MODEL", "o4-mini"), "--model", help="LLM model to use."), # Read default from env here too
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging."),
    output_json: bool = typer.Option(False, "--json", "-j", help="Dump raw JSON (single query)."),
):
    """Ask a question about US Congress bills and votes."""
    if debug:
        # Set root logger level AND cli logger level to DEBUG only when debug is True
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)
        console.print("[yellow]Debug logging enabled[/yellow]")
    # else, the level defaults to WARNING from basicConfig
    # Use console.print for user feedback, logger for internal logs
    console.print(f"[dim]Initializing agent (model={model})...[/dim]")
    try:
        # Pass the command line args directly to the builder
        agent = build_agent_graph(model_name=model)
    except Exception as e:
        logger.exception("Failed to build agent with given parameters")
        console.print(f"[bold red]Error building agent:[/bold red] {e}")
        raise typer.Exit(code=1)

    is_interactive = query is None
    if is_interactive:
        console.print(Panel(
            "[bold green]CongressLens Interactive[/bold green]\nType your question or 'exit' to quit.",
            title="CongressLens", border_style="blue"
        ))
    else:
        console.print(f"\n💬 [bold blue]Query:[/bold blue] {query}")

    messages: List[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    user_query_for_turn = query # Use a separate variable for the current turn's query

    while True:
        if user_query_for_turn is None: # Prompt if we don't have a query for this turn
            try:
                user_input_str = Prompt.ask("[bold cyan]You[/bold cyan]")
                if not user_input_str.strip(): # Handle empty input
                    continue
                if user_input_str.lower() in ("exit", "quit"):
                    break # Exit loop naturally
                user_query_for_turn = user_input_str # Use the prompted input for this turn
            except (EOFError, KeyboardInterrupt):
                # Trigger shutdown manually to ensure flush
                _shutdown(None, None) # Call shutdown handler
                return # Exit function after shutdown

        # Append the user message for this turn
        messages.append(HumanMessage(content=user_query_for_turn))        # Invoke Agent
        try:
            # --- START MOD: Add simple status indicators ---
            console.print("[dim]CongressLens is processing...[/dim]") # Indicator before ainvoke
            logger.info("Invoking agent...") # Keep internal log at INFO

            state = asyncio.run(agent.ainvoke(AgentState(messages=messages)))

            logger.info("Agent invocation complete.") # Keep internal log at INFO

            # Check if any tool calls were made in this turn's messages
            # We need to check the messages *added* by the ainvoke call.
            tools_used = extract_tool_results(state["messages"]) # Use the state's messages directly
            if tools_used:
                 console.print(f"[dim]Used {len(tools_used)} tool call(s) to fetch data.[/dim]") # Indicate tool use
            # --- END MOD ---

        except Exception as e:
            logger.exception("Agent invocation failed")
            console.print(f"[bold red]Error during processing:[/bold red] {e}")
            if debug:
                # Use Rich's traceback rendering
                console.print_exception(show_locals=True)
            # Decide whether to break or continue after an error
            # For robustness, maybe continue the loop in interactive mode?
            if not is_interactive:
                break # Exit on error in single-shot mode
            else:
                # Reset query so user can try again or type exit
                user_query_for_turn = None
                # Remove the failed HumanMessage and potentially the SystemMessage if it was the first try
                if len(messages) > 1 and isinstance(messages[-1], HumanMessage):
                    messages.pop()
                continue # Continue interactive loop

        # Update message history with the *full* result from the agent state
        messages = list(state["messages"])

        # Extract answer and tools
        answer = ""
        for m in reversed(messages):
            if isinstance(m, AIMessage) and not m.tool_calls:
                answer = m.content
                break

        tools_used = extract_tool_results(messages)
        # Record the *successful* interaction
        history.record(user_query_for_turn, answer, tools_used)        # Output
        if not is_interactive and query and output_json:
            # For single-shot JSON output, include messages and tools for completeness
            out = {"answer": answer, "tools": tools_used}
            console.print(Syntax(json.dumps(out, indent=2, default=str), "json", theme="monokai"))
            break
        else:
            # --- START MOD: Simplify Answer Header ---
            # Instead of a header, just use a simple separator
            console.print("\n" + "="*40) # Simple separator
            # --- END MOD ---
            if answer:
                 console.print(Markdown(answer))
            else:
                 # This happens if the LLM's last output was a tool call instruction,
                 # or if it responded with tool calls *instead* of a final text answer.
                 console.print("[italic](Agent processing... awaiting final response in next step or turn)[/italic]")


        # Break if not interactive (single query mode)
        if not is_interactive:
            break

        # Reset query for the next loop iteration (interactive mode)
        user_query_for_turn = None

    # Call shutdown explicitly after the loop finishes or breaks
    _shutdown(None, None)

# --- Main ---
if __name__ == "__main__":
    # Wrap the app call in a try-except for final catch-all
    try:
         app()
    except Exception as e:
         # Log fatal errors that might occur outside the command functions
         logger.critical(f"Unhandled exception at top level: {e}", exc_info=True)
         console.print(f"[bold red]A critical error occurred. Check logs.[/bold red]")
         sys.exit(1)