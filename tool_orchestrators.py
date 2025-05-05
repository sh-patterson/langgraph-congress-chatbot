# tool_orchestrators.py

import asyncio
import logging
from typing import List, Literal, Optional, Any # Added Any for isinstance check
from pydantic import ValidationError

# Import the detailed vote fetching functions and models
from votes_api.house import get_house_vote_details
from votes_api.senate import get_senate_vote_details
from models import MemberVoteRecord, VotePosition, RollCallVote # Import necessary models

logger = logging.getLogger(__name__)

async def list_member_vote_details(
    bioguide_id: str,
    chamber: Literal['House', 'Senate'],
    congress: int,
    roll_numbers: List[int],
    session: Optional[int] = None,
    year: Optional[int] = None,
) -> List[MemberVoteRecord]:
    """
    Fetches vote details for multiple specified roll calls for a given chamber
    and filters the results to show only how a specific member (identified by bioguide_id)
    voted on each.

    Args:
        bioguide_id: The BioGuide ID of the member (e.g., 'G000570').
        chamber: The chamber ('House' or 'Senate').
        congress: The Congress number (e.g., 117). Always required for context.
        roll_numbers: A LIST of INTEGERS representing the specific roll call
                      numbers to fetch (e.g., [420, 421, 500]).
        session: The session number (e.g., 1 or 2). Required only if chamber is 'Senate'.
        year: The calendar year (e.g., 2022). Required only if chamber is 'House'.

    Returns:
        A list of MemberVoteRecord objects, one for each specified roll call where
        the member's vote position was found.

    Raises:
        ValueError: If required parameters are missing or if 'roll_numbers'
                    is not a list of integers.
    """
    logger.info(f"Executing list_member_vote_details: Member={bioguide_id}, Chamber={chamber}, Congress={congress}, Session={session}, Year={year}, Rolls={len(roll_numbers) if isinstance(roll_numbers, list) else 'INVALID'}")

    # --- Input Validation ---
    if not bioguide_id:
        raise ValueError("bioguide_id must be provided for list_member_vote_details.")
    if not isinstance(roll_numbers, list):
        # Provide a clear error message if the LLM passed the wrong type
        raise ValueError(f"Invalid argument type: 'roll_numbers' must be a List of integers (e.g., [420, 500]), but received type {type(roll_numbers)} with value: {roll_numbers}")
    if not roll_numbers: # Check for empty list after type check
         logger.warning("list_member_vote_details called with empty roll_numbers list.")
         return []
    # Check if all items in the list are integers
    if not all(isinstance(item, int) for item in roll_numbers):
        raise ValueError(f"Invalid argument content: 'roll_numbers' list must contain only integers. Received: {roll_numbers}")

    # Validate chamber-specific requirements
    if chamber == "House" and year is None:
        raise ValueError("Parameter 'year' is required for House votes in list_member_vote_details.")
    if chamber == "Senate" and session is None:
         raise ValueError("Parameter 'session' is required for Senate votes in list_member_vote_details.")
    # --- End Input Validation ---


    tasks = []
    for roll in roll_numbers:
        if chamber == "House":
            tasks.append(get_house_vote_details(year, roll)) # type: ignore
        else: # Senate
            tasks.append(get_senate_vote_details(congress, session, roll)) # type: ignore

    logger.debug(f"Gathering {len(tasks)} vote detail tasks...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    logger.debug("Finished gathering vote detail tasks.")


    member_records: List[MemberVoteRecord] = []
    for i, vote_result_or_exc in enumerate(results):
        roll = roll_numbers[i]
        # current_session = session if chamber == "Senate" else None # Not needed here

        if isinstance(vote_result_or_exc, Exception):
            # Log error but continue to process other votes
            logger.error(f"Failed to fetch/parse vote {chamber} roll {roll} for member {bioguide_id}: {vote_result_or_exc}", exc_info=False)
            continue

        # Type hint for clarity after exception check
        vote_result: RollCallVote = vote_result_or_exc

        # Find the specific member's position
        found_position: Optional[VotePosition] = None
        for pos in vote_result.positions:
            # Check if bioguide_id exists on the position before comparing
            if pos.bioguide_id and pos.bioguide_id == bioguide_id:
                found_position = pos
                break

        # *** Name Fallback Logic - Deferred for now ***
        # if not found_position and user_provided_name: # Need to pass user_provided_name
        #    logger.debug(f"Bioguide ID {bioguide_id} not found for roll {roll}, attempting fallback search by name '{user_provided_name}'")
        #    for pos in vote_result.positions:
        #       if pos.name and user_provided_name.lower() in pos.name.lower():
        #           logger.info(f"Found member by name fallback: {pos.name}")
        #           found_position = pos
        #           break # Take the first name match? Or collect all?

        if found_position:
            # Create the specific record
            try:
                record = MemberVoteRecord(
                    chamber=vote_result.chamber,
                    congress=vote_result.congress,
                    session=vote_result.session,
                    roll_number=vote_result.roll_number,
                    vote_date=vote_result.vote_date,
                    vote_question=vote_result.vote_question,
                    bill_number=vote_result.bill_number,
                    member_position=found_position
                )
                member_records.append(record)
            except ValidationError as e:
                 logger.warning(f"Skipping MemberVoteRecord creation for roll {roll} due to validation error: {e}")
        else:
             # Only log info if we didn't find them by ID (and fallback isn't implemented)
             logger.info(f"Member {bioguide_id} position not found in vote {chamber} roll {roll}")


    logger.info(f"Found {len(member_records)} vote records for member {bioguide_id} out of {len(roll_numbers)} requested.")
    return member_records