
# --- Member Lookup Tool Function ---
from typing import Dict, Any, Optional, List
from models import MemberInfo, MemberSearchResults
import httpx

async def find_member(
    name: str,
    congress: Optional[int] = None,
    chamber: Optional[str] = None, # Use Chamber Literal from models.py
    state: Optional[str] = None
) -> MemberSearchResults:
    """
    Searches for Members of Congress by name using the Congress.gov API. Can be
    optionally filtered by Congress number, chamber ('House' or 'Senate'), and
    state (2-letter abbreviation). Returns a list of matching members including
    their BioGuide ID, name, party, and state. Useful for finding a
    member's Bioguide ID when only their name is known.
    """
    logger.info(f"Executing find_member: name='{name}', congress={congress}, chamber={chamber}, state={state}")
    # API documentation suggests 'q' for general query
    params: Dict[str, Any] = {"q": name, "limit": 20} # Limit results to avoid excessive data
    if congress: params["congress"] = congress
    # API seems to accept mixed case, but lower/upper are safer conventions
    if chamber:  params["chamber"]  = chamber.lower()
    if state:    params["state"]    = state.upper()

    try:
        # Call the  endpoint using the existing helper
        raw_data = await _get_async("/member", params=params)
        # Expect results under the "members" key based on API docs
        member_list_data = _unwrap_payload(raw_data, "members")

        validated_members = []
        if isinstance(member_list_data, list):
            for item in member_list_data:
                try:
                    # Validate each item against the MemberInfo model
                    validated_members.append(MemberInfo.model_validate(item))
                except ValidationError as e_item:
                     logger.warning(f"Skipping invalid member item for search '{name}': {e_item} - Item: {item}")
        else:
             logger.warning(f"Expected list for members, got {type(member_list_data)} for search '{name}'.")

        # Create the final search results object
        model = MemberSearchResults(
            members=validated_members,
            pagination=raw_data.get('pagination'),
            request=raw_data.get('request')
        )
        logger.info(f"Successfully parsed {len(model.members)} members for search '{name}'")
        return model

    except (ValidationError, TypeError, ValueError, httpx.HTTPError) as e:
        logger.error(f"Error fetching/parsing find_member for '{name}': {e}", exc_info=True)
        # Re-raise to signal tool failure to the agent
        raise ValueError(f"Failed to find or parse members for name '{name}'") from e
# congress_api.py (Complete Implementation)


import os
import httpx
import asyncio
import math # Keep math import for search_bills
from dotenv import load_dotenv
from pydantic import ValidationError
import logging

# Import models and utility functions/decorators
from models import (
    BillInfo, BillSummarySet, BillActionSet, CosponsorSet, TextVersionSet,
    BillSearchResults, BillSearchResultItem, Party, Chamber, # Make sure all needed models are here
    BillSummary, BillAction, Cosponsor, TextVersion # Import individual item models if needed for validation loops
)
from utils import handle_api_errors, congress_api_limiter, _unwrap_payload

logger = logging.getLogger(__name__)
load_dotenv()

BASE_URL = "https://api.congress.gov/v3"
API_KEY = os.getenv("CONGRESS_API_KEY")

if not API_KEY:
    raise ValueError("CONGRESS_API_KEY not found in .env file.")

