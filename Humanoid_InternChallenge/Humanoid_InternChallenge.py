import sys
import subprocess
import os
import threading
import time

# --- 0. AUTO-INSTALLER (new google-genai package) ---
try:
    from google import genai
    from google.genai import types
except ImportError:
    print(f"Installing google-genai into: {sys.executable}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "google-genai"])
    print("\n--- INSTALLATION SUCCESSFUL! Please run the script again. ---")
    sys.exit(0)

try:
    import tkinter as tk
except ImportError:
    print("tkinter is not available.")
    print("  Ubuntu/Debian: sudo apt install python3-tk")
    print("  macOS:         brew install python-tk")
    sys.exit(1)


# --- 1. THE ENVIRONMENT ---
class SimpleWorld:
    """
    5x5 grid world.

    Layout:
        . . . . .
        . # # . .
        . . K . .   K = key item
        . # . # .
        . . . . G   G = goal

    Agent starts at (0,0), must collect KEY at (2,2), then reach EXIT at (4,4).
    """

    LAYOUT = [
        [".", ".", ".", ".", "."],
        [".", "#", "#", ".", "."],
        [".", ".", "K", ".", "."],
        [".", "#", ".", "#", "."],
        [".", ".", ".", ".", "G"],
    ]
    SIZE = 5

    def __init__(self):
        self.agent_pos = [0, 0]
        self.goal_pos  = [4, 4]
        self.key_pos   = [2, 2]
        self.has_key   = False
        self.key_collected = False
        self.steps = 0
        self.done  = False
        self.history = []

    def get_observation(self) -> str:
        """Structured text observation for the LLM."""
        x, y = self.agent_pos
        parts = [
            f"Position: ({x},{y}).",
            f"Inventory: {'iron key' if self.has_key else 'empty'}.",
        ]

        adj = {
            "North": (x-1, y), "South": (x+1, y),
            "East":  (x, y+1), "West":  (x, y-1),
        }
        surroundings = []
        for name, (nx, ny) in adj.items():
            if not (0 <= nx < self.SIZE and 0 <= ny < self.SIZE):
                surroundings.append(f"{name}: boundary wall")
            else:
                cell = self.LAYOUT[nx][ny]
                if cell == "#":
                    surroundings.append(f"{name}: wall")
                elif [nx, ny] == self.goal_pos:
                    surroundings.append(f"{name}: EXIT (G)")
                elif [nx, ny] == self.key_pos and not self.key_collected:
                    surroundings.append(f"{name}: KEY item on floor")
                else:
                    surroundings.append(f"{name}: open floor")

        parts.append("Surroundings: " + "; ".join(surroundings) + ".")
        if not self.key_collected:
            parts.append("Objective: collect the KEY at (2,2), then go to the EXIT at (4,4).")
        else:
            parts.append("Objective: you have the key — reach the EXIT at (4,4).")

        return " ".join(parts)

    def move(self, direction: str) -> str:
        """
        Move the agent one step.
        direction must be one of: North, South, East, West.
        Returns a string describing what happened.
        """
        direction = direction.strip().capitalize()
        move_map = {
            "North": [-1,  0], "South": [1, 0],
            "East":  [ 0,  1], "West":  [0, -1],
        }
        if direction not in move_map:
            return f"Invalid direction '{direction}'. Use North, South, East, or West."

        x, y = self.agent_pos
        dx, dy = move_map[direction]
        nx, ny = x + dx, y + dy

        if not (0 <= nx < self.SIZE and 0 <= ny < self.SIZE):
            result = f"Blocked: boundary wall to the {direction}."
            self.history.append((list(self.agent_pos), direction, result))
            return result

        if self.LAYOUT[nx][ny] == "#":
            result = f"Blocked: wall to the {direction}."
            self.history.append((list(self.agent_pos), direction, result))
            return result

        self.agent_pos = [nx, ny]
        self.steps += 1

        extra = ""
        if [nx, ny] == self.key_pos and not self.key_collected:
            self.key_collected = True
            self.has_key = True
            extra = " You picked up the KEY!"

        if self.agent_pos == self.goal_pos and self.has_key:
            self.done = True
            result = f"Moved {direction} to ({nx},{ny}).{extra} SUCCESS: reached the exit in {self.steps} steps!"
        elif self.agent_pos == self.goal_pos:
            result = f"Moved {direction} to ({nx},{ny}). At the exit, but you need the KEY first!"
        else:
            result = f"Moved {direction} to ({nx},{ny}).{extra}"

        self.history.append((list(self.agent_pos), direction, result))
        return result


# --- 2. TKINTER MAP WINDOW ---
class MapWindow:
    CELL = 80
    PAD  = 20

    COLORS = {
        "floor":        "#F5F3EE",
        "wall":         "#3A3A38",
        "goal":         "#9FE1CB",
        "key":          "#FAC775",
        "agent":        "#CECBF6",
        "agent_border": "#534AB7",
        "trail":        "#E0DDF5",
        "text":         "#2C2C2A",
        "bg":           "#FAFAF8",
        "status_bg":    "#F0EEE8",
    }

    def __init__(self, world: SimpleWorld):
        self.world = world
        self._trail = set()

        self.root = tk.Tk()
        self.root.title("Dungeon Agent — Live Map")
        self.root.configure(bg=self.COLORS["bg"])
        self.root.resizable(False, False)

        canvas_w = world.SIZE * self.CELL + self.PAD * 2
        canvas_h = world.SIZE * self.CELL + self.PAD * 2

        self.canvas = tk.Canvas(
            self.root, width=canvas_w, height=canvas_h,
            bg=self.COLORS["bg"], highlightthickness=0,
        )
        self.canvas.pack(pady=(12, 0))

        self.status_var = tk.StringVar(value="Initialising agent…")
        tk.Label(
            self.root, textvariable=self.status_var,
            bg=self.COLORS["status_bg"], fg=self.COLORS["text"],
            font=("Courier New", 11), anchor="w", padx=12, pady=6,
        ).pack(fill="x", pady=(6, 0))

        self.action_var = tk.StringVar(value="")
        tk.Label(
            self.root, textvariable=self.action_var,
            bg=self.COLORS["bg"], fg="#534AB7",
            font=("Courier New", 10), anchor="w", padx=12, pady=4,
            wraplength=canvas_w - 24, justify="left",
        ).pack(fill="x", pady=(0, 10))

        self._draw_grid()

    def _cell_xy(self, row, col):
        return self.PAD + col * self.CELL, self.PAD + row * self.CELL

    def _draw_grid(self):
        self.canvas.delete("all")
        w = self.world

        for r in range(w.SIZE):
            for c in range(w.SIZE):
                x, y = self._cell_xy(r, c)
                sym = w.LAYOUT[r][c]

                if sym == "#":
                    fill = self.COLORS["wall"]
                elif [r, c] == w.goal_pos:
                    fill = self.COLORS["goal"]
                elif [r, c] == w.key_pos and not w.key_collected:
                    fill = self.COLORS["key"]
                elif (r, c) in self._trail:
                    fill = self.COLORS["trail"]
                else:
                    fill = self.COLORS["floor"]

                self.canvas.create_rectangle(
                    x, y, x + self.CELL, y + self.CELL,
                    fill=fill, outline="#DDDBD3", width=1,
                )

                if [r, c] == w.goal_pos:
                    self.canvas.create_text(
                        x + self.CELL//2, y + self.CELL//2,
                        text="EXIT", font=("Courier New", 10, "bold"), fill="#0F6E56",
                    )
                elif [r, c] == w.key_pos and not w.key_collected:
                    self.canvas.create_text(
                        x + self.CELL//2, y + self.CELL//2,
                        text="KEY", font=("Courier New", 10, "bold"), fill="#854F0B",
                    )
                elif sym != "#":
                    self.canvas.create_text(
                        x + 6, y + 6, text=f"{r},{c}",
                        font=("Courier New", 7), fill="#BBBBBB", anchor="nw",
                    )

        # Agent circle
        ar, ac = w.agent_pos
        ax, ay = self._cell_xy(ar, ac)
        m = 12
        self.canvas.create_oval(
            ax+m, ay+m, ax+self.CELL-m, ay+self.CELL-m,
            fill=self.COLORS["agent"], outline=self.COLORS["agent_border"], width=2,
        )
        self.canvas.create_text(
            ax + self.CELL//2, ay + self.CELL//2,
            text="◉", font=("Courier New", 18, "bold"), fill=self.COLORS["agent_border"],
        )

    def update(self, status: str, last_action: str = ""):
        """Refresh map + labels. Safe to call from the agent thread."""
        r, c = self.world.agent_pos
        self._trail.add((r, c))
        self._draw_grid()
        self.status_var.set(status)
        self.action_var.set(last_action)
        self.root.update()

    def run_loop(self):
        self.root.mainloop()


# --- 3. TOOL DECLARATION for google-genai SDK ---
MOVE_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="move",
            description="Move the agent one step in the given direction.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "direction": types.Schema(
                        type="STRING",
                        description="One of: North, South, East, West",
                        enum=["North", "South", "East", "West"],
                    )
                },
                required=["direction"],
            ),
        )
    ]
)

