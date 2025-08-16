# LLM Social Group Dynamics – Secret Hitler Simulation

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

Tested with Python 3.11.8

This repository contains the research code accompanying the paper:
**[Exploring the Potential of Large Language Models (LLMs) to Simulate Social Group Dynamics: A Case Study Using the Board Game *Secret Hitler*](https://orb.binghamton.edu/nejcs/vol7/iss2/5/)**, published in the *Northeast Journal of Complex Systems* (2025).

The project implements an **agent-based simulation of the board game *Secret Hitler***, where each player is controlled by a Large Language Model. The simulation was used to investigate whether LLM-powered agents can exhibit:

* **Theory of mind** (modeling other players’ beliefs)
* **Strategic adaptation** over repeated rounds
* **Deception, trust cycles, and coordination** in a social deduction setting

These experiments form the basis of the published paper.

---

## How to Run

1. Clone this repository.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```
3. Set your OpenAI API key. You can do this in one of two ways:

   * Edit the `.env` file in the project and add your key:
   * 
     ```
     OPENAI_API_KEY=your_api_key_here
     ```

     This will be loaded automatically when you run the script.
   * Or set it directly in your terminal:

     ```bash
     export OPENAI_API_KEY="your_api_key_here"
     ```
4. Run the simulation with default settings:

   ```bash
   python secret_hitler.py
   ```

   This runs the simulations with the settings:

   ```bash
   python secret_hitler.py \
  --model gpt-4o \
  --games 1 \
  --logdir logs \
  --player_type 1 \
  --run_number 1
   ```

   
6. (Optional) Customize the run with arguments. For example:

   ```bash
   python secret_hitler.py \
     --model gpt-4o-mini \
     --games 2 \
     --player_type 2 \
     --run_number 3 \
     --logdir logs
   ```

   * `--model` → choose between `gpt-4o` and `gpt-4o-mini`
   * `--games` → number of games to run in parallel.
   * `--player_type` → agent setup: 1 = default, 2 = personalities, 3 = relationships
   * `--run_number` → gives each run a unique ID to avoid overwriting logs
   * `--logdir` → directory where logs are saved. By defult logs are saved in the current directory.
  
7. To see all available options, run:

   ```bash
   python secret_hitler.py --help
   ```

---

✅ This makes it crystal clear: default run, `.env` setup, and optional arguments.

Do you also want me to add a **“Quick Test Run”** example (super cheap, one game on `gpt-4o-mini`) so reviewers can try it without worrying about costs?


---

## What Happens

Running the script simulates a full game of *Secret Hitler* with LLM-controlled agents. The program will:

* Assign each agent a role (Liberal, Fascist, or Hitler)
* Orchestrate turns, voting, and policy plays
* Capture each agent’s reasoning and dialogue during the game
* Log game outcomes and strategic decisions

---

## Game Logs

Each run produces a **game log** containing:


* Tokens used in run 
* Agent prompts and responses (showing reasoning at each decision point)
* Voting and policy outcomes
* Trust Scores
* Final game result (winning side and reasoning trace)

These logs provide the qualitative and quantitative data analyzed in the paper.

---

## Notes

* This is **research code** supporting the above publication, not production-ready software.
* Results will vary slightly between runs due to stochasticity in LLM outputs.
* See the paper for a full discussion of methodology and findings.
* Cost Scaling: Each agent query re-sends the full game history, so token usage grows with game length.
   * On average, a full game uses about 1.5 million tokens. The cost of a full game with gpt-4o is about $4.00. The cost for gpt-4o-mini is about $0.25.
   *  In the early game, context is short and grows each turn, so cost scales quadratically with the number of turns (each step adds the entire accumulated history).
   *  Once the history reaches the model’s context limit (e.g. 128k tokens for GPT-4o), each query sends ~128k tokens regardless of turn. At this point, cost scales linearly with the number of turns, but at a higher constant rate.
   *  In practice: short games are inexpensive, but very long games with many rounds will approach a “flat per-turn” cost at the maximum context size.

---

## Citation

If you use this code, please cite the paper:

Kaj Hansteen Izora & Christof Teuscher (2025).
*Exploring the Potential of Large Language Models (LLMs) to Simulate Social Group Dynamics: A Case Study Using the Board Game "Secret Hitler"*.
*Northeast Journal of Complex Systems*.
[https://orb.binghamton.edu/nejcs/vol7/iss2/5/](https://orb.binghamton.edu/nejcs/vol7/iss2/5/)
