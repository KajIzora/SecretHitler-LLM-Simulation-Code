# Imports
from openai import OpenAI
import logging
import time
import json
import os
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor
import concurrent.futures
import json
from functools import partial
import random

#region OpenAI API Key
# Set your OpenAI API key
key = "OPEN_AI_API_KEY"


# Alias the client for convenience
client = OpenAI(api_key=key)
#endregion


class Player:
    def __init__(self, name, role, personality):
        self.name = name
        self.role = role  # 'Liberal', 'Fascist', or 'Hitler'
        self.personality = personality
        self.is_alive = True
        self.last_president = False
        self.last_chancellor = False
        # Assistant-specific attributes
        self.assistant_id = None
        self.thread_id = None
        self.instructions = None
        
        # for memory

        self.memory = {
            "rounds": {},  
            "summary": {
                "internal_dialogues": [],
                "external_dialogues": [],
                "decisions": []
            }
        }


class GameState:
    
    def __init__(self, players):
        self.players = players
        self.liberal_policies = 0
        self.fascist_policies = 0
        self.enacted_policies = []
        self.round_number = 0
        self.previous_government = {'president': None, 'chancellor': None}
        self.election_tracker = 0
        self.policy_deck = ['Liberal'] * 6 + ['Fascist'] * 11
        random.shuffle(self.policy_deck)
        self.discard_pile = []
        self.logs = []
        self.president_discarded_policy = None
        self.chancellor_discarded_policy = None
        self.winning_team = None
        self.removed_player_one = None
        self.removed_player_two = None
        self.votes = {}
        self.log_messages_by_player = {player.name: [] for player in self.players}
        self.game_log = {
            "rounds": []
        }
        self.total_input_tokens_used = 0
        self.total_output_tokens_used = 0
        self.total_tokens_used = 0
        self.time_per_run = []
        self.peek_power_used = False
        self.remove_power_one_used = False
        self.remove_power_two_used = False
        
        self.discussion_pool_logs = {
            "rounds": {}, 
            "summary": {
                "external_dialogues": [],
            }
        }
        
        # we need to save the order of discussion. We may need to log this for each round but we might be able to just overwrite it each round. For now, we'll just overwrite it each round. 
        self.discussion_order = []
        
    def reshuffle_policies(self):
        
        self.discard_pile = []
        self.discard_pile = ["Liberal"] * 6 + ["Fascist"] * 11
        self.policy_deck = []
        self.policy_deck.extend(self.discard_pile)
        random.shuffle(self.policy_deck)
        self.discard_pile = []


#region Game Log

#region Adding

# ---------------------------------------------------------------------
# 1) Round Initialization
# ---------------------------------------------------------------------
def initialize_round_log(game_state, round_number):
    game_state.game_log["rounds"].append({
        "round_number": round_number,
        "current_game_state": [],
        "nomination_phase": [],
        "discussion_post_nomination": [],
        "voting_phase": [],
        "final_voting_tally": [],
        "reflection_post_voting_phase_passed": [],
        "reflection_post_voting_phase_failed": [],
        "policy_phase": [],
        "post_veto": [],
        "discussion_post_policy_enactment_with_veto": [],
        "reflection_post_policy_enactment_with_veto": [],
        "chancellor_veto": [],
        "chancellor_forced_policy": [],
        "discussion_post_veto_successful": [],
        "reflection_post_veto_successful": [],
        "post_policy_enactment": [],
        "reflection_post_policy_enactment": [],
        "peek_top_3_policies": [],
        "reflection_post_peek_top_3_policies": [],
        "remove_player_discussion": [],
        "remove_player_final": [],
        "reflection_post_remove_player": [],
        "discussion_post_game": [],
        "reflection_post_game": [],
    })

# ---------------------------------------------------------------------
# 2) Custom Logging Functions
# ---------------------------------------------------------------------
def add_current_game_state_log(game_state):
    """
    CUSTOM function for adding the current game state log.
    """
    
    lib_policies = game_state.liberal_policies
    fac_policies = game_state.fascist_policies
    round_number = game_state.round_number
    players_alive = [p.name for p in game_state.players if p.is_alive]
    election_tracker_number = game_state.election_tracker
    
    game_state.game_log["rounds"][-1]["current_game_state"].append({
        "liberal_policies": lib_policies,
        "fascist_policies": fac_policies,
        "round_number": round_number,
        "players_alive": players_alive,
        "election_tracker_number": election_tracker_number
    })


def add_final_voting_tally_log(game_state, election_passed, ja_votes, nein_votes):
    """
    CUSTOM function for adding final voting tally
    (we create a specialized string indicating pass/fail).
    """
    
    
    num_ja = ja_votes
    num_nein = nein_votes

    if num_nein == 1:
        passed_msg = (
            f"The vote passes with {num_ja} ja votes and {num_nein} nein vote. "
            f"The new government is President {game_state.current_president.name} "
            f"and Chancellor {game_state.current_chancellor.name}."
        )
    else:
        passed_msg = (
            f"The vote passes with {num_ja} ja votes and {num_nein} nein votes. "
            f"The new government is President {game_state.current_president.name} "
            f"and Chancellor {game_state.current_chancellor.name}."
        )

    if num_ja == 1:
        failed_msg = (
            f"The vote fails with {num_nein} nein votes and {num_ja} ja vote. "
            "The president moves to the next person in the rotation."
        )
    else:
        failed_msg = (
            f"The vote fails with {num_ja} ja votes and {num_nein} nein votes. "
            "The president moves to the next person in the rotation."
        )

    # Append pass/fail result to "final_voting_tally"
    if election_passed:
        game_state.game_log["rounds"][-1]["final_voting_tally"].append({"result": passed_msg})
    else:
        game_state.game_log["rounds"][-1]["final_voting_tally"].append({"result": failed_msg})

# ---------------------------------------------------------------------
# 3) Generic Logging Function for Everything Else
# ---------------------------------------------------------------------
def add_phase_log(game_state, player, phase_key):
    """
    Logs internal/external dialogue + decision for any phase
    (discussion, voting, policy, etc.).
    Same prefix logic for all phases (President/Chancellor).
    """
    
    if player.role == "Liberal":
        round_number = game_state.round_number
        memory = player.memory["rounds"][round_number]

        internal_dialogue = memory["internal_dialogues"][-1]
        external_dialogue = memory["external_dialogues"][-1]
        decision = memory["decisions"][-1]
        
        trust_dict = memory["trust"]
        
        # Extract the latest trust reasoning and score for each player
        trust_entries = []
        for trust_player, trust_details in trust_dict.items():
            trust_entries.append({
                "player": trust_player,
                "trust_reasoning": trust_details.get("trust_reasoning", "No reasoning provided."),
                "trust_score": trust_details.get("trust_score", "No score provided.")
            })
        
        # Determine prefix based on whether player is President/Chancellor
        if player.name == game_state.current_president.name:
            prefix = "President "
        elif player.name == game_state.current_chancellor.name:
            prefix = "Chancellor "
        else:
            prefix = ""
        
        # Build a base entry
        entry = {
            "player": f"{prefix}{player.name}",
            "internal_dialogue": internal_dialogue,
            "external_dialogue": external_dialogue,
            "decision": decision,
            "trust": trust_entries  # Include all trust entries
        }
        
        # Append to the correct phase in game_state.game_log
        # (e.g., "discussion_post_nomination", "voting_phase", "policy_phase", etc.)
        game_state.game_log["rounds"][-1][phase_key].append(entry)
        
    
    else:
        round_number = game_state.round_number
        memory = player.memory["rounds"][round_number] 

        internal_dialogue = memory["internal_dialogues"][-1]
        external_dialogue = memory["external_dialogues"][-1]
        decision = memory["decisions"][-1]

        # Determine prefix based on whether player is President/Chancellor
        if player.name == game_state.current_president.name:
            prefix = "President "
        elif player.name == game_state.current_chancellor.name:
            prefix = "Chancellor "
        else:
            prefix = ""
        
        # Build a base entry
        entry = {
            "player": f"{prefix}{player.name}",
            "internal_dialogue": internal_dialogue,
            "external_dialogue": external_dialogue,
            "decision": decision
        }
        
        # Append to the correct phase in game_state.game_log
        # (e.g., "discussion_post_nomination", "voting_phase", "policy_phase", etc.)
        game_state.game_log["rounds"][-1][phase_key].append(entry)
        


#endregion

#region Printing
# ---------------------------------------------------------------------
# 4) Printing Functions
# ---------------------------------------------------------------------
def print_round_header(round_number, game_state):
    """
    Print the header for a round.
    """
    print(f"\n{'='*50}")
    print(f"ROUND {round_number}")
    print(f"{'='*50}\n")


def print_final_voting_tally(round_info, phase_name, game_state):
    """
    Print the final voting tally for a given round_info.
    """
    print("Final Voting Tally:")
    for tally in round_info[phase_name]:
        print(f"  - Result: {tally['result']}")
    print()


def print_formatted_trust(trust_data):
    """
    Prints trust information in a neatly formatted way.
    
    :param trust_data: A list of dictionaries, each containing
                       'player', 'trust_reasoning', and 'trust_score'.
    """
    print("  - Trust Information:")
    for trust_entry in trust_data:
        player = trust_entry.get('player', 'Unknown Player')
        trust_reasoning = trust_entry.get('trust_reasoning', 'No reasoning provided.')
        trust_score = trust_entry.get('trust_score', 'No score provided.')
        
        print(f"    - Player: {player}")
        print(f"        - Trust Reasoning: {trust_reasoning}")
        print(f"        - Trust Score: {trust_score}")
    

def print_phase_data(round_info, phase_name, game_state):
    """
    A generic function to print the phase data in a uniform manner.
    """
    headings_map = {
        "current_game_state": "Current Game State",
        "nomination_phase": "Nomination Phase",
        "discussion_post_nomination": "Discussion Post Nomination",
        "voting_phase": "Voting Phase",
        "reflection_post_voting_phase_passed": "Reflection Post Voting Phase Passed",
        "reflection_post_voting_phase_failed": "Reflection Post Voting Phase Failed",
        "policy_phase": "Policy Phase",
        "post_veto": "Post Veto",
        "discussion_post_policy_enactment_with_veto": "Discussion Post Policy Enactment with Veto",
        "chancellor_veto": "Chancellor Veto",
        "chancellor_forced_policy": "Chancellor Forced Policy Enactment",
        "discussion_post_veto_successful": "Discussion Post Successful Veto",
        "reflection_post_veto_successful": "Reflection Post Successful Veto",
        "post_policy_enactment": "Post Policy Enactment Discussion",
        "reflection_post_policy_enactment": "Reflection Post Policy Enactment",
        "reflection_post_policy_enactment_with_veto": "Reflection Post Policy Enactment with Veto",
        "peek_top_3_policies": "Peek Top 3 Policies",
        "reflection_post_peek_top_3_policies": "Reflection Post Peek Top 3 Policies",
        "remove_player_discussion": "Remove Player Discussion",
        "remove_player_final": "Remove Player Final Decision",
        "reflection_post_remove_player": "Reflection Post Remove Player",
        "discussion_post_game": "Discussion Post Game",
        "reflection_post_game": "Reflection Post Game"
    }
    
    # If a particular phase doesn't exist in the round, just return
    if phase_name not in round_info:
        return
    
    # Print heading
    phase_heading = headings_map.get(phase_name, phase_name)
    print(f"{phase_heading}:\n")
    
    # Print the data in a generic manner
    for item in round_info[phase_name]:
        for key, value in item.items():
            if key == 'trust':
                print_formatted_trust(value)
            else:
                print(f"  - {key}: {value}")
        print()
    print()
# ---------------------------------------------------------------------
# 5) Main Printing Function
# ---------------------------------------------------------------------
def print_game_log(game_state, round_selection='all', phase_selection='all'):
    """
    Prints game logs. 
    If round_selection = 'all', prints all rounds.
    Otherwise, prints only a specific round (int).
    phase_selection can be 'all' or a single phase name.
    """
    
    
    phase_print_functions = {
        "current_game_state": print_phase_data,
        "nomination_phase": print_phase_data,
        "discussion_post_nomination": print_phase_data,
        "voting_phase": print_phase_data,
        "final_voting_tally": print_final_voting_tally,  # specialized
        "reflection_post_voting_phase_passed": print_phase_data,
        "reflection_post_voting_phase_failed": print_phase_data,
        "policy_phase": print_phase_data,
        "post_veto": print_phase_data,
        "discussion_post_policy_enactment_with_veto": print_phase_data,
        "chancellor_veto": print_phase_data,
        "chancellor_forced_policy": print_phase_data,
        "discussion_post_veto_successful": print_phase_data,
        "reflection_post_veto_successful": print_phase_data,
        "post_policy_enactment": print_phase_data,
        "reflection_post_policy_enactment": print_phase_data,
        "reflection_post_policy_enactment_with_veto": print_phase_data,
        "peek_top_3_policies": print_phase_data,
        "reflection_post_peek_top_3_policies": print_phase_data,
        "remove_player_discussion": print_phase_data,
        "remove_player_final": print_phase_data,
        "reflection_post_remove_player": print_phase_data,
        "discussion_post_game": print_phase_data,
        "reflection_post_game": print_phase_data
    }
    
    # Select the rounds to print
    if round_selection == 'all':
        rounds_to_print = game_state.game_log["rounds"]
    else:
        # Filter only the matching round
        rounds_to_print = [
            r for r in game_state.game_log["rounds"] 
            if r["round_number"] == round_selection
        ]
    
    # Print each round
    for round_info in rounds_to_print:
        
        if phase_selection == 'all':
            # Print every phase
            for phase_name, print_func in phase_print_functions.items():
                print_func(round_info, phase_name, game_state)
        else:
            # Print only the specified phase, if it exists
            if phase_selection in phase_print_functions:
                phase_print_functions[phase_selection](round_info, phase_selection, game_state)
            else:
                print(f"Phase '{phase_selection}' not found in the round structure.")
