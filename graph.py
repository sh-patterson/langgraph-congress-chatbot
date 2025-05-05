# graph.py (Simplified Flow - No Processor Node)

import os
import json
import logging
import asyncio
from typing import List, Sequence, TypedDict, Optional, Any
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.graph import StateGraph, END
# Use ToolNode and standard tools_condition
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages

# Import AgentState and tools
from models import AgentState
from congress_api import (
    get_bill_info, get_bill_summaries, get_bill_actions, get_bill_cosponsors,
    get_bill_text_versions, search_bills, find_member
)
from votes_api.house import get_house_vote_details
from votes_api.senate import get_senate_vote_details
from tool_orchestrators import list_member_vote_details

logger = logging.getLogger(__name__)

# ----- Tool registry -----
TOOLS = [
    get_bill_info, get_bill_summaries, get_bill_actions, get_bill_cosponsors,
    get_bill_text_versions, search_bills, get_house_vote_details,
    get_senate_vote_details, list_member_vote_details,
    find_member
]
logger.info(f"Registered {len(TOOLS)} tools.")


# --- Agent Graph Builder ---
def build_agent_graph(
    model_name: Optional[str] = None,
) -> StateGraph:
    """
    Constructs and compiles the LangGraph agent with configurable LLM parameters.
    Uses a simplified flow: LLM -> Tools -> LLM.
    """
    # Resolve model parameters
    resolved_model_name = model_name or os.getenv("OPENAI_MODEL", "o4-mini")

    logger.info(f"Initializing agent graph with LLM: model='{resolved_model_name}', Using default temperature.")    # Instantiate the LLM and bind the registered tools
    llm = ChatOpenAI(model=resolved_model_name)
    llm_with_tools = llm.bind_tools(TOOLS)    # Define the LLM node function
    async def call_model(state: AgentState) -> dict:
        """Invokes the LLM with the current message history."""
        messages = state["messages"]
        logger.debug(f"DEBUG: Before LLM call - messages type: {type(messages)}, content: {messages}")
        logger.debug(f"LLM node executing with {len(messages)} messages.")
        response = await llm_with_tools.ainvoke(messages)
        logger.debug(f"DEBUG: After LLM call - response type: {type(response)}, content: {response}")
        logger.debug(f"DEBUG: After LLM call - tool_calls: {getattr(response, 'tool_calls', None)}")
        logger.debug(f"LLM node received response. Tool calls present: {bool(getattr(response, 'tool_calls', None))}")
        # new: only return the new message; reducer will append
        return {"messages": [response]}

    # Define the Tool execution node
    # wrap ToolNode so it appends instead of replaces
    raw_tools = ToolNode(TOOLS)    
    
    async def run_tools(state: AgentState) -> dict:
        # raw = either a list of ToolMessage objects or a dict {"messages": [...]}
        raw = await raw_tools.ainvoke(state["messages"])

        # Normalize into a plain list of ToolMessage objects
        if isinstance(raw, dict) and "messages" in raw:
            new_msgs = raw["messages"]
        elif isinstance(raw, list):
            new_msgs = raw
        else:
            # unexpected shape—wrap whatever it is
            new_msgs = [raw]

        # new: just return the tool messages; reducer appends
        return {"messages": new_msgs}

    tool_node = run_tools    # Assemble the graph structure
    # Enforce append-only merge for messages
    graph_builder = StateGraph(AgentState)

    graph_builder.add_node("llm", call_model)
    graph_builder.add_node("tools", tool_node)
    # *** REMOVED process_results node ***

    graph_builder.set_entry_point("llm")    # Add conditional logic after LLM using standard condition
    graph_builder.add_conditional_edges(
        "llm",
        tools_condition, # returns END or "tools"
        {
            "tools": "tools", # If tool calls -> go to tools node
            END: END      # Otherwise -> end graph
        }
    )

    # Edge directly from tools node BACK to the LLM node
    graph_builder.add_edge("tools", "llm")

    # Compile the graph
    agent = graph_builder.compile()
    logger.info("LangGraph agent compiled successfully (simplified flow).")
    return agent


# --- Utility to get Tool Schemas ---
def get_tools_list() -> List[dict]:
    # Correct instantiation needed here
    temp_model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "o4-mini"))
    bound_model = temp_model.bind_tools(TOOLS)
    return bound_model.get_tools_json_schema()

# --- Utility to generate documentation for tool schemas ---
def generate_tool_schemas_doc(output_file: str = "docs/graph_contract.md") -> None:
    # Keep implementation
    logger.info(f"Generating tool schema documentation -> {output_file}")
    schemas = get_tools_list()
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists

    with open(output_path, 'w') as f:
        f.write("# Graph Tool Contract\n\n")
        f.write("This document outlines the schemas for the tools available within the LangGraph agent.\n\n")
        for schema in schemas:
            f.write(f"## Tool: `{schema['name']}`\n\n")
            f.write(f"**Description:** {schema.get('description', 'No description provided.')}\n\n")
            f.write("**Parameters:**\n")
            if 'parameters' in schema and schema['parameters'].get('properties'):
                f.write("```json\n")
                f.write(json.dumps(schema['parameters'], indent=2))
                f.write("\n```\n\n")
            else:
                f.write("This tool takes no parameters.\n\n")
    logger.info(f"Successfully wrote tool schemas to {output_path}")

# --- Main execution block for script actions ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    generate_tool_schemas_doc()