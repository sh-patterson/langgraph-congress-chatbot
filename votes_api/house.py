# votes_api/house.py

import datetime as dt
import logging
import re # Import the regular expression module
from typing import List, Optional, Literal, Dict
from collections import defaultdict
from lxml import etree
from pydantic import ValidationError

# Import necessary models including Party and VoteCast
from models import RollCallVote, VotePosition, VoteCast, Party, Chamber
from utils import fetch_and_parse_xml # Async helper

logger = logging.getLogger(__name__)

# The sample XML does NOT use a default namespace on the key elements like
# <vote-metadata>, <vote-data>, <recorded-vote>, etc.
# While the DTD suggests a namespace, the actual data might not use it consistently.
# For elements that are NOT namespaced in the XML, we should NOT use the namespace prefix in XPath.
# We will keep EVS_NS definition but use it only where needed (e.g., attributes if they were namespaced, or if future XML samples include it).
# For the elements shown in the sample, we'll use unprefixed XPath.
EVS_NS = {"evs": "http://xml.house.gov/schema/evs"}

def _safe_int(s: Optional[str], default: Optional[int] = None) -> Optional[int]:
    """
    Safely converts a string to an integer, stripping non-digits first.
    Returns default on error or None input.
    """
    if s is None:
        return default
    try:
        s_stripped = s.strip()
        # Use regex to keep only digits
        digits_only = re.sub(r'\D', '', s_stripped)
        if not digits_only: # Handle cases like "" or "abc" after stripping non-digits
            return default
        return int(digits_only)
    except (ValueError, TypeError):
        logger.warning(f"Could not parse int from '{s}' (digits only: '{digits_only}'), using default {default}.")
        return default


