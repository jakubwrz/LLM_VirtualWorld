# Humanoid Intern Challenge: LLM-Powered Embodied AI

![LLM Embodied Agent](https://img.shields.io/badge/AI-Gemini%20Powered-blue?style=for-the-badge&logo=google) ![Python 3.x](https://img.shields.io/badge/Python-3.x-blue?style=for-the-badge&logo=python) ![Tkinter GUI](https://img.shields.io/badge/GUI-Tkinter-orange?style=for-the-badge)

A reinforcement learning simulation where an LLM-powered agent explores a grid-world, builds persistent spatial memory, and learns to avoid hazards to reach a goal.

## Core Features
* **LLM Reasoning Loop**: Powered by the Gemini API, the agent processes egocentric sensor data and inventory status to output a Chain of Thought before acting.
* **Persistent Memory**: Evaluates and logs map elements (walls, hazards, items) and the outcome of past moves into `memory.json`.
* **Sensor Overlays**: Augments the agent's real-time simulated sensors with its historical memory to prevent repeated mistakes without relying on Q-Tables.
* **Dynamic Grid-World**: Includes battery consumption mechanics, debris hazards, alarm traps, and an A* pathfinding hunter enemy.
* **Real-time GUI**: Visualizes the agent's position, thoughts, and actions via Tkinter.

## Setup & Execution

**Prerequisites:** Python 3.x, `google-genai` library

1. Clone and enter the repository.
2. Install dependencies: `pip install google-genai`
3. Create `api_key.txt` in the root directory and paste your Gemini API key inside.
4. Run the simulation:
   ```bash
   python Humanoid_InternChallenge.py
   ```

## Runtime Configuration
Upon execution, the terminal prompts for:
* **Episodes**: How many exploration runs the agent gets before termination.
* **Wipe Memory**: Choose whether to delete `memory.json` after the program ends for fresh training.
* **Cooldown**: The delay between steps (useful for reading the LLM's thought process).

## Architecture
- `EmbodiedWorld`: Simulation engine, physics, and state logic.
- `MapWindow`: Tkinter UI rendering.
- `run_episodes`: Asynchronous event loop connecting the LLM to the environment.
