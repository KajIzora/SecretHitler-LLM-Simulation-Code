# LLM Social Group Dynamics – Secret Hitler Simulation

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

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
3. Set your OpenAI API key as an environment variable:

   ```bash
   export OPENAI_API_KEY="your_api_key_here"
   ```
4. Run the simulation:

   ```bash
   python secret_hitler.py
   ```

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
* On average, a full game uses about 1.5 million tokens. The cost of a full game with gpt-4o is about $4.00. The cost for gpt-4o-mini is about $0.25. 

---

## Citation

If you use this code, please cite the paper:

Kaj Hansteen Izora & Christof Teuscher (2025).
*Exploring the Potential of Large Language Models (LLMs) to Simulate Social Group Dynamics: A Case Study Using the Board Game "Secret Hitler"*.
*Northeast Journal of Complex Systems*.
[https://orb.binghamton.edu/nejcs/vol7/iss2/5/](https://orb.binghamton.edu/nejcs/vol7/iss2/5/)