async def get_house_vote_details(year: int, roll: int) -> RollCallVote:
    """
    Fetches and parses a specific House roll call vote, adjusting XPath based
    on common House XML structure (often no default namespace on core elements),
    including all member positions and calculating the vote breakdown by party.
    """
    logger.info(f"Executing get_house_vote_details: Year={year}, Roll={roll}")
    # Use 03d format string to match the XML filename structure (e.g., roll001.xml)
    url = f"https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
    logger.info(f"Fetching XML from: {url}")

    try:
        xml_root = await fetch_and_parse_xml(url)
        if xml_root is None:
             logger.error(f"fetch_and_parse_xml returned None for {url}")
             raise ValueError("fetch_and_parse_xml returned None")
    except Exception as e:
         # Handle exceptions from fetch_and_parse_xml (HTTP errors, XML syntax errors)
         logger.error(f"Failed to fetch or parse House vote {year}-{roll} from {url}: {e}", exc_info=True)
         raise ValueError(f"Could not retrieve or parse House vote {year}-{roll} from {url}") from e

    # --- DEBUGGING AID: Log the raw XML ---
    if logger.isEnabledFor(logging.DEBUG):
        try:
            logger.debug("--- RAW XML RECEIVED ---")
            logger.debug(etree.tostring(xml_root, encoding='unicode', pretty_print=True).decode('utf-8'))
            logger.debug("--- END RAW XML ---")
        except Exception as log_e:
            logger.error(f"Error during XML debug logging: {log_e}")
    # --- END DEBUGGING AID ---


    # --- XPath Changes ---
    # Use unprefixed XPath as per sample XML structure for these core elements
    meta_list = xml_root.xpath('.//vote-metadata')
    vote_data_list = xml_root.xpath('.//vote-data')

    if not meta_list:
         logger.error(f"Missing <vote-metadata> section using XPath './/vote-metadata' in {url}.")
         raise ValueError(f"Invalid XML structure (missing vote-metadata) for House vote {year}-{roll}")
    if not vote_data_list:
         logger.error(f"Missing <vote-data> section using XPath './/vote-data' in {url}.")
         raise ValueError(f"Invalid XML structure (missing vote-data) for House vote {year}-{roll}")


    meta = meta_list[0]
    vote_data_elem = vote_data_list[0]

    # Extract core metadata - using string() to get text content safely
    # Use correct element names from sample XML and corrected XPaths
    congress = _safe_int(meta.xpath('string(./congress)'))
    # --- FIX: Corrected XPath for session and improved _safe_int handles '1st', '2nd' ---
    session = _safe_int(meta.xpath('string(./session)')) # Use unprefixed XPath
    # --- END FIX ---

    roll_num = _safe_int(meta.xpath('string(./rollcall-num)')) # Use correct element name
    vote_question = meta.xpath('string(./vote-question)').strip() # Use correct element name and strip
    vote_result = meta.xpath('string(./vote-result)').strip() # Use correct element name and strip
    bill_number = meta.xpath('string(./legis-num)').strip() # Use correct element name and strip

    # --- Date Parsing Change ---
    # Sample XML uses '1-May-2025' format. Use strptime with correct format.
    vote_date_str = meta.xpath('string(./action-date)')
    vote_date = None
    if vote_date_str:
        try:
            # Use the format string that matches 'DD-Mon-YYYY'
            vote_date = dt.datetime.strptime(vote_date_str.strip(), '%d-%b-%Y').date()
        except ValueError:
            logger.error(f"Failed to parse action-date with format '%d-%b-%Y': '{vote_date_str}' for {url}")
            # Fallback to other potential formats if necessary, or just log error
            pass # Keep vote_date as None if parsing fails

    # --- Tally Extraction Change ---
    # Totals are nested under <vote-metadata>/<vote-totals>/<totals-by-vote>
    tally: Dict[str, Optional[int]] = {} # Use Optional[int] as _safe_int can return None
    totals_elem_list = meta.xpath('.//vote-totals/totals-by-vote') # Correct XPath path relative to meta
    if totals_elem_list:
        totals = totals_elem_list[0]
        # Use correct element names and _safe_int
        tally['Yea'] = _safe_int(totals.xpath('string(./yea-total)'))
        tally['Nay'] = _safe_int(totals.xpath('string(./nay-total)'))
        tally['Present'] = _safe_int(totals.xpath('string(./present-total)')) # Handles empty <present/>
        tally['Not Voting'] = _safe_int(totals.xpath('string(./not-voting-total)'))
    else:
        logger.warning(f"Could not find <vote-totals>/<totals-by-vote> for House vote {year}-{roll} in {url}")
        # If totals not found, tally will be empty dict

    # --- Member Position Extraction & Party Tally ---
    positions: List[VotePosition] = []
    # Use a simple dict of dicts for tally aggregation before Pydantic model
    party_tally_agg: Dict[Party, Dict[VoteCast, int]] = defaultdict(lambda: defaultdict(int))

    # XPath to find all <recorded-vote> elements under <vote-data>
    recorded_vote_elements = vote_data_elem.xpath('./recorded-vote') # Use relative path './' or './/' from vote_data_elem

    if recorded_vote_elements:
        logger.debug(f"Found {len(recorded_vote_elements)} recorded-vote elements.")
        for rec in recorded_vote_elements:
            # Legislator info is directly under <recorded-vote>
            legislator_elem_list = rec.xpath('./legislator') # Relative path
            bioguide_id = None
            name = None
            party_raw = None
            state_raw = None

            if legislator_elem_list:
                legislator_elem = legislator_elem_list[0]
                # Attributes use @
                bioguide_id = legislator_elem.xpath('string(@name-id)')
                # Get text content of the legislator element (e.g., "Adams")
                name = legislator_elem.xpath('string()').strip()
                party_raw = legislator_elem.xpath('string(@party)')
                state_raw = legislator_elem.xpath('string(@state)')
            else:
                 logger.warning(f"Missing <legislator> element in a <recorded-vote> for {url}")
                 # Skip this recorded vote if no legislator info
                 continue # Skip to next recorded-vote

            # Get vote cast from the <vote> child element
            vote_cast_raw = rec.xpath('string(./vote)').strip() or "Not Voting" # Relative path


            # --- Party Mapping (Refined) ---
            party: Optional[Party] = None
            # Convert party_raw to expected Party literal if possible
            if party_raw == 'D':
                party = 'D'
            elif party_raw == 'R':
                party = 'R'
            elif party_raw in ['I', 'ID']: # Include 'ID' for consistency if needed
                 party = 'ID'
            # Keep party as None if it doesn't match expected literals

            # --- Vote Cast Mapping (Refined) ---
            vote_cast: VoteCast = "Not Voting"
            # Map raw vote string to VoteCast literal
            if vote_cast_raw in ["Yea", "Aye"]: # House uses "Aye"
                vote_cast = "Yea"
            elif vote_cast_raw in ["Nay", "No"]: # House uses "No"
                vote_cast = "Nay"
            elif vote_cast_raw == "Present":
                vote_cast = "Present"
            # Anything else (like "Not Voting") maps to "Not Voting"

            if not bioguide_id and name and "VACANT" not in name.upper():
                logger.debug(f"Missing bioguide_id for legislator '{name}'")

            try:
                pos = VotePosition(
                    bioguide_id=bioguide_id if bioguide_id else None, # Ensure None if empty string
                    name=name if name else None, # Ensure None if empty string
                    party=party,
                    state=state_raw if state_raw else None, # Ensure None if empty string
                    vote_cast=vote_cast,
                )
                positions.append(pos)
                # Aggregate party tally using the validated Party and VoteCast
                if pos.party and pos.vote_cast:
                    party_tally_agg[pos.party][pos.vote_cast] += 1
            except ValidationError as e:
                # Log details of the item that failed validation
                logger.warning(f"Skipping invalid VotePosition (House): {e} - Data: bio={bioguide_id}, name={name}, party='{party_raw}', state='{state_raw}', vote='{vote_cast_raw}'")
    else:
        logger.warning(f"Could not find <recorded-vote> elements using XPath './recorded-vote' under <vote-data> for House vote {year}-{roll} in {url}. Check XML structure.")


    # Convert defaultdict to standard dict for the Pydantic model
    final_party_tally = {p: dict(counts) for p, counts in party_tally_agg.items()}

    # Create the final RollCallVote model
    try:
        model = RollCallVote(
            chamber="House",
            congress=congress,
            session=session, # session should now be an integer or None from _safe_int
            roll_number=roll_num, # Use extracted roll number
            vote_date=vote_date,
            vote_question=vote_question,
            vote_result=vote_result,
            bill_number=bill_number,
            tally=tally,
            positions=positions,
            party_tally=final_party_tally # Pass the calculated party tally
        )
        logger.info(f"Successfully parsed House vote details for {year}-{roll} ({url}) with {len(positions)} positions.")
        return model
    except ValidationError as e:
        logger.error(f"Failed to validate RollCallVote model for House vote {year}-{roll} ({url}): {e}", exc_info=True)
        # Re-raise with a more informative message
        raise ValueError(f"Failed to validate parsed vote data for House vote {year}-{roll}: {e}") from e
    except Exception as e:
        # Catch any other errors during model creation or final processing
        logger.error(f"An unexpected error occurred during processing House vote {year}-{roll} ({url}): {e}", exc_info=True)
        # Re-raise as a runtime error
        raise RuntimeError(f"An unexpected error occurred processing House vote {year}-{roll}") from e