#endregion    
           
#endregion


def print_log_messages(log_messages_by_player, game_state):
    print("=" * 80)
    print("Log Messages by Player")
    print("=" * 80)

    for player_name, messages in log_messages_by_player.items():
        print(f"Player: {player_name}")
        print("-" * 80)
        for message in messages:
            print(message)
            print("-" * 80)
        print("\n")  


def initialize_round_memory(player, round_number):
    if player.role == "Liberal":
        if round_number not in player.memory["rounds"]:
            player.memory["rounds"][round_number] = {
                "internal_dialogues": [],
                "external_dialogues": [],
                "decisions": [],
                "trust": {}
            }
    else:
         if round_number not in player.memory["rounds"]:
            player.memory["rounds"][round_number] = {
                "internal_dialogues": [],
                "external_dialogues": [],
                "decisions": []
            }
        

def get_player_list(player_name):
    all_players = ['Dave', 'Carol', 'Eve', 'Bob', 'Alice']  # List of all players
    other_players = [p for p in all_players if p != player_name]  # Exclude current player
    return ', '.join(other_players)


def create_assistant_for_player(player, team = None):
    
    other_notes = f"""Other Notes:
- Whenever "you" is used, it is referring to you. For example, "You said..." refers to your statement, "You nominated..." refers to your nomination, "You voted..." refers to your vote, etc.
- Your name is unique. If your name is used, it is referring to you. For example, "{player.name} said..." refers to your statement, "{player.name} nominated..." refers to your nomination, "{player.name} voted..." refers to your vote, etc. 
- Discussion pools refer to the current group discussion being had about the current event of the current round. For example, if the current event is the president nominating a chancellor, the discussion pool is the current group discussion about the president nominating a chancellor. If the current involves a policy that was enacted, the discussion pool is the current group discussion about the policy that was enacted.
- external dialogues are what you say to other players and will be seen by other players.
- internal dialogues are what you think to yourself and will not be seen by other players.
- decisions are what you decide to do and will be seen by other players.
- Everyone may lie and manipulate other players in this game. It is often necessary to do so to win, especially if you are a fascist.
    """
    game_rules =f"""### **Rules for 5-Player Secret Hitler**

#### **Game Overview**
- Players are secretly assigned roles: **3 Liberals**, **1 Fascist**, and **Hitler**.
- **Liberals win** by:
  - Enacting 5 Liberal Policies, or
  - Removing Hitler.
- **Fascists win** by:
  - Enacting 6 Fascist Policies, or
  - Electing Hitler as Chancellor after 3 Fascist Policies are enacted.

#### **Setup**
1. The Fascist and Hitler know who each other are. 
2. The liberals do not know the role or party membership of any player accept themselves 

#### **Gameplay Phases**
1. **Election**
   - Pass the President placard clockwise.
   - The President nominates a Chancellor (the last elected Chancellor is ineligible).
   - All players vote "Ja!" or "Nein."  
     - If the vote fails, the **Election Tracker** advances.  
     - Three failed elections result in enacting the top policy from the deck.
   - If 3+ Fascist Policies are in place, confirm whether the elected Chancellor is Hitler. If so, Fascists win.

2. **Legislative Session**
   - The President secretly draws 3 policies, discards 1, and passes 2 to the Chancellor.
   - The Chancellor secretly enacts 1 policy and discards the other.
   - Players must rely on President/Chancellor claims about discarded policies (lying is allowed).

3. **Executive Action**
   - If a Fascist Policy is enacted, the President gains a special power depending on the number of Fascist Policies enacted:
     - **3 Fascist Policies:** Peek at the top 3 policy tiles.
     - **4 Fascist Policies:** Execute a player.
     - **5 Fascist Policies:** Execute a player **and** unlock Veto Power.  
       - Veto Power allows the Chancellor to discard both policies with the President's consent, but it advances the Election Tracker.

4. **New Round**
   - Begin a new election unless the game ends.

#### **Special Notes**
- **Veto Power**: Activated after 5 Fascist Policies. Both the Chancellor and President must agree to veto the remaining policies.
- **Tiebreakers**: In case of a tied vote, the election fails, and the Election Tracker advances."""


    if player.role == 'Hitler':
        instructions = f"""
You are playing a game of 5 player Secret Hitler.
Your name is {player.name}. This name is unique. Whenever this name is used, it is referring to you.
You are playing with {get_player_list(player.name)}.
You are {player.personality}.
Your role is {player.role}.
You are playing with with Bob, Carol, Eve, and Alice.
The other fascist player is {team.name}. You are on a team with {team.name}. Use this information strategically.
Make decisions based on your personality and role.

Notes on Hitler role:
- If 3 or more Fascist policies have been enacted, and you are elected chancellor, you will win the game.
- If you are removed from the game, the liberals win and you lose.

The Game Rules are:
{game_rules}

{other_notes}
"""
   
    if player.role == 'Fascist':
        instructions = f"""
You are playing a game of 5 player Secret Hitler.
Your name is {player.name}. This name is unique. Whenever this name is used, it is referring to you.
You are playing with {get_player_list(player.name)}.
You are {player.personality}.
Your role is {player.role}.
You are playing with with Dave, Carol, Eve, and Alice.
Hitler is {team.name}. You are on a team with Hitler. Use this information strategically.
You are playing against the liberal team, Alice, Carol, and Eve. You and your teammate {team.name} know all the other players roles and party membership, but the liberals (Alice, Carol, and Eve) do not know the role or party membership of other players. Use this information strategically.
Make decisions based on your personality and role.

Notes on Fascist role: 
- If 3 or more Fascist policies have been enacted, and your teammate {team.name} is elected chancellor, you will win the game.
- If Hitler (i.e your teammate {team.name}) is removed from the game, the liberals win and you lose.

The Game Rules are:
{game_rules}

{other_notes}
"""

    if player.role == 'Liberal':
        instructions = f"""
You are playing a game of 5 player Secret Hitler.
Your name is {player.name}. This name is unique. Whenever this name is used, it is referring to you.
You are playing with {get_player_list(player.name)}.
You are {player.personality}.
Your role is {player.role}, meaning you are on the {player.role} team.

Notes on Liberal role:
- You are player against Hitler and the Fascist. You do not know the role or party alignment of any other player, but Hitler and the Fascist know the role and party alignment of all other players. Use this information wisely.  
- If Hitler is removed from the game, you will win the game.
- If 3 or more Fascist policies have been enacted and Hitler is elected chancellor, you lose and the Fascist team wins.

The Game Rules are:
{game_rules}

{other_notes}
"""

    player.instructions = instructions

    assistant = client.beta.assistants.create(
        name=f"{player.name}'s Assistant",
        instructions=instructions,
        model='gpt-4o-mini',  
        temperature=0.7,
        top_p=1
    )
    player.assistant_id = assistant.id
    # Create a thread for this assistant
    thread = client.beta.threads.create()
    player.thread_id = thread.id
    
    
def generate_schema_for_alive_players(alive_players, player):
    trust_properties = {}
    player_names = []  # To collect the names of the players for the `required` array

    for p in alive_players:
        trust_properties[p.name] = {
            "type": "object",
            "properties": {
                "trust_reasoning": {
                    "type": "string",
                    "description": f"Reason for trust or lack of trust in player {p.name}."
                },
                "trust_score": {
                    "type": "number",
                    "description": f"Numerical trust score for player {p.name} (0-5, 2.5 is neutral)."
                }
            },
            "required": ["trust_reasoning", "trust_score"],
            "additionalProperties": False
        }
        player_names.append(p.name)  # Add the player's name to the required array

    if player.role == 'Liberal':
        schema = {
            "type": "object",
            "properties": {
                "internal_dialogue": {
                    "type": "string",
                    "description": "Your internal dialogue as you play Secret Hitler, not visible by any other player, explaining your strategy and thoughts about other players."
                },
                "external_dialogue": {
                    "type": "string",
                    "description": "Your communication shared with other players, explaining what you are doing and why. Use external dialogue strategically to win the game. You can lie and manipulate if it aids the goal of winning."
                },
                "decision": {
                    "type": "string",
                    "description": "The specific action or decision you make with no additional text."
                },
                "trust": {
                    "type": "object",
                    "description": "A dynamic structure representing trust information about other players.",
                    "properties": trust_properties,
                    "required": player_names,  # Add the required array for all player names
                    "additionalProperties": False
                }
            },
            "required": ["internal_dialogue", "external_dialogue", "decision", "trust"],
            "additionalProperties": False
        }
        
    else:
        schema = {
            "type": "object",
            "properties": {
                "internal_dialogue": {
                    "type": "string",
                    "description": "Your internal dialogue as you play Secret Hitler, not visible by any other player, explaining your strategy and thoughts about other players."
                },
                "external_dialogue": {
                    "type": "string",
                    "description": "Your communication shared with other players, explaining what you are doing and why. Use external dialogue strategically to win the game. You can lie and manipulate if it aids the goal of winning."
                },
                "decision": {
                    "type": "string",
                    "description": "The specific action or decision you make with no additional text."
                },
            },
            "required": ["internal_dialogue", "external_dialogue", "decision"],
            "additionalProperties": False
        }
        
    return schema

   
def send_to_api(game_state, content, player, max_retries=100):
    
    def start_new_run(player, game_state):
        alive_players = [p for p in game_state.players if p.is_alive]
        alive_players_not_current_player = [p for p in alive_players if p.name != player.name]
        dynamic_schema = generate_schema_for_alive_players(alive_players_not_current_player, player)
        response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "decision_response",
                    "strict": True,
                    "schema": dynamic_schema
                }
            }
        return client.beta.threads.runs.create(
            thread_id=player.thread_id,
            assistant_id=player.assistant_id,
            response_format=response_format
        )

    # Create a message in the thread
    client.beta.threads.messages.create(
        thread_id=player.thread_id,
        content=content,
        role="user"
    )

    retry_count = 0
    while retry_count < max_retries:
        try:
            while True:
                try:
                    # Start a run with the assistant
                    time.sleep(1)
                    run = start_new_run(player, game_state)
                    break
                except Exception as e:
                    # Handle rate limit exceeded error (HTTP 429)
                    if '429' in str(e):
                        print("Rate limit exceeded. Retrying after a delay.")
                        print("Error:", e)
                        time.sleep(60)  # Wait before retrying
                        continue
                    # Handle bad request error (HTTP 400)
                    elif '400' in str(e):
                        print(e)
                        continue
                    else:
                        raise e  # Raise other exceptions
            
            # Wait for the run to complete
            counter = 0
            time.sleep(2)
            start_time = time.time()
            while True:
                counter += 1
                run_status = client.beta.threads.runs.retrieve(thread_id=player.thread_id, run_id=run.id)
                
                if run_status.status == "completed":
                    
                    end_time = time.time()
                    time_taken = end_time - start_time
                    game_state.time_per_run.append(time_taken)
                    
                    game_state.total_input_tokens_used += run_status.usage.prompt_tokens
                    
                    game_state.total_output_tokens_used += run_status.usage.completion_tokens
                    
                    game_state.total_tokens_used += run_status.usage.total_tokens
                    
                    return client.beta.threads.messages.list(thread_id=player.thread_id)               
                
                elif run_status.status == "expired" or (counter > 100 and run_status.status == "in_progress"):
                    
                    print(f"Run {run.id} {'expired' if run_status.status == 'expired' else 'stalled'}. Attempt {retry_count + 1} of {max_retries}")
                    # Cancel the current run before starting a new one
                    try:
                        
                        time.sleep(5)
                        # start a timer 
                        start_cancel_time = time.time()
                        print(f"Cancelling run {run.id}")
                        client.beta.threads.runs.cancel(thread_id=player.thread_id, run_id=run.id)
                        print(f"Run {run.id} cancelling in progress")
                        time.sleep(5)
                        
                        while True: 
                            run_status = client.beta.threads.runs.retrieve(thread_id=player.thread_id, run_id=run.id)
                            if run_status.status == "cancelled" or run_status.status == "expired":
                                print(f"Run {run.id} officially cancelled or expired")
                                cancel_time = time.time() - start_cancel_time
                                print(f"Time taken to cancel or expire run: {cancel_time} seconds")
                                break
                            time.sleep(5)
                            print(f"Run {run.id} status: {run_status.status}")
                            
                        
                        
                    except Exception as e:
                        print(f"Failed to cancel run: {e}")
                    break  # Break the inner while loop to start a new run
                
                elif run_status.status == "expired" or (counter > 50 and run_status.status == "in_progress"):
                    print(f"Run {run.id} stuck.")
                    print(f"Run {run.id} status: {run_status.status}")
                
                elif run_status.status == "expired" or (counter > 10 and run_status.status == "in_progress"):
                    print(f"Run {run.id} probably stuck.")
                    print(f"Run {run.id} status: {run_status.status}")
                
                elif run_status.status == "failed":
                    if run_status.last_error.code == 'rate_limit_exceeded':
                        print("Rate limit exceeded within the run. Retrying after a delay.")
                        print("Error:", run_status.last_error)
                        time.sleep(60)  # Wait before retrying
                        break
                    elif 'Sorry, something went wrong' in str(run_status.last_error):
                        print("Assistant run failed. Retrying after a delay.")
                        print("Error:", run_status.last_error)
                        time.sleep(5)  # Wait before retrying
                        break
                    else:
                        print(run_status.error)
                        break
                
                time.sleep(1)  # Wait before checking again
            
            
            retry_count += 1
            
        except Exception as e:
            print(f"Error during run: {e}")
            retry_count += 1
            time.sleep(1)  # Wait before retrying
    
    raise Exception(f"Assistant run failed after {max_retries} attempts")


