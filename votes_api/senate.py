# votes_api/senate.py

import datetime as dt
import logging
from typing import List, Optional, Literal, Dict
from collections import defaultdict
from lxml import etree
from pydantic import ValidationError

# Import necessary models including Party and VoteCast
from models import RollCallVote, VotePosition, VoteCast, Party, Chamber
from utils import fetch_and_parse_xml # Async helper

logger = logging.getLogger(__name__)

# --- FIX: Change default value to 0 for tally counts ---
def _safe_int(s: Optional[str], default: Optional[int] = 0) -> Optional[int]:
    """
    Safely converts a string to an integer. Returns default (0) on error or None input.
    """
    if s is None:
        return default
    try:
        # Strip whitespace before attempting conversion
        return int(s.strip())
    except (ValueError, TypeError):
        # Log a warning, but return the default value (0)
        logger.warning(f"Could not parse int from '{s}', using default {default}.")
        return default
# --- END FIX ---


async def get_senate_vote_details(congress: int, session: int, number: int) -> RollCallVote:
    """
    Fetches and parses a specific Senate roll call vote, including all member positions
    and calculating the vote breakdown by party. Uses direct element finding based on
    Senate XML structure (typically no default namespace).
    """
    logger.info(f"Executing get_senate_vote_details: Congress={congress}, Session={session}, Vote={number}")
    # Use 05d format string to match the XML filename structure (e.g., vote_119_1_00230.xml)
    url = (f"https://www.senate.gov/legislative/LIS/roll_call_votes/"
           f"vote{congress}{session}/vote_{congress}_{session}_{number:05d}.xml")
    logger.info(f"Fetching XML from: {url}")

    try:
        xml_root = await fetch_and_parse_xml(url)
        if xml_root is None:
            logger.error(f"fetch_and_parse_xml returned None for {url}")
            raise ValueError("fetch_and_parse_xml returned None")
    except Exception as e:
         # Handle exceptions from fetch_and_parse_xml (HTTP errors, XML syntax errors, etc.)
         logger.error(f"Failed to fetch or parse Senate vote {congress}-{session}-{number} from {url}: {e}", exc_info=True)
         raise ValueError(f"Could not retrieve or parse Senate vote {congress}-{session}-{number} from {url}") from e

    # --- DEBUGGING AID: Log the raw XML ---
    if logger.isEnabledFor(logging.DEBUG):
        try:
            logger.debug("--- RAW XML RECEIVED ---")
            # Decode bytes content to string for logging
            logger.debug(etree.tostring(xml_root, encoding='unicode', pretty_print=True).decode('utf-8'))
            logger.debug("--- END RAW XML ---")
        except Exception as log_e:
            logger.error(f"Error during XML debug logging: {log_e}")
    # --- END DEBUGGING AID ---

    # Extract core metadata using findtext (handles missing elements gracefully)
    # Use _safe_int with default=None for these, as they might genuinely be missing or string "None"
    congress_val = _safe_int(xml_root.findtext("congress"), default=None)
    session_val = _safe_int(xml_root.findtext("session"), default=None)
    vote_num = _safe_int(xml_root.findtext("vote_number"), default=None)


    # --- Date Parsing Fix ---
    # The date format in the sample XML is "Month Day, Year, HH:MM AM/PM"
    vote_date_str = xml_root.findtext("vote_date")
    vote_date = None
    if vote_date_str:
        try:
            # Parse the full string and extract the date part
            # Format codes: %B (Full month name), %d (Day of month), %Y (Year),
            # %I (Hour 12-hour clock), %M (Minute), %p (AM/PM)
            dt_object = dt.datetime.strptime(vote_date_str.strip(), "%B %d, %Y, %I:%M %p")
            vote_date = dt_object.date() # Extract only the date part
        except ValueError:
            logger.warning(f"Could not parse Senate vote date from string: '{vote_date_str}' for {url}")
            # vote_date remains None

    # --- Question & Result Element Name Fix ---
    # Use the specific element names found in the sample XML
    vote_question = xml_root.findtext("vote_question_text") # Prefer full text if available
    if not vote_question: # Fallback to the shorter <question> if _text is not found
        vote_question = xml_root.findtext("question")
    if vote_question: # Ensure stripped if found
         vote_question = vote_question.strip()


    vote_result = xml_root.findtext("vote_result_text") # Prefer full text
    if not vote_result: # Fallback
        vote_result = xml_root.findtext("vote_result")
    if vote_result: # Ensure stripped if found
         vote_result = vote_result.strip()


    # --- Tally Extraction ---
    # Use _safe_int with default=0 here, as tally counts should be integers >= 0
    tally: Dict[str, int] = {} # Pydantic model expects int, not Optional[int]
    counts = xml_root.find("count") # Direct find works as there's no namespace
    if counts is not None:
        # Use _safe_int for each count element, returning 0 by default
        tally['Yea'] = _safe_int(counts.findtext("yeas"), default=0) # Ensure default is 0
        tally['Nay'] = _safe_int(counts.findtext("nays"), default=0) # Ensure default is 0
        tally['Present'] = _safe_int(counts.findtext("present"), default=0) # Ensure default is 0
        tally['Not Voting'] = _safe_int(counts.findtext("absent"), default=0) # Ensure default is 0
    else:
        logger.warning(f"Could not find <count> element for Senate vote {congress}-{session}-{number} in {url}. Tally will be empty/zero.")
        # If counts is None, tally will be {} initially. Populate with 0s to match model expectations.
        tally = {'Yea': 0, 'Nay': 0, 'Present': 0, 'Not Voting': 0}


    # --- Member Position Extraction & Party Tally ---
    positions: List[VotePosition] = []
    # Use a simple dict of dicts for tally aggregation before Pydantic model
    party_tally_agg: Dict[Party, Dict[VoteCast, int]] = defaultdict(lambda: defaultdict(int))

    members = xml_root.find("members") # Direct find works
    if members is not None:
        logger.debug(f"Found <members>, processing positions...")
        for member in members.findall("member"): # findall works for direct children
            # --- Member ID Fix ---
            # Sample XML uses <lis_member_id>, not <bioguide_id>
            # Note: The VotePosition model expects 'bioguide_id'.
            # We will populate it with the LIS ID for now, as a direct mapping isn't available here.
            lis_member_id = member.findtext("lis_member_id")
            # bioguide_id = member.findtext("bioguide_id") # This element doesn't exist in the sample

            # Extract other member details
            # Use findtext for child elements
            name_full = member.findtext("member_full") # e.g., "Alsobrooks (D-MD)"
            last_name = member.findtext("last_name")
            first_name = member.findtext("first_name")
            party_raw = member.findtext("party")
            state_raw = member.findtext("state")
            vote_cast_raw = member.findtext("vote_cast") or "Not Voting"

            # Try to construct name if member_full is missing, although it's usually present
            name = name_full if name_full else f"{first_name} {last_name}".strip()
            name = name if name else None # Ensure None if still empty

            # --- Party Mapping ---
            party: Optional[Party] = None
            # Convert party_raw to expected Party literal if possible
            if party_raw == 'D':
                party = 'D'
            elif party_raw == 'R':
                party = 'R'
            elif party_raw in ['I', 'ID']: # Include 'ID' for consistency
                 party = 'ID'
            # Keep party as None if it doesn't match expected literals

            # --- Vote Cast Mapping (Refined) ---
            vote_cast: VoteCast = "Not Voting"
            # Map raw vote string to VoteCast literal
            if vote_cast_raw in ["Yea", "Guilty"]:
                vote_cast = "Yea"
            elif vote_cast_raw in ["Nay", "Not Guilty"]:
                vote_cast = "Nay"
            elif vote_cast_raw == "Present":
                vote_cast = "Present"
            # Anything else (like "Not Voting") maps to "Not Voting"

            # Log missing ID if necessary
            if not lis_member_id and name and "VACANT" not in name.upper():
                logger.debug(f"Missing LIS member ID for Senator '{name}' in Senate vote {congress}-{session}-{number} ({url})")

            try:
                pos = VotePosition(
                    bioguide_id=lis_member_id, # Populating bioguide_id with LIS ID for now
                    name=name,
                    party=party,
                    state=state_raw,
                    vote_cast=vote_cast,
                )
                positions.append(pos)
                # Aggregate party tally using the validated Party and VoteCast
                if pos.party and pos.vote_cast:
                    party_tally_agg[pos.party][pos.vote_cast] += 1
            except ValidationError as e:
                # Log details of the item that failed validation
                logger.warning(f"Skipping invalid VotePosition (Senate) for {url}: {e} - Data: lis_id={lis_member_id}, name={name}, party='{party_raw}', state='{state_raw}', vote='{vote_cast_raw}'")

        logger.debug(f"Finished processing {len(positions)} member positions.")
    else:
        logger.warning(f"Could not find <members> element for Senate vote {congress}-{session}-{number} in {url}. No member positions parsed.")


    # Convert defaultdict to standard dict for the Pydantic model
    final_party_tally = {p: dict(counts) for p, counts in party_tally_agg.items()}

    # --- Bill Number Extraction Fix ---
    bill_number_parsed = None
    doc_elem = xml_root.find("document") # Direct find works
    if doc_elem is not None:
        # Use findtext for the child elements within <document>
        doc_type = doc_elem.findtext("document_type")
        doc_num = doc_elem.findtext("document_number")
        doc_congress = doc_elem.findtext("document_congress")

        # Construct the bill number string based on the sample format
        if doc_type and doc_num:
            # Example: "PN 20 (Congress 119)" or "H.J.Res. 88 (119th Congress)"
            # Let's format it like "TYPE NUM (Congress CONG)" or similar for clarity
            bill_number_parsed = f"{doc_type.strip()} {doc_num.strip()}"
            if doc_congress and doc_congress.strip():
                 bill_number_parsed += f" (Congress {doc_congress.strip()})"
            # If the type is Resolution, use a more formal abbreviation like H.J.Res.
            # This would require a mapping, let's keep it simple for now.
            # The <legis-num> element in House XML gives HR XXX, H.J.Res. XXX etc.
            # For Senate, let's just use the Type and Number if document_name isn't suitable.
            # The sample XML has <document_name>PN20</document_name> which is concise.
            # Let's prefer document_name if available and not empty, otherwise use Type + Num
            doc_name = doc_elem.findtext("document_name")
            if doc_name and doc_name.strip():
                 bill_number_parsed = doc_name.strip()
                 if doc_congress and doc_congress.strip():
                      bill_number_parsed += f" (Congress {doc_congress.strip()})"
            elif doc_type and doc_num:
                # Fallback to Type Num format if name is empty
                 bill_number_parsed = f"{doc_type.strip()} {doc_num.strip()}"
                 if doc_congress and doc_congress.strip():
                      bill_number_parsed += f" (Congress {doc_congress.strip()})"
            else:
                bill_number_parsed = None # No meaningful number found

    # Create the final RollCallVote model
    try:
        model = RollCallVote(
            chamber="Senate",
            congress=congress_val,
            session=session_val,
            roll_number=vote_num,
            vote_date=vote_date,
            vote_question=vote_question,
            vote_result=vote_result,
            bill_number=bill_number_parsed, # Use the parsed bill number
            tally=tally, # tally should now have integer values (0 or more)
            positions=positions,
            party_tally=final_party_tally # Pass the calculated party tally
        )
        logger.info(f"Successfully parsed Senate vote details for {congress}-{session}-{number} ({url}) with {len(positions)} positions.")
        return model
    except ValidationError as e:
        logger.error(f"Failed to validate RollCallVote model for Senate vote {congress}-{session}-{number} ({url}): {e}", exc_info=True)
        # Re-raise with a more informative message
        raise ValueError(f"Failed to validate parsed vote data for Senate vote {congress}-{session}-{number}: {e}") from e
    except Exception as e:
        # Catch any other errors during model creation or final processing
        logger.error(f"An unexpected error occurred during processing Senate vote {congress}-{session}-{number} ({url}): {e}", exc_info=True)
        # Re-raise as a runtime error
        raise RuntimeError(f"An unexpected error occurred processing Senate vote {congress}-{session}-{number}") from e