# models.py

import datetime as dt
from typing import List, Optional, Dict, Any, Literal, Sequence, TypedDict
from pydantic import BaseModel, Field, AliasChoices
from typing_extensions import Annotated
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# --- Vote Position / Status Enums ---
VoteCast = Literal['Yea', 'Nay', 'Present', 'Not Voting', 'Guilty', 'Not Guilty', 'Paired Yea', 'Paired Nay', 'Announced Yea', 'Announced Nay', 'Announced For', 'Announced Against']
Party = Literal['D', 'R', 'I', 'ID'] # Expand if needed
Chamber = Literal['House', 'Senate']

# --- Agent State ---
class AgentState(TypedDict):
    # Tell LangGraph to append new messages rather than overwrite
    messages: Annotated[List[BaseMessage], add_messages]
    # Add other scratchpad fields here if needed later

# --- Bill Models (Congress.gov API) ---
# ... (BillInfo, BillSummarySet, BillActionSet, etc. remain unchanged) ...
class BillInfo(BaseModel):
    congress: int
    legislation_type: str = Field(validation_alias=AliasChoices("type", "bill_type"))
    number: str
    title: str
    origin_chamber: Optional[Chamber] = Field(alias="originChamber", default=None)
    latest_action: Optional[Dict[str, Any]] = Field(alias="latestAction", default=None)
    update_date: Optional[dt.datetime] = Field(alias="updateDate", default=None)

class BillSummary(BaseModel):
    text: str
    update_date: Optional[dt.datetime] = Field(alias="updateDate", default=None)
    action_date: Optional[dt.date] = Field(alias="actionDate", default=None)
    action_desc: Optional[str] = Field(alias="actionDesc", default=None)

class BillSummarySet(BaseModel):
    summaries: List[BillSummary]

class BillAction(BaseModel):
    action_date: dt.datetime = Field(alias="actionDate")
    text: str
    action_code: Optional[str] = Field(alias="actionCode", default=None)
    recorded_votes: List[Dict[str, Any]] = Field(alias="recordedVotes", default_factory=list)

class BillActionSet(BaseModel):
    actions: List[BillAction]

class Cosponsor(BaseModel):
    bioguide_id: str = Field(alias="bioguideId")
    district: Optional[int] = None
    first_name: str = Field(alias="firstName")
    last_name: str = Field(alias="lastName")
    party: Party
    state: str
    sponsorship_date: dt.date = Field(alias="sponsorshipDate")
    is_original_cosponsor: bool = Field(alias="isOriginalCosponsor")

class CosponsorSet(BaseModel):
    cosponsors: List[Cosponsor]

class TextVersionFormat(BaseModel):
     url: str
     type: str # e.g., 'PDF', 'Text'

class TextVersion(BaseModel):
    type: str
    date: dt.datetime
    formats: List[TextVersionFormat]

class TextVersionSet(BaseModel):
    text_versions: List[TextVersion] = Field(alias="textVersions")

class BillSearchResultItem(BaseModel):
    congress: int
    number: str
    title: str
    legislation_type: str = Field(validation_alias=AliasChoices("type", "bill_type"))
    latest_action: Optional[Dict[str, Any]] = Field(alias="latestAction", default=None)
    update_date: Optional[dt.datetime] = Field(alias="updateDate", default=None)

class BillSearchResults(BaseModel):
    bills: List[BillSearchResultItem]
    pagination: Optional[Dict[str, Any]] = None
    request: Optional[Dict[str, Any]] = None

# --- Vote Models (XML Feeds) ---
class VotePosition(BaseModel):
    bioguide_id: Optional[str] = Field(default=None)
    name: Optional[str] = None
    party: Optional[Party] = None # Party is optional as it might be missing
    state: Optional[str] = None
    vote_cast: VoteCast

class RollCallVote(BaseModel):
    chamber: Chamber
    congress: int
    session: int # Keeping non-optional based on parser default
    roll_number: int
    vote_date: Optional[dt.date] = None
    vote_question: Optional[str] = None
    vote_result: Optional[str] = Field(default=None, description="Result string (e.g., 'Passed', 'Failed', 'Agreed to')")
    bill_number: Optional[str] = None
    tally: Dict[str, int] = Field(default_factory=dict) # Overall tally
    positions: List[VotePosition] = Field(default_factory=list)
    # *** ADDED party_tally FIELD ***
    party_tally: Dict[Party, Dict[VoteCast, int]] = Field(
        default_factory=dict,
        description="Detailed tally broken down by party (e.g., {'D': {'Yea': 100, 'Nay': 5}, 'R': {'Yea': 10, 'Nay': 150}})"
    )
# --- Member Lookup Models (Congress.gov API) ---
class MemberInfo(BaseModel):
    bioguideId: str
    name: str
    depiction: Optional[Dict[str, str]] = None
    partyHistory: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    terms: Optional[Dict[str, Any]] = None
    directOrderName: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    state: Optional[str] = None

class MemberSearchResults(BaseModel):
    members: List[MemberInfo]
    pagination: Optional[Dict[str, Any]] = None
    request: Optional[Dict[str, Any]] = None
    # Add senate specific document fields if required

# --- Orchestrator Model ---
class MemberVoteRecord(BaseModel):
    chamber: Chamber
    congress: int
    session: int # Should session be Optional here? Vote object has it non-optional. Make consistent.
    roll_number: int
    vote_date: Optional[dt.date] = None
    vote_question: Optional[str] = None
    bill_number: Optional[str] = None
    member_position: VotePosition # Embed the specific member's vote details