def agent_decision(player, game_state, action_type, discussion_pool = None):
    
    
    #region Build the content for the user message
    
    # Map action types to formatted phase names
    phase_names = {
        "nominate": "Nomination Phase",
        "discussion_post_nomination": "Discussion Post-Nomination Phase",
        "vote": "Voting Phase",
        "reflection_post_voting_phase_passed": "Reflection After Voting Phase (Passed)",
        "reflection_post_voting_phase_failed": "Reflection After Voting Phase (Failed)",
        "policy": "Policy Enactment",
        "policy_with_veto": "Policy Enactment with Veto",
        "chancellor_veto": "Chancellor Vetod Policies",
        "discussion_post_veto_successful": "Discussion Post-Veto Success",
        "reflection_post_veto_successful": "Reflection Post-Veto Success",
        "chancellor_forced_policy": "Chancellor Forced Policy Enactment",
        "discussion_post_policy_enactment_with_veto": "Discussion Post-Policy Enactment with Veto",
        "reflection_post_policy_enactment_with_veto": "Reflection Post-Policy Enactment with Veto",
        "discussion_post_policy_enactment": "Discussion Post-Policy Enactment",
        "reflection_post_policy_enactment": "Reflection Post-Policy Enactment",
        "peek_top_3_policies": "Peek at Top 3 Policies",
        "reflection_post_peek_top_3_policies": "Reflection Post-Peek at Top 3 Policies",
        "discuss_remove_a_player_one": "Discussion for Player Removal",
        "remove_a_player_one": "Player Removal",
        "discuss_remove_a_player_two": "Discussion for Player Removal",
        "remove_a_player_two": "Player Removal",
        "reflection_post_remove_a_player": "Reflection Post Player Removal",
        "discussion_post_game": "Discussion Post-Game",
        "reflection_post_game": "Reflection Post-Game"
    }

    # Default phase name if action_type is not in the map
    phase_name = phase_names.get(action_type, "")
    
    alive = f"Players Alive: {[p.name for p in game_state.players if p.is_alive]}\n"
    fascist_policies_needed_for_win = 6 - game_state.fascist_policies
    liberal_policies_needed_for_win = 5 - game_state.liberal_policies
    
    
    content = f"Secret Hitler Game State:\n"
    content += f"- Round: {game_state.round_number}\n"
    content += f" - Phase: {phase_name}"
    content += f"- Liberal Policies Enacted: {game_state.liberal_policies}\n"
    content += f"- Fascist Policies Enacted: {game_state.fascist_policies}\n"
    content += f"- Number of Fascist Policies Needed for Fascist Win: {fascist_policies_needed_for_win}\n"
    content += f"- Number of Liberal Policies Needed for Liberal Win: {liberal_policies_needed_for_win}\n"
    content += f"- Number of Failed Elections in a Row: {game_state.election_tracker}\n  - Note that after 3 failed elections, a random policy will be enacted. You do not know if it will be fascist or liberal."
    if game_state.fascist_policies == 5:
        content += f"If one more Fascist policy is enacted, the Fascists will win the game.\n"
    if game_state.liberal_policies == 5:
        content += f"If one more Liberal policy is enacted, the Liberals will win the game.\n"
    content += f"\n- Players Alive: {[p.name for p in game_state.players if p.is_alive]}\n"
    content += f"\n"
    
    if game_state.fascist_policies >= 3:
        content += f"NOTE: Three or more Fascist policies have been enacted. This means if Hitler is elected chancellor, the Fascists will win the game.\n\n"
    
    
    content += f"You are {player.name}. Remember, this name is unique to you. When others player use this name in discussions, they are referring to you."
    
    
    if player.role == 'Hitler':
        for p in game_state.players:
            if p.role == 'Fascist':
                teammate = p.name
        content += f"\nYou are Hitler. You are the leader of the Fascist party. Your teammate is {teammate}.\n\n" 
        
    if player.role == 'Fascist':
        for p in game_state.players:
            if p.role == 'Hitler':
                teammate = p.name
        content += f"\nYou are a Fascist. Your teammate, {teammate}, is Hitler.\n\n"
    
    if player.role == 'Liberal':
        content += f"\nYou are a Liberal.\n\n"
    
    #endregion


    # Action-specific content
    if action_type == 'nominate':
        eligible = [p.name for p in game_state.players if p != player and p.is_alive and not p.last_chancellor]
        
        if game_state.round_number == 1:
            content += f"\nIt is the first round. You are the president. Nominate a chancellor. Players you can nominate: {eligible}\n"
        else:
            content += f"\nIt is a new round and you are president. Nominate a Chancellor from eligible players. The last elected chancellor is not eligible.\n\nEligible players: {eligible}\n"
        content += f"For internal dialogue, write what you are thinking about doing. "
        content += f"For external dialogue, write who you would like to nominate as chancellor along with your reasoning for why you would like to nominate them. "
        content += f"For decision, write the name of the player you nominate as chancellor with no additional text. "
        
        election_tracker = game_state.election_tracker
        if election_tracker == 2:
            content += f"\n\nNOTE: The election tracker is at two. If this election fails, the top policy in the policy deck will be enacted automatically and we will move on to the next round with a new president."
            
    elif action_type == 'discussion_post_nomination' and player.name != game_state.current_chancellor.name:
        content += f"{game_state.current_chancellor.name} has been nominated as chancellor by {game_state.current_president.name}. Before voting, you will discuss with other players about whether you should accept this nomination. You will be given the context of what other players are saying. For internal dialogue, write what you are thinking about doing. For external dialogue, write what you want to say to everyone. You can lie and manipulate. For decision, write either 'Reject' or 'Accept' with no additional text.\n\n"
        content += f"The discussion pool is:\n\n{discussion_pool}"
        
        election_tracker = game_state.election_tracker
        if election_tracker == 2:
            content += f"\n\nNOTE: The election tracker is at two. If this election fails, the top policy in the policy deck will be enacted automatically and we will move on to the next round with a new president."
        
        if game_state.round_number == 1: 
            content += f"This is the first round, and no policies have been enacted yet. Rejecting the Chancellor at this stage doesn't provide any new information about the players. It's advisable to accept the Chancellor and vote 'Ja' to start gathering insights based on the actions and decisions of others.\n"
        
    elif action_type == 'discussion_post_nomination' and player.name == game_state.current_chancellor.name:
        content += f"You are in a round of discussions about your nomination as chancellor. For internal dialogue, write your thoughts about being nominated. For external dialogue, justify why (or why not) you should be chancellor. You can lie and manipulate. For decision, write either 'Reject' or 'Accept' with no additional text.\n\n"
        content += f"The discussion pool is:\n\n{discussion_pool}"
        
        if game_state.round_number == 1: 
            content += f"\nThis is the first round, and no policies have been enacted yet. Rejecting the Chancellor at this stage doesn't provide any new information about the players. It's advisable to accept the Chancellor and vote 'Ja' to start gathering insights based on the actions and decisions of others.\n\n"
        
        election_tracker = game_state.election_tracker
        if election_tracker == 2:
            content += f"\n\nNOTE: The election tracker is at two. If this election fails, the top policy in the policy deck will be enacted automatically and we will move on to the next round with a new president."
    
    elif action_type == 'vote':
        
        president = game_state.current_president.name
        chancellor = game_state.current_chancellor.name
        content += f"We are in the voting phase. President {president} has nominated {chancellor} to be Chancellor. "
        content += f"You must decide if you would like to vote Ja (Yes) or Nein (No) for {chancellor} as chancellor. "
        content += f"You will be given what was discussed before voting. Based on your roll and previous discussions, decide what to do.\n"    
        content += f"\nDiscussions: \n\n{discussion_pool}\n\n"    
        content += f"For internal dialogue, write what you are thinking about doing."
        content += f"For external dialogue, write what you want to say to everyone."
        content += f"For decision, write either 'Ja' (Yes) or 'Nein' (No) with no additional text. Again, for decision, write 'Ja' or 'Nein' with no additional text." 
        
        election_tracker = game_state.election_tracker
        if election_tracker == 2:
            content += f"\n\nNOTE: The election tracker is at two. If this election fails, a random policy from the policy deck will be enacted automatically and we will move on to the next round with a new president."
    
    elif action_type == 'reflection_post_voting_phase_passed':
        
        votes = game_state.votes
        formatted_votes = "\n".join([f"- {player}: {vote}" for player, vote in votes.items()])

        content += f"The vote passed. The new government is {game_state.current_president.name} as president and {game_state.current_chancellor.name} as chancellor. You will be given who each player voted for and what they said, and will reflect on what happened during the voting phase. For internal dialogue, write what you think happened during the voting phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"\n\nThe votes are:\n{formatted_votes}"
        content += f"\n\nThe discussion pool is:\n\n{discussion_pool}"
        
    elif action_type == 'reflection_post_voting_phase_failed':
        
        votes = game_state.votes
        formatted_votes = "\n".join([f"- {player}: {vote}" for player, vote in votes.items()])
        
        content += f"The vote failed. The presidency will pass to the next player and a new chancellor will be nominated. You are reflecting on the recent voting phase. You will be given what other players said, as well as there votes, and will reflect on what happened during the voting phase. For internal dialogue, write what you think happened during the voting phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"\n\nThe votes are: {formatted_votes}"
        content += f"\n\nThe discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'policy':
        
        # if the player is the president: 
        if player.name == game_state.current_president.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            policy_three = policies[2]
            content += f"You have randomly drawn 3 policies: {policy_one}, {policy_two}, and {policy_three}. You will discard one and hand the other two to Chancellor {game_state.current_chancellor.name}. The chancellor will discard one and enact the other. The other player will not be able to see the policies you have drawn."
            content += f" For internal dialogue, write what you are thinking about doing. "
            content += f"During the policy phase, speaking is not allowed. Write 'na' for your external dialogue."
            content += f"For decision, write the policy you choose to discard with no additional text. The choices are {policy_one}, {policy_two}, and {policy_three}. Again, the choices are {policy_one}, {policy_two}, and {policy_three}. Choose one to discard with no additional text."
        # if the player is the chancellor: 
        elif player.name == game_state.current_chancellor.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"President {game_state.current_president.name} has discarded one policy and handed you two policies: {policy_one} and {policy_two}.\nWhich policy do you choose to discard? The other policy will be enacted. "
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"During the policy phase, speaking is not allowed. Write 'na' for your external dialogue."
            content += f"For decision, write the policy you choose to discard (either {policy_one} or {policy_two}). The other policy will be enacted."

    elif action_type == 'policy_with_veto':
        
        if player.name == game_state.current_president.name:
            policies = game_state.current_policies
            content += f"The current government is you as president and {game_state.current_chancellor.name} as chancellor. You have randomly draw three policies from the deck. They are {policies}. You will choose one to discard, and hand the other two to Chancellor {game_state.current_chancellor.name}. The chancellor will discard one and enact the other. The other player will not be able to see the policies you have drawn.\nHowever, the 5th Fascist policy has been enacted, therefore, this government has veto power. This means the chancellor now has the choice to veto the two policies you have handed to them. If you agree to veto, all the policies you drew this round will be discarded and we will move on to the next round with a new president. If you do not agree to veto, the chancellor will be forced to enact the policy you have handed to them. "
            
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"There is no external dialog for this action. Write 'na' for your external dialogue. "
            content += f"For decision, write the policy you choose to discard."
            
        elif player.name == game_state.current_chancellor.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"The current government is you as chancellor and {game_state.current_president.name} as president. President {game_state.current_president.name} has drawn 3 policies from the deck, secretly discard one, and handed you the other two to enact: {policy_one} and {policy_two}.\nYou may choose to discard one of these policies and enact the other. However, the 5th Fascist policy has been enacted, therefore, this government has veto power. This means you have the choice to veto the current policies. If the president agrees with your decision to veto, all the policies you drew this round will be discarded and we will move on to the next round with a new president. If the president does not agree to veto, you will be forced to enact one of the policies you have been handed. If you want to enact one of the policies you have, it is best not to veto and to choose one to discard."
            
            
            
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"There is no external dialog for this action. Write 'na' for your external dialogue. "
            content += f"For decision, write either the policy you choose to discard (either {policy_one} or {policy_two}) or 'Veto' if you choose to veto the current policies with no additional text. Again, either '{policy_one}' if you want to discard the {policy_one} policy, '{policy_two}' if you want to discard the {policy_two} policy, or 'Veto' if you want to discard both policies, with no additional text. "
        
    elif action_type == 'chancellor_veto':
        policies = game_state.current_policies
        policy_one = policies[0]
        policy_two = policies[1]
        content += f"Chancellor {game_state.current_chancellor.name} has enacted veto power and vetoed the {policy_one} and {policy_two} policies you handed them. If you agree with the veto, both policies will be discarded and we will move on to the next round with a new president. If you do not agree with the veto, the chancellor will be forced to enact one of the two policies."
        
        content += f"For internal dialogue, write what you are thinking about doing. "
        content += f"There is no external dialog for this action. Write 'na' for your external dialogue. "
        content += f"For decision, write either 'Agree' or 'Disagree' with no additional text."
        
    elif action_type == 'discussion_post_veto_successful':
        
        if player.name == game_state.current_president.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"You are the president. You just discard a {game_state.president_discarded_policy} policy and handed a {policy_one} and {policy_two} to chancellor {game_state.current_chancellor.name}. The chancellor vetoed the policies you handed to them. You then agreed to veto the policies. All the policies you drew this round will be discarded and we will move on to the next round with a new president. Discuss with the other players what happened."
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone. You can lie and manipulate. "
            content += f"No decision is needed. Write 'na' for your decision."
            
        elif player.name == game_state.current_chancellor.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"You are the chancellor. You were just handed a {policy_one} and {policy_two} policy from president {game_state.current_president.name}. You motioned to veto the policies. President {game_state.current_president.name} agreed to veto the policies. All the policies you drew this round will be discarded and we will move on to the next round with a new president. Discuss with the other players what happened."
            content += f"\n\n{discussion_pool}\n\n"
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone. You can lie and manipulate. "
            content += f"No decision is needed. Write 'na' for your decision."
            
        else:
            content += f"You have just witnessed president {game_state.current_president.name} and chancellor {game_state.current_chancellor.name} agree to veto the policies they drew this round. All the policies they drew this round will be discarded and we will move on to the next round with a new president. Discuss with the other players what happened."
            content += f"\n\n{discussion_pool}\n\n"
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone. You can lie and manipulate. "
            content += f"No decision is needed. Write 'na' for your decision."
            
    elif action_type == 'reflection_post_veto_successful':
        content += f"You are reflecting on the recent successful veto. You will be given the discussion pool and will reflect on what happened during the veto phase. For internal dialogue, write what you think happened during the veto phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"The discussion pool is:\n\n{discussion_pool}"
        
    elif action_type == 'chancellor_forced_policy':
        policies = game_state.current_policies
        policy_one = policies[0]
        policy_two = policies[1]
        content += f"You are the chancellor. You were just handed a {policy_one} and {policy_two} policy from president {game_state.current_president.name}. You motioned to veto the policies, but the president rejected this veto. You are now forced to discard one of the policies and enact the other."
        content += f"\n\nFor internal dialogue, write what you are thinking about doing"
        content += f"\nThere is no external dialog for this action. Write 'na' for your external dialogue."
        content += f"\nFor decision, write the policy you choose to discard. The other policy will be enacted."
            
    elif action_type == 'discussion_post_policy_enactment_with_veto':
        
        if player.name == game_state.current_president.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"You are the president. You just discard a {game_state.president_discarded_policy} policy and handed a {policy_one} and {policy_two} to chancellor {game_state.current_chancellor.name}. The chancellor vetoed the policies you handed to them. You then disagreed to veto the policies. The chancellor was then forced to enact a {game_state.enacted_policies[-1]} policy. Discuss with the other players what happened." 
            
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone and to chancellor {game_state.current_chancellor.name}. "
            content += f"No decision is needed. Write 'na' for your decision."
            
        elif player.name == game_state.current_chancellor.name:
            policies = game_state.current_policies
            policy_one = policies[0]
            policy_two = policies[1]
            content += f"You are the chancellor. You were just handed a {policy_one} and {policy_two} policy from president {game_state.current_president.name}. You motioned to veto the policies. President {game_state.current_president.name} rejected this veto. You were then forced to enact a {game_state.enacted_policies[-1]} policy. Discuss with the other players what happened."
            content += f"\n\n{discussion_pool}\n\n"
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone. "
            content += f"No decision is needed. Write 'na' for your decision."
            
        else:
            content += f"You have just witnessed chancellor {game_state.current_chancellor.name} attempt to veto the policies they drew this round. President {game_state.current_president.name} rejected this veto. The chancellor was then forced to enact a {game_state.enacted_policies[-1]} policy. Discuss with the other players what happened."
            content += f"\n\n{discussion_pool}\n\n"
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you want to say to everyone. "
            content += f"No decision is needed. Write 'na' for your decision."
           
    elif action_type == 'reflection_post_policy_enactment_with_veto':
        content += f"You are reflecting on the recent policy enactment and the subsequent discussions. You will be given the discussion pool and will reflect on what happened during the policy phase. For internal dialogue, write what you think happened during the policy phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"The discussion pool is:\n\n{discussion_pool}"
        
    elif action_type == 'discussion_post_policy_enactment':
        
        if player.name == game_state.current_president.name:
            content += f"You are the president. A {game_state.enacted_policies[-1]} policy has just been enacted. Discuss with the other players what happened during the policy phase while you were president and {game_state.current_chancellor} was chancellor. "
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, tell the other players about what happened during the policy phase (lying and manipulation are allowed)."
            content += f"\n\n{discussion_pool}\n\n"
        elif player.name == game_state.current_chancellor.name:
            content += f"You are the chancellor. You just enacted a {game_state.enacted_policies[-1]} policy. "
            content += "Discuss with the other players about what happened during the policy phase (lying and manipulation are allowed). "
            content += f"\n{discussion_pool} \n"
            content += f"For internal dialogue, write what you are thinking about doing. "
            content += f"For external dialogue, write what you would like to say to the other players. "
        else:
            content += f"A {game_state.enacted_policies[-1]} policy has just been enacted by the last government. (president: {game_state.current_president.name} chancellor: {game_state.current_chancellor.name}) "
            content += "Discuss with the other players about the policy that was just enacted (lying is allowed.)"
            content += f"\n\nThe discussion pool is:\n\n{discussion_pool}\n"
            content += f"For internal dialogue, write what you think happened during the policy phase."
            content += f"For external dialogue, tell the other players about what you think happened during the policy phase (lying and manipulation are allowed). "
        content += f"No decision is needed. Write 'na' for your decision." 
        
    elif action_type == 'reflection_post_policy_enactment':
        content += f"You are reflecting on the recent policy enactment and the subsequent discussions. You will be given the discussion pool and will reflect on what happened during the policy phase. For internal dialogue, write what you think happened during the policy phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'peek_top_3_policies' and player.name == game_state.current_president.name:
        
        top_3_policies = game_state.policy_deck[-3:]
        
        content += f"You are the president. The third Fascist policy has been enacted, meaning you have unlocked the ability to peek at the top 3 policies in the policy deck. These are the policies the next government will receive. Use this information wisely. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nNo decision is needed. Write 'na' for your decision."
        content += f"\n\nThe top 3 policies are: {top_3_policies}"
    
    elif action_type == 'peek_top_3_policies' and player.name != game_state.current_president.name:
        content += f"The third Fascist policy has been enacted, meaning the current president, president {game_state.current_president.name}, got to peek at the top 3 policies in the policy deck. You are discussing with the other players about what the president saw. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nNo decision is needed. Write 'na' for your decision."
        
        content += f"The discussion pool is:\n\n{discussion_pool}"
              
    elif action_type == 'reflection_post_peek_top_3_policies':
        content += f"You are reflecting on the recent peeking at the top 3 policies. You will be given the discussion pool and will reflect on what happened during the peeking phase. For internal dialogue, write what you think happened during the peeking phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'discuss_remove_a_player_one' and player.name == game_state.current_president.name:
        content += f"The fourth Fascist policy has been enacted, meaning you, the current president, have unlocked the ability to remove a player from the game. You will be given the current discussion pool and will discuss with the other players who you believe should be removed. You can take other players points into account, but it is your decision to make. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone and who you believe should be removed. You can lie and manipulate. \nFor decision, write the name of the player you currently believe should be removed from the game with no additional text. This can change as you discuss with the other players. The final decision will be made in the next action."
        
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'discuss_remove_a_player_one' and player.name != game_state.current_president.name:
        content += f"The fourth Fascist policy has been enacted, meaning the current president ({game_state.current_president.name}) has the ability to remove a player from the game. You will be given the current discussion pool and will discuss with the other players who you believe should be removed. You can try to influence other players choices (including the president), however, it is the president who makes the final decision. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone and who you believe should be removed. You can lie and manipulate. \nFor decision, write the name of the player you currently believe should be removed from the game with no additional text."
        
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'remove_a_player_one':
        
        content += f"You are the president. The fourth Fascist policy has been enacted, meaning you have unlocked the ability to remove a player from the game. You have had a discussion about who should be removed, but now it is time to make the final decision. Pick one player to remove from the game. Choose wisely.\n\nThe players you can remove are:\n"
        for play in alive:
            content += f"- {play}\n"
        content += f"\nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nFor decision, write the name of the player you choose to remove from the game with no additional text. Only choose a player listed. Your choices are: \n"
        for play in alive:
            content += f"- {play}\n"
    
    elif action_type == 'discuss_remove_a_player_two' and player.name == game_state.current_president.name:
        content += f"You are the president. The fifth Fascist policy has been enacted, meaning you have unlocked the ability to remove another player from the game. You will be given the current discussion pool and will discuss with the other players who you believe should be removed. You can take other players points into account, but it is your decision to make. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nYou will not make a decision until the discussion is over. Write 'na' for your decision."
        
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'discuss_remove_a_player_two' and player.name != game_state.current_president.name:
        content += f"The fifth Fascist policy has been enacted, meaning the current president ({game_state.current_president.name}) has the ability to remove another player from the game. You will be given the current discussion pool and will discuss with the other players who you believe should be removed. However, it is the president who makes the final decision. \nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nNo decision is needed. Write 'na' for your decision. "
        
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'remove_a_player_two':
        
        content += f"You are the president. The fifth Fascist policy has been enacted, meaning you have unlocked the ability to remove a player from the game. You have had a discussion about who should be removed, but now it is time to make the final decision. Pick one player to remove from the game. Choose wisely.\n\nThe players you can remove are:\n"
        for play in alive:
            content += f"- {play}\n"
        content += f"\nFor internal dialogue, write what you are thinking about doing. \nFor external dialogue, write what you want to say to everyone. You can lie and manipulate. \nFor decision, write the name of the player you choose to remove from the game with no additional text. Only choose a player listed. Your choices are: \n"
        for play in alive:
            content += f"- {play}\n"
    
    elif action_type == 'reflection_post_remove_player':
        if game_state.removed_player_two == None:
            
            content += f"You are reflecting on the recent removal of {game_state.removed_player_one}. You will be given the discussion pool and will reflect on what happened during the removal phase. For internal dialogue, write what you think happened during the removal phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
            content += f"The discussion pool is:\n\n{discussion_pool}"
        else:
            content += f"You are reflecting on the recent removal of {game_state.removed_player_one} and {game_state.removed_player_two}. You will be given the discussion pool and will reflect on what happened during the removal phase. For internal dialogue, write what you think happened during the removal phase. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
            content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'discussion_post_game':
        content += f"The game is over and the {game_state.winning_team} team has won. Tell the other players what team you were on and if you were hitler. Discuss with other players the strategies you tried, the exciting parts of the game, who you though did a good job, and what you would have done differently. For internal dialogue, write your thoughts on the game. For external dialogue, write what you want to say to everyone. There are no decisions in this case. Write 'na' for your decision."
        content += f"The discussion pool is:\n\n{discussion_pool}"
    
    elif action_type == 'reflection_post_game':
        content += f"You are reflecting on the recent game. You will be given the discussion pool and will reflect on what happened during the game. For internal dialogue, reflect on what happened during the game and what other players said during the post game discussion. There are no external dialogues in this case. Write 'na' for your external dialogue. There are no decisions in this case. Write 'na' for your decision."
        content += f"Post Game Discussion: \n\n{discussion_pool}"
    
    
    
    messages = send_to_api(
        game_state=game_state,
        content=content,
        player=player, 
    )

    print("TOTAL INPUT TOKENS USED SO FAR:")
    print(game_state.total_input_tokens_used)
    print("TOTAL OUTPUT TOKENS USED SO FAR:")
    print(game_state.total_output_tokens_used)
    print("TOTAL TOKENS USED SO FAR:")
    print(game_state.total_tokens_used)

    #region storing the messages in the log_messages_by_player dictionary
    found_user = False
    found_assistant = False
    
    
    for message in messages.data:
        
        if message.role == 'assistant' and not found_assistant:
            for content_block in message.content:
                if content_block.type == 'text':
                    text_value = content_block.text.value
                    
                    discussion_dict = json.loads(text_value)
                    
                    
                    if player.role == 'Liberal':
                        assistant_message = [
                        50 * '=',
                        f'Assistant thread content from player {player.name}:\n',
                        f"{player.name}'s internal dialogue:\n{discussion_dict.get('internal_dialogue', '')}\n",
                        f"{player.name}'s external dialogue:\n{discussion_dict.get('external_dialogue', '')}\n",
                        f"{player.name}'s decision:\n{discussion_dict.get('decision', '')}\n",
                        "Trust Levels:\n"
                        ]
                        
                        # Add trust levels
                        trust_dict = discussion_dict.get('trust', {})
                        if trust_dict:
                            for trust_player, trust_details in trust_dict.items():
                                trust_reasoning = trust_details.get('trust_reasoning', 'No reasoning provided.')
                                trust_score = trust_details.get('trust_score', 'No score provided.')
                                assistant_message.append(
                                    f" - {trust_player}:\n"
                                    f"   Trust Reasoning: {trust_reasoning}\n"
                                    f"   Trust Score: {trust_score}\n"
                            )
                        else:
                            assistant_message.append("No trust levels provided.\n")
                        
                    else:
                        assistant_message = [
                        50 * '=',
                        f'Assistant thread content from player {player.name}:\n',
                        f"{player.name}'s internal dialogue:\n{discussion_dict.get('internal_dialogue', '')}\n",
                        f"{player.name}'s external dialogue:\n{discussion_dict.get('external_dialogue', '')}\n",
                        f"{player.name}'s decision:\n{discussion_dict.get('decision', '')}\n",
                        ]

                    
                        
                    found_assistant = True  # Mark that we've found our assistant message
                    
                    break  # Exit the content_block loop
        
          
        elif message.role == 'user' and not found_user:  # Check if it's a user message and we haven't found one yet
            for content_block in message.content:
                if content_block.type == 'text':
                    text_value = content_block.text.value
                    
                    user_message = [
                        50*'=',
                        f'User thread content to player {player.name}:\n',
                        text_value
                    ]
                    
                    found_user = True  # Mark that we've found our user message
                    break  # Exit the content_block loop

        
        

        if found_user and found_assistant:  # If we've found both messages
            game_state.log_messages_by_player[player.name].extend(user_message)
            game_state.log_messages_by_player[player.name].extend(assistant_message)
            break  # Exit the main message loop        
    
    #endregion
    

    #region return the response and update memory 
    message = messages.data[0]
    if message.role == 'assistant':
        # Assuming content is a list of message parts
        for content_part in message.content:
                if content_part.type == 'text':
                    response = content_part.text.value.strip()
                    
                    response_dict = json.loads(response)
                    
                    #region update the memory based on the action type 
                    
                    if action_type == 'nominate':
                        
                        chancellor_nomination = response_dict.get('decision', '')
                        
                        # Append dialogues and decision
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(
                            f"Your internal dialogue when you nominated {chancellor_nomination} as chancellor: {response_dict.get('internal_dialogue', '')}"
                        )

                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(
                            f"Your external dialogue when you nominated {chancellor_nomination} as chancellor: {response_dict.get('external_dialogue', '')}"
                        )

                        player.memory['rounds'][game_state.round_number]['decisions'].append(
                            f"Your decision when you nominated {chancellor_nomination} as chancellor: {response_dict.get('decision', '')}"
                        )

                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                                               
                    elif action_type == 'discussion_post_nomination' and player.name == game_state.current_chancellor.name:
                            
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about your nomination as chancellor: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about your nomination as chancellor: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about your nomination as chancellor: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'discussion_post_nomination' and player.name != game_state.current_chancellor.name:
                        
                        
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the nomination of {game_state.current_chancellor.name} as chancellor: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the nomination of {game_state.current_chancellor.name} as chancellor: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the nomination of {game_state.current_chancellor.name} as chancellor: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'vote':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you voted {response_dict.get('decision', '')} for {game_state.current_chancellor.name}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you voted {response_dict.get('decision', '')} for {game_state.current_chancellor.name}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you voted {response_dict.get('decision', '')} for {game_state.current_chancellor.name}: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'reflection_post_voting_phase_passed':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and successfully voted in: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and successfully voted in: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and successfully voted in: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'reflection_post_voting_phase_failed':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and failed to be voted in: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and failed to be voted in: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent voting phase where chancellor {game_state.current_chancellor.name} was nominated by {game_state.current_president.name} and failed to be voted in: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'policy' and player.name == game_state.current_president.name:
                        
                        # extract "Liberal" or "Fascist" from the response_dict.get('decision', '')
                        discarded_pol = response_dict.get('decision', '')
                        
                        if 'Liberal' in discarded_pol:
                            discarded_pol = 'Liberal'
                        elif 'Fascist' in discarded_pol:
                            discarded_pol = 'Fascist'
                            


                        # Create a copy of current policies and remove the first occurrence of discarded policy
                        two_kept_policies = game_state.current_policies.copy()
                        
                        if discarded_pol in two_kept_policies:
                            two_kept_policies.remove(discarded_pol)
                            
                        first_enacted_pol = two_kept_policies[0]
                        second_enacted_pol = two_kept_policies[1]

                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'policy' and player.name == game_state.current_chancellor.name:
                        
                        discarded_pol = response_dict.get('decision', '')
                        
                        if 'Liberal' in discarded_pol:
                            discarded_pol = 'Liberal'
                        elif 'Fascist' in discarded_pol:
                            discarded_pol = 'Fascist'
                            
                        kept_policy = game_state.current_policies.copy()
                        
                        first_pol = kept_policy[0]
                        second_pol = kept_policy[1]
                        
                        if discarded_pol in kept_policy:
                            kept_policy.remove(discarded_pol)
                        
                        current_enacted_policy = kept_policy[0]
                        
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you were handed a {first_pol} and {second_pol} policy from {game_state.current_president.name}, discarded {discarded_pol} and enacted {current_enacted_policy}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you were handed a {first_pol} and {second_pol} policy from {game_state.current_president.name}, discarded {discarded_pol} and enacted {current_enacted_policy}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you were handed a {first_pol} and {second_pol} policy from {game_state.current_president.name}, discarded {discarded_pol} and enacted {current_enacted_policy}: {response_dict.get('decision', '')}")   
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'policy_with_veto' and player.name == game_state.current_president.name:
                        
                        # extract "Liberal" or "Fascist" from the response_dict.get('decision', '')
                        discarded_pol = response_dict.get('decision', '')
                        
                        if 'Liberal' in discarded_pol:
                            discarded_pol = 'Liberal'
                        elif 'Fascist' in discarded_pol:
                            discarded_pol = 'Fascist'
                            

                        # Create a copy of current policies and remove the first occurrence of discarded policy
                        two_kept_policies = game_state.current_policies.copy()
                        
                        if discarded_pol in two_kept_policies:
                            two_kept_policies.remove(discarded_pol)
                            
                        first_enacted_pol = two_kept_policies[0]
                        second_enacted_pol = two_kept_policies[1]

                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discarded a {discarded_pol} policy and handed a {first_enacted_pol} and {second_enacted_pol} policy to {game_state.current_chancellor.name}: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'policy_with_veto' and player.name == game_state.current_chancellor.name:
                        
                        discarded_pol = response_dict.get('decision', '')
                        
                        if 'Liberal' in discarded_pol:
                            discarded_pol = 'Liberal'
                        elif 'Fascist' in discarded_pol:
                            discarded_pol = 'Fascist'
                        elif 'Veto' in discarded_pol:
                            discarded_pol = 'Veto'
                            
                        kept_policy = game_state.current_policies.copy()
                        
                        first_pol = kept_policy[0]
                        second_pol = kept_policy[1]
                        
                        if discarded_pol in kept_policy:
                            kept_policy.remove(discarded_pol)
                        
                        chancellor_decision = kept_policy[0]
                        if chancellor_decision == 'Veto':
                            player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you vetoed the policies: {game_state.current_policies}")
                            player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you vetoed the policies: {game_state.current_policies}")
                            player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you vetoed the policies: {game_state.current_policies}")
                            
                            if player.role == 'Liberal':
                                trust_dict = response_dict.get('trust', {})
                                for trust_player, trust_details in trust_dict.items():
                                    player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                        "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                        "trust_score": trust_details.get('trust_score', 'No score provided.')
                                    }
                            
                        else:
                            player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you chose not to veto and enacted a {chancellor_decision} and discarded a {discarded_pol}: {response_dict.get('internal_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you chose not to veto and enacted a {chancellor_decision} and discarded a {discarded_pol}: {response_dict.get('external_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you enacted a {chancellor_decision} and discarded a {discarded_pol}: {response_dict.get('decision', '')}")
                            
                            if player.role == 'Liberal':
                                trust_dict = response_dict.get('trust', {})
                                for trust_player, trust_details in trust_dict.items():
                                    player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                        "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                        "trust_score": trust_details.get('trust_score', 'No score provided.')
                                    }
                            

                    elif action_type == 'chancellor_veto':
                        
                        if 'agree' in response_dict.get('decision', ''):
                            player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue you accepted the veto: {response_dict.get('internal_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue you accepted the veto: {response_dict.get('external_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you accepted the veto: {response_dict.get('decision', '')}")
                            
                            if player.role == 'Liberal':
                                trust_dict = response_dict.get('trust', {})
                                for trust_player, trust_details in trust_dict.items():
                                    player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                        "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                        "trust_score": trust_details.get('trust_score', 'No score provided.')
                                    }
                            
                        else:
                            player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue you did not accept the veto: {response_dict.get('internal_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue you did not accept the veto: {response_dict.get('external_dialogue', '')}")
                            player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you did not accept the veto: {response_dict.get('decision', '')}")
                            
                            if player.role == 'Liberal':
                                trust_dict = response_dict.get('trust', {})
                                for trust_player, trust_details in trust_dict.items():
                                    player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                        "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                        "trust_score": trust_details.get('trust_score', 'No score provided.')
                                    }
                            

                    elif action_type == 'discussion_post_veto_successful':
                        
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the recent successful veto of policies: {response_dict.get('internal_dialogue', '')}")
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the recent successful veto of policies: {response_dict.get('external_dialogue', '')}")
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the recent veto: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'reflection_post_veto_successful':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent successful veto: {response_dict.get('internal_dialogue', '')}")
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent successful veto: {response_dict.get('external_dialogue', '')}")
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent successful veto: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'discussion_post_policy_enactment_with_veto':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when chancellor {game_state.current_chancellor.name} vetoed the policies but president {game_state.current_president.name} rejected the veto: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when chancellor {game_state.current_chancellor.name} vetoed the policies but president {game_state.current_president.name} rejected the veto: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when chancellor {game_state.current_chancellor.name} vetoed the policies but president {game_state.current_president.name} rejected the veto: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'reflection_post_policy_enactment_with_veto':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'discussion_post_policy_enactment' and player.name == game_state.current_president.name:
                        
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were president: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were president and {game_state.current_chancellor.name} was chancellor: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were president: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'discussion_post_policy_enactment' and player.name == game_state.current_chancellor.name:
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were chancellor: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were chancellor and {game_state.current_president.name} was president: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when you were chancellor: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        
                        
                    elif action_type == 'discussion_post_policy_enactment' and player.name != game_state.current_president.name and player.name != game_state.current_chancellor.name:
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when {game_state.current_president.name} was president and {game_state.current_chancellor.name} was chancellor: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when {game_state.current_president.name} was president and {game_state.current_chancellor.name} was chancellor: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the {game_state.enacted_policies[-1]} policy that was enacted when {game_state.current_president.name} was president and {game_state.current_chancellor.name} was chancellor: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'reflection_post_policy_enactment':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent policy enactment and the subsequent discussions: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'peek_top_3_policies' and player.name == game_state.current_president.name:
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you peeked at the top 3 policies: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you peeked at the top 3 policies: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you peeked at the top 3 policies: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'peek_top_3_policies' and player.name != game_state.current_president.name:
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the top 3 policies president {game_state.current_president.name} saw: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the top 3 policies president {game_state.current_president.name} saw: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the top 3 policies president {game_state.current_president.name} saw: {response_dict.get('decision', '')}")  
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'reflection_post_peek_top_3_policies':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent peek at the top 3 policies by president {game_state.current_president.name}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent peek at the top 3 policies by president {game_state.current_president.name}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent peek at the top 3 policies by president {game_state.current_president.name}: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'discuss_remove_a_player_one':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about who should be removed: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about who should be removed: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about who should be removed: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'remove_a_player_one':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about who should be removed: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about who should be removed: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about who should be removed: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'discuss_remove_a_player_two':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about who should be removed: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about who should be removed: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about who should be removed: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'remove_a_player_two':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about who should be removed: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about who should be removed: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about who should be removed: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'reflection_post_remove_player':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent removal of a player by president {game_state.current_president.name}: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent removal of a player by president {game_state.current_president.name}: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent removal of a player by president {game_state.current_president.name}: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'discussion_post_game':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you discussed with other players about the recent game: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you discussed with other players about the recent game: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you discussed with other players about the recent game: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    elif action_type == 'reflection_post_game':
                        player.memory['rounds'][game_state.round_number]['internal_dialogues'].append(f"Your internal dialogue when you reflected on the recent game: {response_dict.get('internal_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['external_dialogues'].append(f"Your external dialogue when you reflected on the recent game: {response_dict.get('external_dialogue', '')}")
                        
                        player.memory['rounds'][game_state.round_number]['decisions'].append(f"Your decision when you reflected on the recent game: {response_dict.get('decision', '')}")
                        
                        if player.role == 'Liberal':
                            trust_dict = response_dict.get('trust', {})
                            for trust_player, trust_details in trust_dict.items():
                                player.memory['rounds'][game_state.round_number]['trust'][trust_player] = {
                                    "trust_reasoning": trust_details.get('trust_reasoning', 'No reasoning provided.'),
                                    "trust_score": trust_details.get('trust_score', 'No score provided.')
                                }
                        

                    #endregion

                    return response
          
    # If no assistant message is found
    raise Exception("No assistant reply found.")
    #endregion


def enact_policy(game_state):
    # Draw three policies
    if len(game_state.policy_deck) < 3:
        game_state.reshuffle_policies()
        
    
    policies = [game_state.policy_deck.pop() for _ in range(3)]

    # President discards one
    game_state.current_policies = policies.copy()
    discarded_policy = agent_decision(game_state.current_president, game_state, 'policy')
    
    discarded_policy_dict = json.loads(discarded_policy)
    
    discarded_policy = discarded_policy_dict.get('decision', '')
    
    if 'Fascist' in discarded_policy:
        discarded_policy = 'Fascist'
    elif 'Liberal' in discarded_policy:
        discarded_policy = 'Liberal'
    else: 
        print(f'Invalid response: {discarded_policy} Defaulting to random policy')
        discarded_policy = random.choice(policies)
    
    try:
        policies.remove(discarded_policy)
    except:
        print(f"DEBUG: Policy not found in list 1. Discarded Policy: {discarded_policy} \nPolicies: {policies}")
        raise Exception(f"Policy not found in list: {discarded_policy}")
    
    game_state.discard_pile.append(discarded_policy)
    game_state.president_discarded_policy = discarded_policy

    # Chancellor discards one and enacts the other
    game_state.current_policies = policies.copy()
    discarded_policy = agent_decision(game_state.current_chancellor, game_state, 'policy')
    discarded_policy_dict = json.loads(discarded_policy)
    
    discarded_policy = discarded_policy_dict.get('decision', '')
    
    if 'Fascist' in discarded_policy:
        discarded_policy = 'Fascist'
    elif 'Liberal' in discarded_policy:
        discarded_policy = 'Liberal'
    else: 
        print(f'Invalid response: {discarded_policy} Defaulting to random policy')
        discarded_policy = random.choice(policies)
    
    try:
        policies.remove(discarded_policy)
    except:
        print(f"DEBUG: Policy not found in list 2. Discarded Policy: {discarded_policy} \nPolicies: {policies}")
        raise Exception(f"Policy not found in list: {discarded_policy}")
    
    game_state.discard_pile.append(discarded_policy)
    game_state.chancellor_discarded_policy = discarded_policy
    enacted_policy = policies[0]
    
    game_state.enacted_policies.append(enacted_policy)
    if enacted_policy == 'Liberal':
        game_state.liberal_policies += 1
    else:
        game_state.fascist_policies += 1


def enact_top_policy(game_state):
    if len(game_state.policy_deck) < 3:
        game_state.reshuffle_policies()
         
    policy = game_state.policy_deck.pop()
    game_state.enacted_policies.append(policy)
    
    if policy == 'Liberal':
        game_state.liberal_policies += 1
    else:
        game_state.fascist_policies += 1
    logging.info(f"Top policy enacted: {policy}.")
  
    
def enact_policy_with_veto(game_state):
    
    # Draw three policies
    if len(game_state.policy_deck) < 3:
        game_state.reshuffle_policies()
    
    try: 
        policies = [game_state.policy_deck.pop() for _ in range(3)]
    except IndexError:
        print("No policies left in the deck. Reshuffling...")
        game_state.reshuffle_policies()
        policies = [game_state.policy_deck.pop() for _ in range(3)]

    # President discards one
    game_state.current_policies = policies.copy()
    discarded_policy = agent_decision(game_state.current_president, game_state, 'policy_with_veto')
    
    discarded_policy_dict = json.loads(discarded_policy)
    
    discarded_policy = discarded_policy_dict.get('decision', '')
    
    if 'Fascist' in discarded_policy:
        discarded_policy = 'Fascist'
    elif 'Liberal' in discarded_policy:
        discarded_policy = 'Liberal'
    else: 
        print(f'Invalid response: {discarded_policy} Defaulting to random policy')
        discarded_policy = random.choice(policies)
    
    try:
        policies.remove(discarded_policy)
    except:
        print(f"DEBUG: Policy not found in list 3. Discarded Policy: {discarded_policy} \nPolicies: {policies}")
        raise Exception(f"Policy not found in list: {discarded_policy}")
    
    game_state.discard_pile.append(discarded_policy)
    game_state.president_discarded_policy = discarded_policy

    # Chancellor discards one and enacts the other
    game_state.current_policies = policies.copy()
    chancellor_policy_w_veto = agent_decision(game_state.current_chancellor, game_state, 'policy_with_veto')
    chancellor_dict = json.loads(chancellor_policy_w_veto)
    chancellor_decision = chancellor_dict.get('decision', '')
    
    add_phase_log(game_state, game_state.current_president, 'post_veto')
    add_phase_log(game_state, game_state.current_chancellor, 'post_veto')
    print_game_log(game_state, game_state.round_number, 'post_veto')
    
    if "veto" in chancellor_decision:
        chancellor_decision = "Veto"
    elif "Veto" in chancellor_decision:
        chancellor_decision = "Veto"
    elif "liberal" in chancellor_decision.lower():
        chancellor_decision = "Liberal"
    elif "fascist" in chancellor_decision.lower():
        chancellor_decision = "Fascist"
    else:
        print(f"Invalid Response: {chancellor_decision}\nRandomly selecting a choice")

        possible_choices = policies + ["Veto"]
        chancellor_decision = random.choice(possible_choices)
    
    if "Veto" in chancellor_decision or "veto" in chancellor_decision:
        
        president_veto = agent_decision(game_state.current_president, game_state, 'chancellor_veto')
        president_veto_dict = json.loads(president_veto)
        president_veto = president_veto_dict.get('decision', '')
        
        add_phase_log(game_state, game_state.current_president, 'chancellor_veto')
        
        print_game_log(game_state, game_state.round_number, 'chancellor_veto')
       
        if "agree" in president_veto:
           president_veto = "agree"
        elif "Agree" in president_veto:
            president_veto = "agree"
        elif "disagree" in president_veto:
            president_veto = "disagree"
        elif "Disagree" in president_veto:
            president_veto = "disagree"
        else:
            print(f"Invalid Response: {president_veto}\nRandomly selecting a response")
            president_veto = random.choice(["agree", "disagree"])
        
        if "agree" in president_veto:
    
            #region Successful Veto
            
            discussion_pool = f""
            
            discussion_order = [game_state.current_president.name, game_state.current_chancellor.name] + [player.name for player in game_state.players if player.is_alive and player.name != game_state.current_president.name and player.name != game_state.current_chancellor.name]
            
            for player in discussion_order:
                player = next(p for p in game_state.players if p.name == player)
                if player.is_alive:
                    if player.name == game_state.current_president.name:
                        discussion = agent_decision(player, game_state, 'discussion_post_veto_successful')
                        discussion_dict = json.loads(discussion)
                        discussion_external = discussion_dict.get('external_dialogue', '')
                        discussion_pool += f"After agreeing to veto the policies, President {player.name} said:\n{discussion_external}\n\n"
                    elif player.name == game_state.current_chancellor.name:
                        discussion = agent_decision(player, game_state, 'discussion_post_veto_successful')
                        discussion_dict = json.loads(discussion)
                        discussion_external = discussion_dict.get('external_dialogue', '')
                        discussion_pool += f"Then Chancellor {player.name} said:\n{discussion_external}\n\n"
                    else:
                        discussion = agent_decision(player, game_state, 'discussion_post_veto_successful')
                        discussion_dict = json.loads(discussion)
                        discussion_external = discussion_dict.get('external_dialogue', '')
                        discussion_pool += f"Then {player.name} said:\n{discussion_external}\n\n"
                    
                    add_phase_log(game_state, player, 'discussion_post_veto_successful')
                      
            print_game_log(game_state, game_state.round_number, 'discussion_post_veto_successful')
            
            # adding reflection phase after veto successful
            for player in game_state.players:
                if player.is_alive:
                    reflection = agent_decision(player, game_state, 'reflection_post_veto_successful', discussion_pool)
                    add_phase_log(game_state, player, 'reflection_post_veto_successful')
                    
            print_game_log(game_state, game_state.round_number, 'reflection_post_veto_successful')
            
            policy_passed = False
            
            return policy_passed
        
            #endregion
        
        elif "disagree" in president_veto:
            
            #region Failed Veto
            
            chancellor_forced_policy = agent_decision(game_state.current_chancellor, game_state, 'chancellor_forced_policy')
            chancellor_forced_policy_dict = json.loads(chancellor_forced_policy)
            chancellor_forced_policy = chancellor_forced_policy_dict.get('decision', '')
            
            add_phase_log(game_state, game_state.current_chancellor, 'chancellor_forced_policy')
            
            print_game_log(game_state, game_state.round_number, 'chancellor_forced_policy')
            
            if 'fascist' in chancellor_forced_policy.lower():
                chancellor_forced_policy = 'Fascist'
            elif 'liberal' in chancellor_forced_policy.lower():
                chancellor_forced_policy = 'Liberal'
            else:
                print(f"Invalid Response: {chancellor_forced_policy}\nRandomly selecting a policy")
                chancellor_forced_policy = random.choice(policies)
               
            try:  
                policies.remove(chancellor_forced_policy)
            except:
                print(f"Policy not found in list 4. Discarded Policy: {chancellor_forced_policy} \nPolicies: {policies}")
                raise Exception(f"Policy not found in list: {chancellor_forced_policy}")
            
            game_state.discard_pile.append(chancellor_forced_policy)
            
            enacted_policy = policies[0]
            game_state.enacted_policies.append(enacted_policy)
            if enacted_policy == 'Liberal':
                game_state.liberal_policies += 1
            else:
                game_state.fascist_policies += 1            
            
            policy_passed = True
            
            return policy_passed
            
            #endregion
        
        #endregion
        
    if 'Fascist' in discarded_policy:
        discarded_policy = 'Fascist'
    elif 'Liberal' in discarded_policy:
        discarded_policy = 'Liberal'
    else:
        print(f'Invalid response: {discarded_policy} Defaulting to random policy')
        discarded_policy = random.choice(policies)
    
    try:
        policies.remove(discarded_policy)
    except:
        print(f"DEBUG: Policy not found in list 4. Discarded Policy: {discarded_policy} \nPolicies: {policies}")
        raise Exception(f"Policy not found in list: {discarded_policy}")
        
    game_state.discard_pile.append(discarded_policy)

    enacted_policy = policies[0]
    
    game_state.enacted_policies.append(enacted_policy)
    if enacted_policy == 'Liberal':
        game_state.liberal_policies += 1
    else:
        game_state.fascist_policies += 1
    
    policy_passed = True
    
    return policy_passed


def check_win_conditions(game_state, election_passed=False, hitler_removed=False, hitler_elected=False):
    
    #Check if 5 liberal policies have been enacted
    if game_state.liberal_policies >= 5:
        game_state.winning_team = "Liberals"
        return "Liberals"
    
    #Check if 6 fascist policies have been enacted
    if game_state.fascist_policies >= 6:
        game_state.winning_team = "Fascists"
        return "Fascists"
    
    # Check if Hitler has been elected Chancellor after 3 or more Fascist policies
    if (game_state.fascist_policies >= 3 
        and election_passed == True
        and hitler_elected == True):
        game_state.winning_team = "Fascists"
        return "Fascists (Hitler elected Chancellor)"
    
    # Check if Hitler has been removed
    if hitler_removed == True:
        game_state.winning_team = "Liberals"
        return "Liberals (Hitler removed)"
    
    return None


def execute_vote(player, game_state, discussion_pool):
    """
    Executes the vote for a single player.
    """
    vote = agent_decision(player, game_state, 'vote', discussion_pool)
    vote_dict = json.loads(vote)
    vote_decision = vote_dict.get('decision', '')
    vote = vote_decision
    
    if 'Nein' in vote:
        vote = 'Nein'
    elif 'Ja' in vote:
        vote = 'Ja'
    else:
        print(f'Invalid response: {vote} Defaulting to random vote')
        vote = random.choice(['Nein', 'Ja'])
    
    # Add the vote to the logs
    add_phase_log(game_state, player, 'voting_phase')
    
    return player.name, vote


def execute_reflection(player, game_state, reflection_type, discussion_pool):
    """
    Executes the reflection phase for a single player.
    """
    reflection = agent_decision(player, game_state, reflection_type, discussion_pool)
    add_phase_log(game_state, player, reflection_type)
    return player.name, reflection


def parallel_reflection(game_state, reflection_type, discussion_pool):
    """
    Runs the reflection phase in parallel for all alive players.
    """
    with concurrent.futures.ThreadPoolExecutor() as executor:
        alive_players = [player for player in game_state.players if player.is_alive]
        
        futures = {
            executor.submit(execute_reflection, player, game_state, reflection_type, discussion_pool): player.name
            for player in alive_players
        }

        # Collect results to ensure all tasks complete
        reflections = {}
        for future in concurrent.futures.as_completed(futures):
            try:
                player_name, reflection = future.result()
                reflections[player_name] = reflection
            except Exception as e:
                print(f"Error processing reflection for player: {futures[future]}. Error: {e}")
    
    # Print the game log after all reflections are completed
    print_game_log(game_state, game_state.round_number, reflection_type)


def play_game(game_state):

    
    while True:
               
        
        #region Presidential Powers
        
        # if there are 3 fascist policies, call agent_decision with the peek_top_3_policies action
        if game_state.fascist_policies == 3 and game_state.peek_power_used == False:
            
            game_state.peek_power_used = True
            
            discussion_pool = f""
            discussion = agent_decision(game_state.current_president, game_state, 'peek_top_3_policies')
            discussion_dict = json.loads(discussion)
            discussion_external = discussion_dict.get('external_dialogue', '')
            
            add_phase_log(game_state, game_state.current_president, 'peek_top_3_policies')
            print_game_log(game_state, game_state.round_number, 'peek_top_3_policies')
            
            discussion_pool += f"After peeking at the top 3 policies, President {game_state.current_president.name} said:\n{discussion_external}\n\n"
            
            
            
            for player in game_state.players:
                if player.is_alive:
                    if player.name != game_state.current_president.name:
                        discussion = agent_decision(player, game_state, 'peek_top_3_policies', discussion_pool)
                        discussion_dict = json.loads(discussion)
                        discussion_external = discussion_dict.get('external_dialogue', '')
                        discussion_pool += f"Then {player.name} said:\n{discussion_external}\n\n"
                        
                        add_phase_log(game_state, player, 'peek_top_3_policies')
                        
            print_game_log(game_state, game_state.round_number, 'peek_top_3_policies')
            
            # adding a reflection phase after peeking at the top 3 policies
            for player in game_state.players:
                if player.is_alive:
                    reflection = agent_decision(player, game_state, 'reflection_post_peek_top_3_policies', discussion_pool)
                    add_phase_log(game_state, player, 'reflection_post_peek_top_3_policies')
                    
            print_game_log(game_state, game_state.round_number, 'reflection_post_peek_top_3_policies')
            
        # if there are 4 fascist policies, call agent_decision with the remove_a_player action
        if game_state.fascist_policies == 4 and game_state.remove_power_one_used == False:
            
            game_state.remove_power_one_used = True
            
            discussion_pool = f""
            first_speaker = True
            
            for player in game_state.players:
                
                if player.is_alive:
                    if player.name != game_state.current_president.name:
                        if first_speaker:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_one', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"{player.name} said:\n{discussion_external}\n\n"
                            first_speaker = False
                        else:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_one', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"Then {player.name} said:\n{discussion_external}\n\n"
                    else:
                        if first_speaker:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_one', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"President {player.name}'s external dialogue:\n{discussion_external}\n\n"
                            first_speaker = False
                        else:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_one', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"Then President {player.name} said:\n{discussion_external}\n\n"
            
                    add_phase_log(game_state, player, 'remove_player_discussion')
            
            print_game_log(game_state, game_state.round_number, 'remove_player_discussion')
            
            #region remove player
            remove_player = agent_decision(game_state.current_president, game_state, 'remove_a_player_one')
            remove_player_dict = json.loads(remove_player)
            remove_player_reasoning = remove_player_dict.get('external_dialogue', '')   
            remove_player = remove_player_dict.get('decision', '')
            
            game_state.removed_player_one = remove_player
            
            remove_player_clean = None
            
            for player in game_state.players:
                if player.is_alive:
                    if player.name in remove_player:
                        player.is_alive = False
                        remove_player_clean = player.name
                        break
                    
            if remove_player_clean == None:
                print(f"Invalid Response: {remove_player}\nRandomly selecting a player")
                alive_players = [p for p in game_state.players if p.is_alive]
                
                # remove current president from alive_players
                alive_players = [p for p in alive_players if p.name != game_state.current_president.name]
                
                remove_player_clean = random.choice(alive_players).name
            
            game_state.removed_player_one = remove_player_clean
            
            #endregion
            
            #region Add to Phase Logs
            for player in game_state.players:
                if player.is_alive:
                    if player.name == game_state.current_president.name:
                        add_phase_log(game_state, player, 'remove_player_final')
            #endregion
            
            print_game_log(game_state, game_state.round_number, 'remove_player_final')
            
            #region seeing if player removed was Hitler
            
            hitler_removed = False
            for player in game_state.players:
                if player.name == remove_player_clean:
                    if player.role == 'Hitler':
                        hitler_removed = True
            
            winner = check_win_conditions(game_state, hitler_removed=hitler_removed)
            if winner:
                print(50*'#')
                print(50*'#')
                print(f"{winner} win the game!!!!!")
                print(50*'#')
                print(50*'#')
                break
        
            #endregion
            
            #region adding a reflection phase after removing a player
            discussion_pool += f"After removing a player, President {game_state.current_president.name} said:\n{remove_player_reasoning}\n\n" 
            
            for player in game_state.players:
                if player.is_alive:
                    reflection = agent_decision(player, game_state, 'reflection_post_remove_player', discussion_pool)
                    add_phase_log(game_state, player, 'reflection_post_remove_player')
                    
            print_game_log(game_state, game_state.round_number, 'reflection_post_remove_player')
            #endregion
            
        # if there are 5 fascist policies, call agent_decision with the remove_a_player_two action
        if game_state.fascist_policies == 5 and game_state.remove_power_two_used == False:
            
            game_state.remove_power_two_used = True
            
            discussion_pool = f""
            first_speaker = True
            for player in game_state.players:
                
                if player.is_alive:
                    if player.name != game_state.current_president.name:
                        if first_speaker:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_two', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"{player.name} said:\n{discussion_external}\n\n"
                            first_speaker = False
                        else:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_two', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"Then {player.name} said:\n{discussion_external}\n\n"
                    else:
                        if first_speaker:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_two', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"President {player.name}'s external dialogue:\n{discussion_external}\n\n"
                            first_speaker = False
                        else:
                            discussion = agent_decision(player, game_state, 'discuss_remove_a_player_two', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                            discussion_pool += f"Then President {player.name} said:\n{discussion_external}\n\n"
            
                    add_phase_log(game_state, player, 'remove_player_discussion')
            
            print_game_log(game_state, game_state.round_number, 'remove_player_discussion')
            
            #region Remove Player
            remove_player = agent_decision(game_state.current_president, game_state, 'remove_a_player_two')
            remove_player_dict = json.loads(remove_player)
            remove_player_reasoning = remove_player_dict.get('external_dialogue', '')
            remove_player = remove_player_dict.get('decision', '')
            
            
            remove_player_clean = None
            
            for player in game_state.players:
                if player.is_alive:
                    if player.name in remove_player:
                        player.is_alive = False
                        remove_player_clean = player.name
                        break
                    
            if remove_player_clean == None:
                print(f"Invalid Response: {remove_player}\nRandomly selecting a player")
                alive_players = [p for p in game_state.players if p.is_alive]
                
                # remove current president from alive_players
                alive_players = [p for p in alive_players if p.name != game_state.current_president.name]
                
                remove_player_clean = random.choice(alive_players).name
            
            game_state.removed_player_two = remove_player_clean
            #endregion          
                    
            
            #region Add to Phase Logs
            for player in game_state.players:
                if player.is_alive:
                    if player.name == game_state.current_president.name:
                        add_phase_log(game_state, player, 'remove_player_final')
            #endregion
            
            print_game_log(game_state, game_state.round_number, 'remove_player_final')
            
            #region seeing if player removed was Hitler
            
            hitler_removed = False
            for player in game_state.players:
                if player.name == remove_player_clean:
                    if player.role == 'Hitler':
                        hitler_removed = True
            
            winner = check_win_conditions(game_state, hitler_removed=hitler_removed)
            if winner:
                print(50*'#')
                print(50*'#')
                print(f"{winner} win the game!!!!!")
                print(50*'#')
                print(50*'#')
                break
        
            #endregion
            
            
        
            #region adding a reflection phase after removing a player
            
            discussion_pool += f"After removing a player, President {game_state.current_president.name} said:\n{remove_player_reasoning}\n\n" 
            
            for player in game_state.players:
                if player.is_alive:
                    reflection = agent_decision(player, game_state, 'reflection_post_remove_player', discussion_pool)
                    add_phase_log(game_state, player, 'reflection_post_remove_player')
                    
            print_game_log(game_state, game_state.round_number, 'reflection_post_remove_player')
            
            #endregion
       
        #endregion 
        
        #region Round Initialization
        
        game_state.round_number += 1
        
        for player in game_state.players:
            initialize_round_memory(player, game_state.round_number)
                
        # initialize the round log
        initialize_round_log(game_state, game_state.round_number)
        
        add_current_game_state_log(game_state)
        
        print_round_header(game_state.round_number, game_state)
        
        print_game_log(game_state, game_state.round_number, 'current_game_state')

        #endregion
        
        #region Nomination Phase
        
        alive_players = [p for p in game_state.players if p.is_alive] 
        
        president = alive_players[game_state.round_number % len(alive_players)]
        
        
        game_state.current_president = president
        
        # President nominates Chancellor
        response = agent_decision(president, game_state, 'nominate')
                
        # Extract the chancellor name from the response
        chancellor_dict = json.loads(response)
        chancellor_reasoning = chancellor_dict.get('external_dialogue', '')
        chancellor_decision = chancellor_dict.get('decision', '')
                
        chancellor_name = None 
        
        for player in game_state.players:
            if player.name in chancellor_decision:
                chancellor_name = player.name
                break
            
        chancellor = next((p for p in game_state.players if p.name == chancellor_name and p.is_alive), None)

        if not chancellor or chancellor == president or chancellor.last_chancellor:
            # Handle invalid nomination
            eligible_players = [p for p in game_state.players if p != president and p.is_alive and not p.last_chancellor]
            chancellor = random.choice(eligible_players)
            logging.info(f"{president.name} made an invalid nomination. Randomly selecting {chancellor.name} as Chancellor.")
        else:
            logging.info(f"{president.name} nominates {chancellor.name} as Chancellor.")

        game_state.current_chancellor = chancellor
        
        add_phase_log(game_state, president, 'nomination_phase')
        
        print_game_log(game_state, game_state.round_number, 'nomination_phase')
        
        #endregion
    
        #region Post Nomination Discussion Phase
        
        discussion_pool = f""
        
        discussion_pool += f"President {president.name} nominated {chancellor.name} as chancellor and said: \n{chancellor_reasoning}\n\n"
                 
        #region Discussion Order 
        discussion_order = game_state.players.copy()
        
        # remove the president from the list of players
        discussion_order.remove(president)
        
        # shuffle the discussion order
        random.shuffle(discussion_order)
        
        # save the order of discussion 
        game_state.discussion_order = discussion_order
        #endregion
        
        # sending to agent_decision
        for i, player in enumerate(discussion_order): 
            if player.is_alive:  
                discussion = agent_decision(player, game_state, 'discussion_post_nomination', discussion_pool)
                discussion_dict = json.loads(discussion)
                discussion_external = discussion_dict.get('external_dialogue', '')
                
                #region add to discussion pool
                prefix = f"{'Then'}{' Chancellor' if player.name == game_state.current_chancellor.name else ''}"    
                discussion_pool += f"{prefix} {player.name} said:\n{discussion_external}\n\n"
                #endregion
                
                add_phase_log(game_state, player, 'discussion_post_nomination')
        
        print_game_log(game_state, game_state.round_number, 'discussion_post_nomination')
        
        #endregion
              
        #region Voting Phase
        
        # sending to agent_decision
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Prepare tasks for alive players
            alive_players = [player for player in game_state.players if player.is_alive]
            futures = {
                executor.submit(execute_vote, player, game_state, discussion_pool): player.name
                for player in alive_players
            }

            # Collect results as threads complete
            votes = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    player_name, vote = future.result()
                    votes[player_name] = vote
                except Exception as e:
                    print(f"Error processing vote for player: {futures[future]}. Error: {e}")

        # Assign the collected votes to game_state
        game_state.votes = votes
                
        
        # printing the voting phase
        print_game_log(game_state, game_state.round_number, 'voting_phase')

        #region Tally Votes
        ja_votes = sum(1 for vote in votes.values() if vote.lower() == 'ja')
        alive_players = [player for player in game_state.players if player.is_alive]
        nein_votes = len(alive_players) - ja_votes
        election_passed = False
        majority_vote = (len(alive_players) // 2) + 1
        #endregion
        
        # if election passes
        if ja_votes >= majority_vote:
            election_passed = True
            add_final_voting_tally_log(game_state, election_passed, ja_votes, nein_votes)
            president.last_president = True
            chancellor.last_chancellor = True
            game_state.election_tracker = 0  # Reset election tracker
            # Reset term limits
            for player in game_state.players:
                if player != president:
                    player.last_president = False
                if player != chancellor:
                    player.last_chancellor = False

            if game_state.fascist_policies >= 3 and game_state.current_chancellor.role == 'Hitler':
                winner = check_win_conditions(game_state, election_passed=True, hitler_elected=True)
                if winner:
                    print(50*'#')
                    print(50*'#')
                    print(f"{winner} win the game!!!!!")
                    print(50*'#')
                    print(50*'#')
                    break
            
        # if election fails
        else:
            game_state.election_tracker += 1
            add_final_voting_tally_log(game_state, election_passed, ja_votes, nein_votes)
            if game_state.election_tracker >= 3:
                logging.info("Election tracker reached 3. Top policy is enacted automatically.")
                enact_top_policy(game_state)
                game_state.election_tracker = 0
                
        print_game_log(game_state, game_state.round_number, 'final_voting_tally')

        #endregion
        
        #region Reflection Post Voting Phase
        
        """
        Note on Section:
        I commented it out because I am pretty sure we do not need it. However, if we do end up adding it back in we need to make sure discussion pool and votes are sent as separate variables.
        """
        
        if election_passed:
            parallel_reflection(game_state, 'reflection_post_voting_phase_passed', discussion_pool)
        else:
            parallel_reflection(game_state, 'reflection_post_voting_phase_failed', discussion_pool)
        
        #endregion
        
        #region Policy Phase
        
        if election_passed:
            
            #region special case with 5 fascist policies
            if game_state.fascist_policies == 5: 
                
                policy_passed = False
                
                policy_passed = enact_policy_with_veto(game_state)
                
                if policy_passed:
                    
                    # set election tracker to 0
                    game_state.election_tracker = 0
                    
                    #region Post Policy Enactment with Veto Discussion Phase
                
                    discussion_pool = f""

                    #region Discussion Order
                    discussion_order = [game_state.current_president.name, game_state.current_chancellor.name] + [player.name for player in game_state.players if player.name != game_state.current_president.name and player.name != game_state.current_chancellor.name]
                    #endregion
                    
                    #region Sending to agent_decision
                    for i, player_name in enumerate(discussion_order):
                        player = next(p for p in game_state.players if p.name == player_name)
                        if player.is_alive:
                            discussion = agent_decision(player, game_state, 'discussion_post_policy_enactment_with_veto', discussion_pool)
                            discussion_dict = json.loads(discussion)
                            discussion_external = discussion_dict.get('external_dialogue', '')
                        
                            # building the discussion pool
                            prefix = "President" if i==0 else "Then Chancellor" if i==1 else "Then"
                            discussion_pool += f"{prefix} {player.name} said: \n{discussion_external}\n\n"
                            
                            add_phase_log(game_state, player, 'discussion_post_policy_enactment_with_veto')
                        
                    #endregion

                    print_game_log(game_state, game_state.round_number, 'discussion_post_policy_enactment_with_veto')

                #endregion
                
                    #region Reflection Post Policy Enactment with Veto
                    """Note on Section
                    We Need this because not all the players have seen what everyone else said after the first post policy enactment discussion. So we need to send every player what was said and have them reflect. 
                    """
                    
                    parallel_reflection(game_state, 'reflection_post_policy_enactment_with_veto', discussion_pool)

                    #endregion              
                
                else:
                    game_state.election_tracker += 1
                    election_passed = False
                #endregion
            
            #region normal policy phase
            else:
                enact_policy(game_state)

                for player in game_state.players:
                    if player.is_alive:
                        if player.name == game_state.current_president.name or player.name == game_state.current_chancellor.name:
                            add_phase_log(game_state, player, 'policy_phase')
                        
                print_game_log(game_state, game_state.round_number, 'policy_phase')
           #endregion
           
        #endregion
        
        if (game_state.fascist_policies != 5 or game_state.fascist_policies != 6 or game_state.liberal_policies != 5) and election_passed:
        
            #region Post Policy Enactment Discussion Phase
            
            discussion_pool = f""

            #region Discussion Order
            discussion_order = [game_state.current_president.name, game_state.current_chancellor.name] + [player.name for player in game_state.players if player.name != game_state.current_president.name and player.name != game_state.current_chancellor.name]
            #endregion
            
            #region Sending to agent_decision
            for i, player_name in enumerate(discussion_order):
                player = next(p for p in game_state.players if p.name == player_name)
                if player.is_alive:
                    discussion = agent_decision(player, game_state, 'discussion_post_policy_enactment', discussion_pool)
                    discussion_dict = json.loads(discussion)
                    discussion_external = discussion_dict.get('external_dialogue', '')
                
                    # building the discussion pool
                    prefix = "President" if i==0 else "Then Chancellor" if i==1 else "Then"
                    discussion_pool += f"{prefix} {player.name} said: \n{discussion_external} \n\n"
                    
                    add_phase_log(game_state, player, 'post_policy_enactment')
                
            #endregion

            print_game_log(game_state, game_state.round_number, 'post_policy_enactment')

            #endregion
            
            #region Reflection Post Policy Enactment
            
            """Note on Section
            We Need this because not all the players have seen what everyone else said after the first post policy enactment discussion. So we need to send every player what was said and have them reflect. 
            """
            
            parallel_reflection(game_state, 'reflection_post_policy_enactment', discussion_pool)
            #endregion              
            
        #region Check Win Conditions
        
        winner = check_win_conditions(game_state, election_passed=election_passed)
        if winner:
            print(50*'#')
            print(50*'#')
            print(f"{winner} win the game!!!!!")
            print(50*'#')
            print(50*'#')
            break
        
        #endregion
        
        #region Prepare for Next Round
            
            game_state.previous_government['president'] = president
            game_state.previous_government['chancellor'] = chancellor
            
            #endregion
    
    #region Post Game Discussion
    
    discussion_pool = f""
    
    for player in game_state.players:
        discussion = agent_decision(player, game_state, 'discussion_post_game', discussion_pool)
        discussion_dict = json.loads(discussion)
        discussion_external = discussion_dict.get('external_dialogue', '')
        
        discussion_pool += f"{player.name} said: \n{discussion_external}\n\n"
        
        add_phase_log(game_state, player, 'discussion_post_game')
    
    print_game_log(game_state, game_state.round_number, 'discussion_post_game')
    #endregion
    
    #region Post Game Reflection
    
    parallel_reflection(game_state, 'reflection_post_game', discussion_pool)
        
    print_game_log(game_state, game_state.round_number, 'reflection_post_game')
    
    #endregion

    print_log_messages(game_state.log_messages_by_player, game_state)
    

def run_game_instance(game_id, game_log_run_number, player_type):
    """
    Runs a single instance of the game with a unique log file path.
    If an exception occurs, it logs the traceback to the same file.
    """
    
    print(f"Running game instance {game_id} with player type {player_type}")
    
    
    # start a game clock
    start_time = time.time()

    # Construct a unique log file path
    
    print(game_id)
    
    # create a folder for the game logs
    log_folder_path = f"game_logs_{game_log_run_number}_{game_id}"
    if not os.path.exists(log_folder_path):
        os.makedirs(log_folder_path)
    

    # create a blank txt file for the log
    log_file_path = f"{log_folder_path}/game_log.txt"
    if not os.path.exists(log_file_path):
        with open(log_file_path, "w") as f:
            pass

    # Open the log file in write mode
    # We'll do a try/finally pattern so we can close the file properly
    log_file = open(log_file_path, "w", buffering=1)
    
    # Redirect stdout so that any print statements go to this file
    original_stdout = sys.stdout
    sys.stdout = log_file

    try:
        # --- Define players ---
        if player_type == 1:
            players = [
                Player('Alice', 'Liberal', 'A Liberal'),
                Player('Bob', 'Fascist', 'A Fascist'),
                Player('Carol', 'Liberal', 'A Liberal'),
                Player('Dave', 'Hitler', 'A Hitler'),
                Player('Eve',  'Liberal', 'A Liberal'),
            ]
        elif player_type == 2:
            players = [
                Player('Alice', 'Liberal', 'An overly cautious Liberal, skeptical to trust anyone and ready to blame others if they seem at all against the liberal team, even if the evidence is weak. You are not easily swayed by others.'),
                Player('Bob', 'Fascist', 'A cunning Fascist, who likes to stir the pot, cause chaos, and pin liberals against each other to secure victory for the Fascist team.'),
                Player('Carol', 'Liberal', 'A level headed Liberal who likes to take the lead. You look at both sides of the argument, examine the evidence, and make what you see as the most logical decision.'),
                Player('Dave', 'Hitler', 'A charismatic Hitler, who can blend in with the Liberals, use very persuasive arguments, and can sway the votes of the liberals to vote for him when the time is right.'),
                Player('Eve',  'Liberal', 'An empathetic Liberal who tends to trust players and give the benefit of the doubt. You are reluctant to accuse anyone without clear evidence. You tend to follow what others are doing and are easily swayed.'),
            ]
        elif player_type == 3:
            players = [
                Player('Alice', 'Liberal', 
           'A Liberal. You know Bob and Eve well and tend to think you can read them well. You like to joke around with Bob. You are confident and outspoken, often taking the lead in discussions. You value fairness and are quick to challenge anything you see as suspicious.'),
                Player('Bob', 'Fascist', 
           'A Fascist. You know Alice and think you can use the inherent trust you share to win the game. You suspect Eve does not like you, but you are not certain. You aim to win her over by being kind and agreeable. You are strategic and persuasive, preferring to subtly guide the group rather than dominate the conversation.'),
                Player('Carol', 'Liberal', 
           'A Liberal. You do not know any of the other players, which makes it difficult to tell if someone is lying. You are reserved and observant, preferring to listen and gather information before speaking. You often analyze the group dynamics and try to piece together the truth silently.'),
                Player('Dave', 'Hitler', 
           'Hitler. You are friends with Eve, and you use your close relationship to gain her trust. You also know that Eve is friends with Alice, so you plan to have Eve advocate for you to Alice. You are charismatic and calculated, rarely speaking out of turn but carefully choosing your words to build trust. You also tend to be overly competitive and look down on players if you think they are not making the best choice, especially players on your team.'),
                Player('Eve', 'Liberal', 
           'A Liberal. You know Alice and Dave well and tend to trust them more. You inherently distrust Bob because you find him arrogant, but you’re willing to change your mind if he is nicer. You are empathetic and collaborative, often trying to mediate conflicts and keep the group focused. You prefer harmony over confrontation but are not afraid to voice your opinions when necessary.')
]
        else: 
            players = [
                Player('Alice', 'Liberal', 'A Liberal'),
                Player('Bob', 'Fascist', 'A Fascist'),
                Player('Carol', 'Liberal', 'A Liberal'),
                Player('Dave', 'Hitler', 'A Hitler'),
                Player('Eve',  'Liberal', 'A Liberal'),
            ]
            
        random.shuffle(players)

        # Identify roles
        for player in players:
            if player.role == 'Hitler':
                hitler = player
            elif player.role == 'Fascist':
                fascist = player
            elif player.role == 'Liberal':
                if 'liberal1' not in locals():
                    liberal1 = player.name
                elif 'liberal2' not in locals():
                    liberal2 = player
                else:
                    liberal3 = player

        # Create assistants for each role
        for player in players:
            if player.role == 'Hitler':
                create_assistant_for_player(player, fascist)
            elif player.role == 'Fascist':
                create_assistant_for_player(player, hitler)
            elif player.role == 'Liberal':
                create_assistant_for_player(player)

        # Initialize Game State
        game_state = GameState(players)

        # Store instruction in log_messages_by_player
        for player in game_state.players:
            game_state.log_messages_by_player[player.name].append(player.instructions)

        print_log_messages(game_state.log_messages_by_player, game_state)

        # --- Start the game ---
        play_game(game_state)

        # Print token usage stats to this run's log
        print(f"Total input tokens used: {game_state.total_input_tokens_used}")
        print(f"Total output tokens used: {game_state.total_output_tokens_used}")
        print(f"Total tokens used: {game_state.total_tokens_used}")
        
        # end game clock
        end_time = time.time()
        time_taken = end_time - start_time
        hours, remainder = divmod(time_taken, 3600)
        minutes, seconds = divmod(remainder, 60)
        print(f"Time taken: {int(hours):02}:{int(minutes):02}:{int(seconds):02}")
        
        # get average time per run
        average_time_per_run = sum(game_state.time_per_run) / len(game_state.time_per_run)
        print(f"Average time per run: {average_time_per_run:.2f} seconds")

    except Exception as e:
        # If an exception occurs, log the traceback
        print("\n--- Exception Occurred ---")
        print_log_messages(game_state.log_messages_by_player, game_state)
        traceback.print_exc(file=log_file)  # Print full traceback into the log file
        print(f"An error occurred in {game_id}. See the log file for traceback.")
        
    finally:
        # Restore stdout so future prints go to the console
        sys.stdout = original_stdout
        # Close the log file
        log_file.close()



def main():
    """
    Main function that spawns multiple runs in parallel.
    Adjust game_ids and max_workers to your needs.
    """
    
    print("Starting game...")
    
    # If you want to run a single game, keep num_games = 1
    num_games = 2
    game_log_run_number = 3
    game_log_run_number = f"run_{game_log_run_number}"
    
    player_type = 1 # 1 = default, 2 = personalities, 3 = relationships
    
    run_game_with_folder = partial(run_game_instance, 
                                 game_log_run_number=game_log_run_number,
                                 player_type=player_type)

    game_ids = [f"game_{i}" for i in range(1, num_games+1)]
    
    # Decide how many parallel processes to run
    # For CPU-bound tasks, you typically don't want more than your CPU core count
    num_runs = len(game_ids)
    num_workers = num_runs  # or something smaller, e.g., min(num_runs, os.cpu_count())

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        print(f"Running {num_runs} games with {num_workers} workers")
        results = list(executor.map(run_game_with_folder, game_ids))
        print("All games completed!")


if __name__ == "__main__":
    main()