SYSTEM_PROMPT = (
    "You are a robot agent navigating a 5x5 dungeon grid. "
    "Your goal is to reach the EXIT at position (4,4). "
    "There is a KEY at (2,2) — you MUST pick it up before the exit accepts you. "
    "Walls (#) block movement — do not try to walk into them. "
    "Call the move tool to act. Think step-by-step and plan an efficient route."
)


# --- 4. AGENT LOOP ---
def run_agent(world: SimpleWorld, map_win: MapWindow):
    api_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_key.txt")

    try:
        with open(api_file_path) as f:
            api_key = f.read().strip()
            print(f"Loaded API key: '{api_key}'.")
        if not api_key:
            print(f"Error: '{api_file_path}' is empty.")
            return
    except FileNotFoundError:
        print(f"Error: '{api_file_path}' not found.")
        print("Create 'api_key.txt' next to this script and paste your Gemini API key inside.")
        return

    client = genai.Client(api_key=api_key)

    # Pick the best available model
    preferred = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
    try:
        available = [m.name for m in client.models.list()]
        model_id  = next((p for p in preferred if any(p in a for a in available)), None)
        if not model_id:
            model_id = available[0] if available else "gemini-2.5-flash"
    except Exception:
        model_id = "gemini-2.5-flash"

    print(f"Using model: {model_id}")

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[MOVE_TOOL],
    )

    # Conversation history
    history: list[types.Content] = []

    print("\n--- Mission Start ---")

    for step in range(20):
        obs = world.get_observation()
        print(f"\nStep {step+1}: {obs}")

        map_win.update(
            status=f"Step {step+1} | pos {tuple(world.agent_pos)} | {'has key' if world.has_key else 'no key'}",
        )

        history.append(types.Content(
            role="user",
            parts=[types.Part(text=f"Observation: {obs}\nWhat is your next move?")],
        ))

        response = client.models.generate_content(
            model=model_id,
            contents=history,
            config=config,
        )

        # Add delay after API call to respect rate limits
        time.sleep(1.0)

        candidate = response.candidates[0].content
        history.append(candidate)

        tool_results = []
        for part in candidate.parts:
            if hasattr(part, "text") and part.text:
                print(f"Agent: {part.text.strip()}")

            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                direction = fc.args.get("direction", "")
                print(f"  → move({direction})")

                result = world.move(direction)
                print(f"  ← {result}")

                map_win.update(
                    status=f"Step {step+1} | pos {tuple(world.agent_pos)} | {'has key' if world.has_key else 'no key'}",
                    last_action=f"→ move {direction}: {result}",
                )

                tool_results.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result},
                        )
                    )
                )

        if tool_results:
            history.append(types.Content(role="user", parts=tool_results))

        time.sleep(0.8)

        if world.done:
            map_win.update(
                status=f"✓ MISSION COMPLETE in {world.steps} steps!",
                last_action="",
            )
            print("\n=== MISSION COMPLETE ===")
            return

    map_win.update(status="✗ Max steps reached — mission failed.", last_action="")
    print("\n=== Max steps reached ===")


# --- 5. ENTRY POINT ---
if __name__ == "__main__":
    world   = SimpleWorld()
    map_win = MapWindow(world)

    agent_thread = threading.Thread(target=run_agent, args=(world, map_win), daemon=True)
    agent_thread.start()

    map_win.run_loop()