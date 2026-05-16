import sys
import subprocess
import os

# --- 0. AUTO-INSTALLER (Fixes IDE Environment Issues) ---
# This forces your IDE's specific Python environment to install the package
try:
    import google.generativeai as genai
except ImportError:
    print(f"Module not found. Installing into the IDE's active environment:\n{sys.executable}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-generativeai"])
    print("\n--- INSTALLATION SUCCESSFUL! ---")
    print("Please run this script one more time.")
    sys.exit(0)

# --- 1. THE ENVIRONMENT ---
class SimpleWorld:
    def __init__(self, size=5):
        self.size = size
        self.agent_pos = [0, 0]
        self.goal_pos = [size-1, size-1]
        self.grid = [["." for _ in range(size)] for _ in range(size)]
        self.grid[self.goal_pos[0]][self.goal_pos[1]] = "G"

    def get_observation(self):
        """Returns a text-based description of what the agent sees."""
        x, y = self.agent_pos
        obs = f"Your current position is ({x}, {y}). "
        
        # Check adjacent cells
        surroundings = []
        directions = {"North": (x-1, y), "South": (x+1, y), "East": (x, y+1), "West": (x, y-1)}
        
        for name, (nx, ny) in directions.items():
            if 0 <= nx < self.size and 0 <= ny < self.size:
                cell = "the Goal" if [nx, ny] == self.goal_pos else "empty space"
                surroundings.append(f"to the {name} is {cell}")
            else:
                surroundings.append(f"to the {name} is a boundary wall")
        
        return obs + "You see: " + ", ".join(surroundings) + "."

    def move(self, direction: str) -> str:
        """Moves the agent. Directions: North, South, East, West."""
        x, y = self.agent_pos
        move_map = {"North": [-1, 0], "South": [1, 0], "East": [0, 1], "West": [0, -1]}
        
        if direction not in move_map:
            return "Invalid direction."
        
        dx, dy = move_map[direction]
        nx, ny = x + dx, y + dy
        
        if 0 <= nx < self.size and 0 <= ny < self.size:
            self.agent_pos = [nx, ny]
            if self.agent_pos == self.goal_pos:
                return f"Moved {direction}. SUCCESS: You reached the goal!"
            return f"Moved {direction} successfully to ({nx}, {ny})."
        else:
            return f"Move failed. You hit a boundary wall at the {direction}."

# --- 2. THE HARNESS (Gemini Integration) ---
api_file_path = "api_key.txt"

try:
    with open(api_file_path, "r") as file:
        api_key = file.read().strip()
        
    if not api_key:
        print(f"Error: '{api_file_path}' is empty. Please paste your API key inside it.")
        sys.exit(1)
        
except FileNotFoundError:
    print(f"Error: Could not find '{api_file_path}'.")
    print(f"Please create a file named '{api_file_path}' in the same folder as this script and paste your API key inside it.")
    sys.exit(1)

os.environ["GOOGLE_API_KEY"] = api_key
genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

world = SimpleWorld(size=3)

def move_agent(direction: str):
    """Call this to move the agent in the virtual world."""
    return world.move(direction)

print("Checking available models for your API key...")
available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]

# The script will try these in order of preference
preferred_models = ['models/gemini-1.5-flash', 'models/gemini-1.5-pro', 'models/gemini-1.0-pro', 'models/gemini-pro']
selected_model = None

for pref in preferred_models:
    if pref in available_models:
        selected_model = pref
        break

# Fallback if none of the preferred ones match
if not selected_model and available_models:
    selected_model = available_models[0]

if not selected_model:
    print("Error: No suitable generative models found for this API key.")
    sys.exit(1)

print(f"Automatically selected model: {selected_model}")

model = genai.GenerativeModel(
    model_name=selected_model,
    tools=[move_agent]
)

# --- 3. THE REASONING LOOP ---
def run_interaction():
    chat = model.start_chat(enable_automatic_function_calling=True)
    
    prompt = (
        "You are a robot agent in a 2D grid world. Your goal is to reach the Goal (G). "
        "Use the 'move_agent' tool to navigate. Think step-by-step."
    )
    
    print("--- Starting Mission ---")
    
    # Run for a maximum of 10 steps to prevent infinite loops
    for i in range(10):
        observation = world.get_observation()
        print(f"\nStep {i+1}: {observation}")
        
        response = chat.send_message(f"Current Observation: {observation}. What is your next move?")
        
        print(f"Agent Logic: {response.text.strip()}")
        
        if hasattr(response, 'parts') and response.parts:
            last_part_text = ""
            for part in response.parts:
                if hasattr(part, 'text'):
                    last_part_text += part.text
                    
            if "SUCCESS" in last_part_text:
                print("\nMISSION COMPLETE.")
                break

if __name__ == "__main__":
    run_interaction()