# Internal async GET function using the specific Congress API limiter
@handle_api_errors(limiter=congress_api_limiter)
async def _get_async(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Internal helper for async GET requests to Congress.gov API."""
    if params is None:
        params = {}
    params.update({"api_key": API_KEY, "format": "json"})

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=20.0) as client: # Increased timeout slightly
        log_params = {k: v for k, v in params.items() if k != 'api_key'}
        logger.info(f"Requesting Congress API: {path} with params: {log_params}")
        response = await client.get(path, params=params)
        response.raise_for_status() # Error handling delegated to decorator
        return response.json()

# --- Tool Functions (Async, Typed, Validated) ---

async def get_bill_info(congress: int, bill_type: str, number: int) -> BillInfo:
    """Fetches core bill metadata (like title, sponsor, status) for a specific bill using its Congress number, type (e.g., 'hr', 's'), and number."""
    logger.debug(f"Executing get_bill_info for {congress}-{bill_type}-{number}")
    path = f"/bill/{congress}/{bill_type.lower()}/{number}"
    try:
        raw_data = await _get_async(path)
        unwrapped_data = _unwrap_payload(raw_data, 'bill')
        if unwrapped_data and isinstance(unwrapped_data, dict):
            model = BillInfo.model_validate(unwrapped_data)
            logger.info(f"Successfully parsed BillInfo for {congress}-{bill_type}-{number}")
            return model
        logger.error(f"Could not find 'bill' structure in response for {path}. Data received: {raw_data}")
        raise ValueError("Invalid response structure for bill info")
    except (ValidationError, TypeError, ValueError, httpx.HTTPError) as e: # Catch potential errors
        logger.error(f"Error fetching/parsing get_bill_info: {e}", exc_info=True)
        # Re-raise a more specific error or return None/empty model? Re-raising for now.
        raise ValueError(f"Failed to get or parse bill info for {congress}-{bill_type}-{number}") from e

async def get_bill_summaries(congress: int, bill_type: str, number: int) -> BillSummarySet:
    """Fetches official bill summaries for a specific bill."""
    logger.debug(f"Executing get_bill_summaries for {congress}-{bill_type}-{number}")
    path = f"/bill/{congress}/{bill_type.lower()}/{number}/summaries"
    try:
        raw_data = await _get_async(path)
        summary_list = _unwrap_payload(raw_data, 'summaries')
        if isinstance(summary_list, list):
            # Validate each item individually for better error reporting if needed
            validated_summaries = []
            for item in summary_list:
                try:
                    validated_summaries.append(BillSummary.model_validate(item))
                except ValidationError as e_item:
                    logger.warning(f"Skipping invalid summary item for {congress}-{bill_type}-{number}: {e_item} - Item: {item}")
            model = BillSummarySet(summaries=validated_summaries)
            logger.info(f"Successfully parsed {len(model.summaries)} summaries for {congress}-{bill_type}-{number}")
            return model
        logger.warning(f"Expected list for summaries, got {type(summary_list)} for {path}. Returning empty set.")
        return BillSummarySet(summaries=[])
    except (ValidationError, TypeError, httpx.HTTPError) as e:
        logger.error(f"Error fetching/parsing get_bill_summaries: {e}", exc_info=True)
        raise ValueError(f"Failed to get or parse summaries for {congress}-{bill_type}-{number}") from e

async def get_bill_actions(congress: int, bill_type: str, number: int) -> BillActionSet:
    """Fetches the chronological action history for a specific bill."""
    logger.debug(f"Executing get_bill_actions for {congress}-{bill_type}-{number}")
    path = f"/bill/{congress}/{bill_type.lower()}/{number}/actions"
    # Note: Actions can be paginated by the API (default 50). Fetch all?
    # For simplicity, fetch default limit first. Add pagination if needed.
    try:
        raw_data = await _get_async(path) # Add params={'limit': 250} ?
        action_list = _unwrap_payload(raw_data, 'actions')
        if isinstance(action_list, list):
             validated_actions = []
             for item in action_list:
                 try:
                     validated_actions.append(BillAction.model_validate(item))
                 except ValidationError as e_item:
                     logger.warning(f"Skipping invalid action item for {congress}-{bill_type}-{number}: {e_item} - Item: {item}")
             model = BillActionSet(actions=validated_actions)
             logger.info(f"Successfully parsed {len(model.actions)} actions for {congress}-{bill_type}-{number}")
             return model
        logger.warning(f"Expected list for actions, got {type(action_list)} for {path}. Returning empty set.")
        return BillActionSet(actions=[])
    except (ValidationError, TypeError, httpx.HTTPError) as e:
        logger.error(f"Error fetching/parsing get_bill_actions: {e}", exc_info=True)
        raise ValueError(f"Failed to parse actions response for {congress}-{bill_type}-{number}") from e

async def get_bill_cosponsors(congress: int, bill_type: str, number: int) -> CosponsorSet:
    """Fetches the list of cosponsors for a specific bill."""
    logger.debug(f"Executing get_bill_cosponsors for {congress}-{bill_type}-{number}")
    path = f"/bill/{congress}/{bill_type.lower()}/{number}/cosponsors"
    # Note: Pagination IS likely needed here. API defaults to 20, max 250.
    # Implementing full pagination for this tool.
    collected_cosponsors: List[Cosponsor] = []
    offset = 0
    api_limit = 250 # Fetch max per page

    try:
        while True: # Loop until no more pages
            params = {'limit': api_limit, 'offset': offset}
            logger.debug(f"Fetching cosponsors page: offset={offset}, limit={api_limit}")
            raw_data = await _get_async(path, params=params)
            page_cosponsor_data = _unwrap_payload(raw_data, 'cosponsors')
            pagination_info = raw_data.get('pagination')

            if not isinstance(page_cosponsor_data, list):
                logger.warning(f"Expected list for cosponsors (offset {offset}), got {type(page_cosponsor_data)}. Stopping.")
                break

            if not page_cosponsor_data:
                logger.debug(f"No more cosponsors found at offset {offset}.")
                break

            # Validate and add cosponsors from this page
            for item in page_cosponsor_data:
                try:
                    collected_cosponsors.append(Cosponsor.model_validate(item))
                except ValidationError as e_item:
                    logger.warning(f"Skipping invalid cosponsor item for {congress}-{bill_type}-{number} (offset {offset}): {e_item} - Item: {item}")

            # Check pagination info for next page
            if pagination_info and pagination_info.get('next'):
                 offset += api_limit # Prepare for next page
            else:
                 logger.debug("No 'next' link in pagination, assuming last page.")
                 break # Exit loop if no next page indicated

        model = CosponsorSet(cosponsors=collected_cosponsors)
        logger.info(f"Successfully parsed {len(model.cosponsors)} total cosponsors for {congress}-{bill_type}-{number}")
        return model

    except (ValidationError, TypeError, ValueError, httpx.HTTPError) as e:
        logger.error(f"Error fetching/parsing get_bill_cosponsors: {e}", exc_info=True)
        raise ValueError(f"Failed to parse cosponsors response for {congress}-{bill_type}-{number}") from e

async def get_bill_text_versions(congress: int, bill_type: str, number: int) -> TextVersionSet:
    """Fetches links to available bill text versions (PDF/HTML)."""
    logger.debug(f"Executing get_bill_text_versions for {congress}-{bill_type}-{number}")
    path = f"/bill/{congress}/{bill_type.lower()}/{number}/text"
    try:
        raw_data = await _get_async(path)
        # API response structure is often {"textVersions": [...]}
        text_list = _unwrap_payload(raw_data, 'textVersions') # Use correct key
        if isinstance(text_list, list):
             validated_versions = []
             for item in text_list:
                 try:
                     validated_versions.append(TextVersion.model_validate(item))
                 except ValidationError as e_item:
                      logger.warning(f"Skipping invalid textVersion item for {congress}-{bill_type}-{number}: {e_item} - Item: {item}")
             # Note: Pydantic model uses alias 'textVersions', so pass dict directly
             model = TextVersionSet.model_validate({"textVersions": validated_versions})
             logger.info(f"Successfully parsed {len(model.text_versions)} text versions for {congress}-{bill_type}-{number}")
             return model
        logger.warning(f"Expected list for textVersions, got {type(text_list)} for {path}. Returning empty set.")
        return TextVersionSet(textVersions=[])
    except (ValidationError, TypeError, httpx.HTTPError) as e:
        logger.error(f"Error fetching/parsing get_bill_text_versions: {e}", exc_info=True)
        raise ValueError(f"Failed to parse text versions response for {congress}-{bill_type}-{number}") from e

async def search_bills(query: str, congress: Optional[int] = None, limit: int = 20, max_results: int = 100) -> BillSearchResults:
    """
    Searches for bills based on a query string. Fetches efficiently but returns
    a **truncated list** (max 5 items by default) suitable for LLM processing,
    along with pagination info indicating the total found. Use 'limit' and 'max_results'
    for context, but tool returns a fixed small number.
    """
    # Limit the results returned *to the LLM* for deciding next steps
    RESULTS_TO_RETURN_TO_LLM = 5 # Keep this small
    # Fetch slightly more internally in case some fail validation
    INTERNAL_FETCH_COUNT = RESULTS_TO_RETURN_TO_LLM + 5
    INTERNAL_API_LIMIT = 100 # Still fetch in efficient batches from API

    logger.info(f"Executing search_bills: query='{query}', congress={congress}. Returning max {RESULTS_TO_RETURN_TO_LLM} results to LLM.")

    collected_bills: List[BillSearchResultItem] = []
    offset = 0
    last_pagination_info = None
    last_request_info = None
    pages_fetched = 0
    total_possible_results = 0 # Track total found by API

    # Fetch only enough pages to potentially get INTERNAL_FETCH_COUNT results
    # Handle potential division by zero if INTERNAL_API_LIMIT is 0 (shouldn't happen)
    max_pages_to_fetch = math.ceil(INTERNAL_FETCH_COUNT / INTERNAL_API_LIMIT) if INTERNAL_API_LIMIT > 0 else 1

    while pages_fetched < max_pages_to_fetch:
        params: Dict[str, Any] = {"query": query, "limit": INTERNAL_API_LIMIT, "offset": offset}
        if congress:
            params["congress"] = congress

        try:
            logger.debug(f"Fetching search page: offset={offset}, limit={INTERNAL_API_LIMIT}")
            raw_data = await _get_async("/bill", params=params)
            page_bills_data = _unwrap_payload(raw_data, 'bills')
            last_pagination_info = raw_data.get('pagination')
            last_request_info = raw_data.get('request')
            pages_fetched += 1

            # Try to get total count from pagination if available
            if last_pagination_info and 'count' in last_pagination_info and isinstance(last_pagination_info['count'], int):
                total_possible_results = max(total_possible_results, last_pagination_info['count'])

            if not isinstance(page_bills_data, list):
                 logger.warning(f"Expected list for bills search results (offset {offset}), got {type(page_bills_data)}. Stopping.")
                 break

            if not page_bills_data:
                 logger.debug(f"No more bills found at offset {offset}.")
                 break

            # Validate and add bills from this page, stopping once we have enough fetched internally
            validated_page_bills = []
            for item in page_bills_data:
                # Stop adding if we've already reached the desired internal count
                if len(collected_bills) >= INTERNAL_FETCH_COUNT:
                    break
                try:
                    validated_page_bills.append(BillSearchResultItem.model_validate(item))
                except ValidationError as e_item:
                     logger.warning(f"Skipping invalid item in search results (offset {offset}): {e_item} - Item: {item}")

            collected_bills.extend(validated_page_bills)
            logger.debug(f"Collected {len(validated_page_bills)} bills from page. Total fetched internally: {len(collected_bills)}.")

            # Stop fetching more pages if we have enough potential results
            if len(collected_bills) >= INTERNAL_FETCH_COUNT:
                logger.debug(f"Reached internal fetch count ({INTERNAL_FETCH_COUNT}). Stopping pagination.")
                break            # Also stop if API indicates no more pages
            if last_pagination_info and last_pagination_info.get('next') is None:
                 logger.debug("Pagination info indicates no next page.")
                 break
            if len(page_bills_data) < INTERNAL_API_LIMIT:
                logger.debug("Received fewer items than internal limit, assuming last page.")
                break

            offset += INTERNAL_API_LIMIT

        except Exception as e:
            logger.error(f"Error during bill search pagination (offset {offset}): {e}. Returning collected results.", exc_info=True)
            break

    # Truncate the collected list to the amount we want to show the LLM
    final_bills_for_llm = collected_bills[:RESULTS_TO_RETURN_TO_LLM]
    actual_returned_count = len(final_bills_for_llm)

    # Create pagination info reflecting what happened
    final_pagination = {
        "total_found": total_possible_results or len(collected_bills), # Best guess at total
        "returned_to_llm": actual_returned_count,
        "limit_applied_in_tool": RESULTS_TO_RETURN_TO_LLM
    }

    logger.info(f"Search complete. Returning {actual_returned_count} bills to LLM (Total found: ~{final_pagination['total_found']}).")

    return BillSearchResults(
         bills=final_bills_for_llm, # Return the TRUNCATED list
         pagination=final_pagination, # Provide context about truncation
         request=last_request_info
    )